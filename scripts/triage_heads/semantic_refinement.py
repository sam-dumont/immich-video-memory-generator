"""Label-blind DINOv2 semantic refinement for a pinned visual cohort.

This layer may inspect only authenticated preview pixels, frozen DINOv2 token
features, capture-group metadata, and explicit opaque audit-id rejections.  It
must never consume teacher labels, head probabilities, captions, or semantic
search results.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import io
import json
import math
import os
import resource
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .cohorts import (
    CohortAudit,
    CohortSpec,
    ReservationBlocklist,
    TruthReservationLedger,
    audit_cohort,
    cohort_selection_sha256,
)
from .embed import (
    CROP_SIZE,
    DEFAULT_ARTIFACT_DIR,
    EXPECTED_ONNX_SHA256,
    IMAGENET_MEAN,
    IMAGENET_STD,
    MODEL_ID,
    MODEL_REVISION,
    PACK_DIM,
    PATCH_GRID,
    PREPROCESS_VERSION,
    RESIZE_SHORT_SIDE,
    TOKEN_DIM,
    create_onnx_session,
    pool_token_pack,
    verify_onnx_artifact,
)
from .image_diversity import (
    PixelDescriptor,
    confirmed_near_duplicate,
    describe_preview_bytes,
    selected_cluster_metrics,
)
from .memory import acquire_recovery_pipeline_lock
from .preview_store import pin_preview_store

SEMANTIC_LAYOUT_VERSION = "dinov2-s14/l2-cls+l2-global-mean/l2/fp32-v1"
SEMANTIC_DIM = 2 * TOKEN_DIM
MAX_SEMANTIC_PROCESS_BYTES = 8 * 1024**3
PIXEL_MORPHOLOGY_VERSION = "triage-longside256-morphology-v1"
_VISUAL_PLAN_DOMAIN = b"triage-private-visual-plan-v1\0"
_PRIVATE_PREVIEW_ROW_KEYS = frozenset(
    {
        "audit_id",
        "asset_id",
        "source_updated",
        "captured_at",
        "capture_day",
        "moment_key",
        "component_key",
        "image_relpath",
        "preview_sha256",
        "preview_bytes",
        "preview_width",
        "preview_height",
        "preview_format",
    }
)
_MORPHOLOGIES = frozenset(
    {
        "tall_portrait",
        "portrait",
        "square",
        "landscape",
        "panoramic",
        "screen_signature",
    }
)


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def morphology_for_dimensions(width: int, height: int) -> str:
    """Return a label-free shape bucket used only for representation caps."""
    if width < 1 or height < 1:
        raise ValueError("preview dimensions must be positive")
    ratio = width / height
    if ratio < 0.67:
        return "tall_portrait"
    if ratio < 0.90:
        return "portrait"
    if ratio <= 1.10:
        return "square"
    if ratio <= 1.80:
        return "landscape"
    return "panoramic"


@dataclass(frozen=True, slots=True)
class PixelMorphologyMetrics:
    """Task-independent morphology measurements over a fixed-size RGB view."""

    long_side: int
    near_white_fraction: float
    near_black_fraction: float
    edge_fraction: float
    laplacian_variance: float
    mean_saturation: float
    screen_signature: bool
    conservative_quality_failure: bool


def describe_pixel_morphology(payload: bytes) -> PixelMorphologyMetrics:
    """Apply the frozen long-side-256 morphology thresholds to preview bytes."""
    with Image.open(io.BytesIO(payload)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("morphology preview has invalid dimensions")
    scale = 256 / max(width, height)
    resized = (max(16, round(width * scale)), max(16, round(height * scale)))
    image = image.resize(resized, Image.Resampling.LANCZOS)
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = (maximum - minimum) / np.maximum(maximum, 1.0 / 255.0)
    luma = (0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]).astype(
        np.float32
    )
    if min(luma.shape) >= 3:
        center = luma[1:-1, 1:-1]
        gradient_x = np.empty_like(luma)
        gradient_y = np.empty_like(luma)
        gradient_x[:, 1:-1] = (luma[:, 2:] - luma[:, :-2]) * 0.5
        gradient_x[:, 0] = luma[:, 1] - luma[:, 0]
        gradient_x[:, -1] = luma[:, -1] - luma[:, -2]
        gradient_y[1:-1, :] = (luma[2:, :] - luma[:-2, :]) * 0.5
        gradient_y[0, :] = luma[1, :] - luma[0, :]
        gradient_y[-1, :] = luma[-1, :] - luma[-2, :]
        gradient = np.hypot(gradient_x, gradient_y)
        laplacian = (
            4.0 * center - luma[:-2, 1:-1] - luma[2:, 1:-1] - luma[1:-1, :-2] - luma[1:-1, 2:]
        )
    else:  # pragma: no cover - long-side scaling normally prevents this
        gradient = np.zeros(1, dtype=np.float32)
        laplacian = np.zeros(1, dtype=np.float32)
    near_white = float(((luma >= 0.92) & (saturation <= 0.12)).mean())
    near_black = float((luma <= 0.10).mean())
    edge_fraction = float((gradient >= 0.08).mean())
    laplacian_variance = float(laplacian.var())
    mean_saturation = float(saturation.mean())
    screen_signature = bool(
        near_white >= 0.72 and mean_saturation <= 0.04 and edge_fraction >= 0.065
    )
    quality_failure = bool(
        (laplacian_variance <= 0.0015 and edge_fraction <= 0.02)
        or (
            laplacian_variance <= 0.0035
            and 0.05 <= edge_fraction <= 0.075
            and mean_saturation >= 0.35
        )
        or (near_black >= 0.90 and edge_fraction <= 0.04 and mean_saturation <= 0.08)
        or (
            laplacian_variance <= 0.0021
            and edge_fraction <= 0.035
            and near_black <= 0.10
            and mean_saturation <= 0.20
        )
    )
    return PixelMorphologyMetrics(
        long_side=256,
        near_white_fraction=near_white,
        near_black_fraction=near_black,
        edge_fraction=edge_fraction,
        laplacian_variance=laplacian_variance,
        mean_saturation=mean_saturation,
        screen_signature=screen_signature,
        conservative_quality_failure=quality_failure,
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedPreview:
    """One private preview whose bytes reproduce the sealed v2 index."""

    asset_id: str
    audit_id: str
    source_updated: str
    captured_at: str
    capture_day: str
    moment_key: str
    component_key: str
    image_path: Path
    preview_sha256: str
    preview_width: int
    preview_height: int

    @property
    def updated_at(self) -> str:
        """Compatibility with the durable preview-store row protocol."""
        return self.source_updated

    @property
    def stratum(self) -> str:
        captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        return f"{captured.year}-Q{(captured.month - 1) // 3 + 1}"

    @property
    def morphology(self) -> str:
        return morphology_for_dimensions(self.preview_width, self.preview_height)


@dataclass(frozen=True, slots=True)
class AuthenticatedCandidateStore:
    """Authenticated private mapping for one immutable candidate universe."""

    path: Path
    inventory_sha256: str
    selection_sha256: str
    selection_lock_sha256: str
    manifest_sha256: str
    private_index_sha256: str
    rows: tuple[AuthenticatedPreview, ...]


@dataclass(frozen=True, slots=True)
class ResolvedOpaqueRejections:
    candidate_audit_ids: frozenset[str]
    requested_count: int
    requested_sha256: str
    prior_private_selection_sha256: str | None

    def public_dict(self) -> dict[str, object]:
        return {
            "requested_count": self.requested_count,
            "requested_sha256": self.requested_sha256,
            "resolved_candidate_count": len(self.candidate_audit_ids),
            "prior_private_selection_sha256": self.prior_private_selection_sha256,
        }


@dataclass(frozen=True, slots=True)
class SemanticEncoderIdentity:
    """Exact frozen graph and feature transformation used for refinement."""

    encoder_id: str
    weights_sha256: str
    preprocess_version: str
    layout_version: str

    @property
    def key(self) -> str:
        return _canonical_sha256(
            {
                "encoder_id": self.encoder_id,
                "weights_sha256": self.weights_sha256,
                "preprocess_version": self.preprocess_version,
                "layout_version": self.layout_version,
            }
        )


@dataclass(frozen=True, slots=True)
class SemanticEmbedding:
    preview: AuthenticatedPreview
    vector: np.ndarray
    vector_sha256: str


@dataclass(frozen=True, slots=True)
class SemanticUniverse:
    source: AuthenticatedCandidateStore
    encoder: SemanticEncoderIdentity
    embeddings: tuple[SemanticEmbedding, ...]
    semantic_matrix_sha256: str


@dataclass(frozen=True, slots=True)
class ClusteredSemanticCandidate:
    embedding: SemanticEmbedding
    cluster_label: int
    cluster_distance: float

    @property
    def preview(self) -> AuthenticatedPreview:
        return self.embedding.preview

    @property
    def vector(self) -> np.ndarray:
        return self.embedding.vector


@dataclass(frozen=True, slots=True)
class SemanticClustering:
    candidates: tuple[ClusteredSemanticCandidate, ...]
    cluster_count: int
    normalized_entropy: float
    coverage: float
    largest_cluster_share: float
    medoid_audit_ids: tuple[str, ...]
    clustering_sha256: str


@dataclass(frozen=True, slots=True)
class SemanticNeighborPair:
    left_audit_id: str
    right_audit_id: str
    cosine_similarity: float


@dataclass(frozen=True, slots=True)
class MorphologyPrevalenceCap:
    """Bound one pixel-shape mode relative to its source-universe prevalence."""

    morphology: str
    prevalence_multiplier: float = 1.25

    def __post_init__(self) -> None:
        if self.morphology not in _MORPHOLOGIES:
            raise ValueError(f"unknown morphology: {self.morphology}")
        if not 1.0 <= self.prevalence_multiplier <= 4.0:
            raise ValueError("morphology prevalence multiplier must be between 1 and 4")


def prevalence_count_cap(
    *,
    source_count: int,
    candidate_count: int,
    final_target: int,
    multiplier: float,
) -> int:
    """Round a source-rate ceiling up so rare modes retain representation."""
    if source_count < 0 or candidate_count < 1 or not 1 <= final_target <= candidate_count:
        raise ValueError("invalid prevalence cap counts")
    if not 1.0 <= multiplier <= 4.0:
        raise ValueError("prevalence cap multiplier must be between 1 and 4")
    if source_count == 0:
        return 0
    return max(1, math.ceil(final_target * source_count / candidate_count * multiplier))


def opaque_audit_id_set_sha256(audit_ids: list[str] | tuple[str, ...] | set[str]) -> str:
    """Digest a private opaque-id set without publishing its members."""
    values = sorted(str(value) for value in audit_ids)
    if len(values) != len(set(values)) or any(not value for value in values):
        raise ValueError("opaque audit ids must be unique and non-empty")
    payload = "".join(f"{value}\n" for value in values).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticRefinementConfig:
    """Frozen label-blind selection policy for the v3 refinement."""

    seed: str = "location-recovery-1b-semantic-v3"
    candidate_count: int = 2_000
    final_target: int = 400
    cluster_count: int = 96
    max_per_day: int = 1
    max_per_moment: int = 1
    min_year_quarter_coverage: float = 0.90
    max_sqrt_quota_tv: float = 0.10
    min_cluster_coverage: float = 0.90
    min_cluster_entropy: float = 0.90
    max_selected_cosine_similarity: float = 0.985
    morphology_caps: tuple[MorphologyPrevalenceCap, ...] = (
        MorphologyPrevalenceCap("screen_signature", 1.0),
    )
    exclude_conservative_quality_failures: bool = True
    memory_limit_bytes: int = MAX_SEMANTIC_PROCESS_BYTES

    def __post_init__(self) -> None:
        if not self.seed.strip():
            raise ValueError("semantic refinement seed must not be empty")
        if self.candidate_count < 2 or not 1 <= self.final_target <= self.candidate_count:
            raise ValueError("semantic cohort sizes must satisfy 1 <= final <= candidate")
        if not 2 <= self.cluster_count <= self.candidate_count:
            raise ValueError("semantic cluster count must fit inside the candidate universe")
        if self.max_per_day < 1 or self.max_per_moment < 1:
            raise ValueError("semantic capture caps must be positive")
        for value, name in (
            (self.min_year_quarter_coverage, "year-quarter coverage"),
            (self.max_sqrt_quota_tv, "sqrt-quota TV"),
            (self.min_cluster_coverage, "semantic cluster coverage"),
            (self.min_cluster_entropy, "semantic cluster entropy"),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        if not -1.0 <= self.max_selected_cosine_similarity <= 1.0:
            raise ValueError("semantic similarity cap must be between -1 and one")
        morphologies = [cap.morphology for cap in self.morphology_caps]
        if len(set(morphologies)) != len(morphologies):
            raise ValueError("morphology caps must be unique")
        if not 1 <= self.memory_limit_bytes <= MAX_SEMANTIC_PROCESS_BYTES:
            raise ValueError("semantic memory limit must be at most 8 GiB")

    def public_dict(self) -> dict[str, object]:
        return {
            "seed_sha256": hashlib.sha256(self.seed.encode()).hexdigest(),
            "candidate_count": self.candidate_count,
            "final_target": self.final_target,
            "cluster_count": self.cluster_count,
            "max_per_day": self.max_per_day,
            "max_per_moment": self.max_per_moment,
            "min_year_quarter_coverage": self.min_year_quarter_coverage,
            "max_sqrt_quota_tv": self.max_sqrt_quota_tv,
            "min_cluster_coverage": self.min_cluster_coverage,
            "min_cluster_entropy": self.min_cluster_entropy,
            "max_selected_cosine_similarity": self.max_selected_cosine_similarity,
            "morphology_caps": [
                {
                    "morphology": cap.morphology,
                    "prevalence_multiplier": cap.prevalence_multiplier,
                }
                for cap in self.morphology_caps
            ],
            "exclude_conservative_quality_failures": (self.exclude_conservative_quality_failures),
            "pixel_morphology_version": PIXEL_MORPHOLOGY_VERSION,
            "memory_limit_bytes": self.memory_limit_bytes,
        }


@dataclass(frozen=True, slots=True)
class SemanticSelectionAudit:
    temporal: CohortAudit
    represented_clusters: int
    cluster_coverage: float
    normalized_cluster_entropy: float
    largest_cluster_share: float
    source_morphology_counts: Mapping[str, int]
    selected_morphology_counts: Mapping[str, int]
    morphology_count_caps: Mapping[str, int]
    pixel_morphology_sha256: str
    source_conservative_quality_failure_count: int
    excluded_conservative_quality_failure_count: int
    selected_conservative_quality_failure_count: int
    total_policy_exclusion_count: int
    source_screen_signature_id_set_sha256: str
    source_quality_failure_id_set_sha256: str
    selected_screen_signature_id_set_sha256: str
    selected_quality_failure_id_set_sha256: str
    manual_rejection_count: int
    manual_rejection_sha256: str
    raw_pixel_near_duplicate_rejections: int
    semantic_similarity_rejections: int
    selected_raw_pixel_near_duplicate_pairs: int
    maximum_selected_cosine_similarity: float
    truth_asset_overlap: int
    truth_component_overlap: int
    truth_moment_overlap: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def public_dict(self) -> dict[str, object]:
        return {
            "temporal": self.temporal.public_dict(),
            "represented_clusters": self.represented_clusters,
            "cluster_coverage": self.cluster_coverage,
            "normalized_cluster_entropy": self.normalized_cluster_entropy,
            "largest_cluster_share": self.largest_cluster_share,
            "source_morphology_counts": dict(self.source_morphology_counts),
            "selected_morphology_counts": dict(self.selected_morphology_counts),
            "morphology_count_caps": dict(self.morphology_count_caps),
            "pixel_morphology_sha256": self.pixel_morphology_sha256,
            "conservative_quality": {
                "source_failure_count": self.source_conservative_quality_failure_count,
                "excluded_failure_count": self.excluded_conservative_quality_failure_count,
                "selected_failure_count": self.selected_conservative_quality_failure_count,
                "total_policy_exclusion_count": self.total_policy_exclusion_count,
            },
            "opaque_morphology_set_digests": {
                "source_screen_signature_sha256": (self.source_screen_signature_id_set_sha256),
                "source_quality_failure_sha256": self.source_quality_failure_id_set_sha256,
                "selected_screen_signature_sha256": (self.selected_screen_signature_id_set_sha256),
                "selected_quality_failure_sha256": (self.selected_quality_failure_id_set_sha256),
            },
            "manual_rejection_count": self.manual_rejection_count,
            "manual_rejection_sha256": self.manual_rejection_sha256,
            "raw_pixel_near_duplicate_rejections": self.raw_pixel_near_duplicate_rejections,
            "semantic_similarity_rejections": self.semantic_similarity_rejections,
            "selected_raw_pixel_near_duplicate_pairs": (
                self.selected_raw_pixel_near_duplicate_pairs
            ),
            "maximum_selected_cosine_similarity": self.maximum_selected_cosine_similarity,
            "truth_overlap": {
                "asset_ids": self.truth_asset_overlap,
                "components": self.truth_component_overlap,
                "moments": self.truth_moment_overlap,
            },
            "passed": self.passed,
            "failures": list(self.failures),
        }


class SemanticSelectionQualityError(RuntimeError):
    def __init__(self, audit: SemanticSelectionAudit) -> None:
        super().__init__("semantic cohort quality gates failed: " + ", ".join(audit.failures))
        self.audit = audit


@dataclass(frozen=True, slots=True)
class SemanticSelection:
    selected: tuple[ClusteredSemanticCandidate, ...]
    selection_sha256: str
    audit: SemanticSelectionAudit
    pixel_metrics: Mapping[str, PixelMorphologyMetrics]


@dataclass(frozen=True, slots=True)
class SemanticRefinementArtifacts:
    output_dir: Path
    public_manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AuthenticatedSemanticRefinement:
    output_dir: Path
    public_manifest: Mapping[str, object]
    universe: SemanticUniverse
    clustering: SemanticClustering
    selection: SemanticSelection
    config: SemanticRefinementConfig
    final_store: AuthenticatedCandidateStore
    review_pages: tuple[tuple[str, str], ...]
    review_manifest_sha256: str


def _safe_preview_path(store: Path, relative: str) -> Path:
    root = store.resolve()
    path = (store / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("preview index contains a path outside its store")
    if not path.is_file():
        raise ValueError("preview index points to a missing image")
    return path


def _verify_self_digest(payload: Mapping[str, object], field: str, *, what: str) -> str:
    claimed = str(payload.get(field, ""))
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if _canonical_sha256(unsigned) != claimed:
        raise ValueError(f"{what} digest does not reproduce")
    return claimed


def load_authenticated_candidate_store(
    store_path: Path,
    *,
    expected_count: int = 2_000,
) -> AuthenticatedCandidateStore:
    """Authenticate the exact v2 candidate store without reading task labels."""
    if expected_count < 1:
        raise ValueError("expected candidate count must be positive")
    store = Path(store_path)
    manifest_path = store / "manifest.json"
    private_path = store / "private-index.json"
    lock_path = store / "selection-lock.json"
    for path in (manifest_path, private_path, lock_path):
        if not path.is_file():
            raise ValueError(f"candidate store is missing {path.name}")
    if private_path.stat().st_mode & 0o077 or lock_path.stat().st_mode & 0o077:
        raise PermissionError("candidate private index and selection lock must be mode 0600")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    private_bytes = private_path.read_bytes()
    private = json.loads(private_bytes)
    selection_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "triage-pinned-preview-store-v1":
        raise ValueError("candidate manifest has an unsupported schema")
    if private.get("schema") != "triage-private-preview-index-v1":
        raise ValueError("candidate private index has an unsupported schema")
    if selection_lock.get("schema") != "triage-preview-selection-lock-v1":
        raise ValueError("candidate selection lock has an unsupported schema")
    manifest_sha256 = _verify_self_digest(
        manifest,
        "manifest_sha256",
        what="candidate manifest",
    )
    selection_lock_sha256 = _verify_self_digest(
        selection_lock,
        "selection_lock_sha256",
        what="candidate selection lock",
    )
    private_index_sha256 = hashlib.sha256(private_bytes).hexdigest()
    if private_index_sha256 != manifest.get("private_index_sha256"):
        raise ValueError("candidate private index digest does not reproduce")

    header_fields = ("cohort_name", "inventory_sha256", "selection_sha256")
    for field in header_fields:
        values = {str(payload.get(field, "")) for payload in (manifest, private, selection_lock)}
        if len(values) != 1:
            raise ValueError(f"candidate store disagrees about {field}")
    if {
        str(manifest.get("selection_lock_sha256", "")),
        str(private.get("selection_lock_sha256", "")),
        selection_lock_sha256,
    } != {selection_lock_sha256}:
        raise ValueError("candidate store disagrees about its selection lock")

    raw_rows = private.get("rows")
    lock_rows = selection_lock.get("rows")
    public_rows = manifest.get("rows")
    if not isinstance(raw_rows, list) or not isinstance(lock_rows, list):
        raise ValueError("candidate private rows are malformed")
    if not isinstance(public_rows, list):
        raise ValueError("candidate public rows are malformed")
    if len(raw_rows) != expected_count or manifest.get("row_count") != expected_count:
        raise ValueError(f"candidate store must contain exactly {expected_count} previews")
    if len(lock_rows) != expected_count or len(public_rows) != expected_count:
        raise ValueError("candidate store row counts disagree")

    lock_by_audit = {str(row.get("audit_id")): row for row in lock_rows if isinstance(row, Mapping)}
    public_by_audit = {
        str(row.get("audit_id")): row for row in public_rows if isinstance(row, Mapping)
    }
    if len(lock_by_audit) != expected_count or len(public_by_audit) != expected_count:
        raise ValueError("candidate store contains duplicate or malformed audit ids")

    authenticated: list[AuthenticatedPreview] = []
    seen_assets: set[str] = set()
    seen_paths: set[Path] = set()
    for raw in raw_rows:
        if not isinstance(raw, Mapping) or set(raw) != _PRIVATE_PREVIEW_ROW_KEYS:
            raise ValueError("candidate private row violates the label-blind schema")
        audit_id = str(raw["audit_id"])
        asset_id = str(raw["asset_id"])
        if asset_id in seen_assets:
            raise ValueError("candidate private index contains duplicate asset ids")
        lock_row = lock_by_audit.get(audit_id)
        public_row = public_by_audit.get(audit_id)
        if lock_row is None or public_row is None:
            raise ValueError("candidate row is absent from a sealed companion index")
        lock_keys = (
            "audit_id",
            "asset_id",
            "source_updated",
            "captured_at",
            "capture_day",
            "moment_key",
            "component_key",
            "image_relpath",
        )
        if any(lock_row.get(key) != raw[key] for key in lock_keys):
            raise ValueError("candidate selection lock differs from its private index")
        public_keys = (
            "audit_id",
            "image_relpath",
            "preview_sha256",
            "preview_bytes",
            "preview_width",
            "preview_height",
            "preview_format",
        )
        if any(public_row.get(key) != raw[key] for key in public_keys):
            raise ValueError("candidate public manifest differs from its private index")

        image_path = _safe_preview_path(store, str(raw["image_relpath"]))
        if image_path in seen_paths:
            raise ValueError("candidate private index reuses an image path")
        payload = image_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != raw["preview_sha256"]:
            raise ValueError("candidate preview bytes differ from their pinned digest")
        if len(payload) != raw["preview_bytes"]:
            raise ValueError("candidate preview byte count differs from its index")
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            dimensions = image.size
            image_format = str(image.format or "unknown").lower()
        if dimensions != (int(raw["preview_width"]), int(raw["preview_height"])):
            raise ValueError("candidate preview dimensions differ from their index")
        if image_format != raw["preview_format"]:
            raise ValueError("candidate preview format differs from its index")
        captured = datetime.fromisoformat(str(raw["captured_at"]).replace("Z", "+00:00"))
        if captured.date().isoformat() != str(raw["capture_day"]):
            raise ValueError("candidate capture day differs from captured_at")
        authenticated.append(
            AuthenticatedPreview(
                asset_id=asset_id,
                audit_id=audit_id,
                source_updated=str(raw["source_updated"]),
                captured_at=str(raw["captured_at"]),
                capture_day=str(raw["capture_day"]),
                moment_key=str(raw["moment_key"]),
                component_key=str(raw["component_key"]),
                image_path=image_path,
                preview_sha256=str(raw["preview_sha256"]),
                preview_width=int(raw["preview_width"]),
                preview_height=int(raw["preview_height"]),
            )
        )
        seen_assets.add(asset_id)
        seen_paths.add(image_path)
    authenticated.sort(key=lambda row: (row.audit_id, row.asset_id))
    return AuthenticatedCandidateStore(
        path=store,
        inventory_sha256=str(manifest["inventory_sha256"]),
        selection_sha256=str(manifest["selection_sha256"]),
        selection_lock_sha256=selection_lock_sha256,
        manifest_sha256=manifest_sha256,
        private_index_sha256=private_index_sha256,
        rows=tuple(authenticated),
    )


def resolve_opaque_rejections(
    source: AuthenticatedCandidateStore,
    requested_audit_ids: frozenset[str],
    *,
    prior_private_selection: Path | None = None,
) -> ResolvedOpaqueRejections:
    """Resolve prior ``F`` aliases to source ``U`` ids without accepting assets."""
    if any(not value.strip() for value in requested_audit_ids):
        raise ValueError("manual rejection audit ids must not be empty")
    source_by_audit = {row.audit_id: row for row in source.rows}
    resolved = requested_audit_ids & source_by_audit.keys()
    unresolved = requested_audit_ids - resolved
    prior_sha256: str | None = None
    aliases: dict[str, str] = {}
    if prior_private_selection is not None:
        path = Path(prior_private_selection)
        if not path.is_file():
            raise ValueError("prior private selection does not exist")
        if path.stat().st_mode & 0o077:
            raise PermissionError("prior private selection must be mode 0600")
        payload = path.read_bytes()
        prior_sha256 = hashlib.sha256(payload).hexdigest()
        plan = json.loads(payload)
        if plan.get("schema") != "triage-private-provisional-visual-cohort-v1":
            raise ValueError("prior private selection has an unsupported schema")
        claimed = str(plan.get("private_plan_sha256", ""))
        unsigned = dict(plan)
        unsigned.pop("private_plan_sha256", None)
        reproduced = hashlib.sha256(_VISUAL_PLAN_DOMAIN + _canonical_bytes(unsigned)).hexdigest()
        if claimed != reproduced:
            raise ValueError("prior private selection digest does not reproduce")
        plan_rows = plan.get("selected_rows")
        if not isinstance(plan_rows, list):
            raise ValueError("prior private selection rows are malformed")
        seen_candidates: set[str] = set()
        for raw in plan_rows:
            if not isinstance(raw, Mapping):
                raise ValueError("prior private selection contains a malformed row")
            candidate_audit_id = str(raw.get("candidate_audit_id", ""))
            final_audit_id = str(raw.get("final_audit_id", ""))
            preview = source_by_audit.get(candidate_audit_id)
            if preview is None:
                raise ValueError("prior private selection is not a subset of the v2 source")
            if (
                raw.get("asset_id") != preview.asset_id
                or raw.get("preview_sha256") != preview.preview_sha256
            ):
                raise ValueError("prior private selection differs from authenticated source")
            if final_audit_id in aliases or candidate_audit_id in seen_candidates:
                raise ValueError("prior private selection contains duplicate opaque ids")
            aliases[final_audit_id] = candidate_audit_id
            seen_candidates.add(candidate_audit_id)
        missing_aliases = unresolved - aliases.keys()
        if missing_aliases:
            raise ValueError("manual rejection list contains an unknown opaque audit id")
        resolved = resolved | {aliases[audit_id] for audit_id in unresolved}
    elif unresolved:
        raise ValueError("prior final audit ids require their authenticated private selection")
    requested_sha256 = _canonical_sha256(
        {
            "schema": "triage-requested-opaque-rejections-v1",
            "audit_ids": sorted(requested_audit_ids),
        }
    )
    return ResolvedOpaqueRejections(
        candidate_audit_ids=frozenset(resolved),
        requested_count=len(requested_audit_ids),
        requested_sha256=requested_sha256,
        prior_private_selection_sha256=prior_sha256,
    )


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _assert_memory_limit(limit_bytes: int) -> None:
    if not 1 <= limit_bytes <= MAX_SEMANTIC_PROCESS_BYTES:
        raise ValueError("semantic memory limit must be between one byte and 8 GiB")
    observed = _peak_rss_bytes()
    if observed >= limit_bytes:
        raise MemoryError(
            f"semantic-refinement process reached {observed / 1024**3:.2f} GiB; "
            f"limit is {limit_bytes / 1024**3:.2f} GiB"
        )


def estimated_semantic_working_set_bytes(
    row_count: int,
    *,
    batch_size: int,
    cluster_count: int,
    neighbor_block_size: int = 128,
) -> int:
    """Conservative dense-array budget for embedding, clustering, and audit."""
    if row_count < 1 or batch_size < 1 or cluster_count < 2 or neighbor_block_size < 1:
        raise ValueError("semantic working-set dimensions must be positive")
    float_bytes = np.dtype(np.float32).itemsize
    vectors = row_count * SEMANTIC_DIM * float_bytes
    token_batch = batch_size * (1 + PATCH_GRID * PATCH_GRID) * TOKEN_DIM * float_bytes
    image_batch = batch_size * 3 * CROP_SIZE * CROP_SIZE * float_bytes
    cluster_distances = row_count * cluster_count * float_bytes
    neighbor_block = neighbor_block_size * row_count * float_bytes
    # sklearn and ONNX own transient copies; a 4x multiplier keeps this estimate
    # intentionally conservative while remaining far below the 8 GiB hard stop.
    return 4 * (vectors + token_batch + image_batch + cluster_distances + neighbor_block)


def _preprocess_authenticated_payload(payload: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGB")
    width, height = image.size
    if width <= height:
        resized = (RESIZE_SHORT_SIDE, round(height * RESIZE_SHORT_SIDE / width))
    else:
        resized = (round(width * RESIZE_SHORT_SIDE / height), RESIZE_SHORT_SIDE)
    image = image.resize(resized, Image.Resampling.BICUBIC)
    left = (resized[0] - CROP_SIZE) // 2
    top = (resized[1] - CROP_SIZE) // 2
    image = image.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    normalized = (pixels - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)


def _onnx_token_output(outputs: object) -> np.ndarray:
    if not isinstance(outputs, (list, tuple)):
        raise ValueError("ONNX session returned a malformed output collection")
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[1:] == (
            1 + PATCH_GRID * PATCH_GRID,
            TOKEN_DIM,
        ):
            return array.astype(np.float32, copy=False)
    shapes = [np.asarray(output).shape for output in outputs]
    raise ValueError(f"ONNX export did not return frozen DINO tokens; outputs={shapes}")


def semantic_matrix_sha256(
    source: AuthenticatedCandidateStore,
    encoder: SemanticEncoderIdentity,
    embeddings: tuple[SemanticEmbedding, ...] | list[SemanticEmbedding],
) -> str:
    """Bind ordered opaque ids, authenticated pixels, and exact vector bytes."""
    ordered = sorted(embeddings, key=lambda item: item.preview.audit_id)
    if {item.preview.audit_id for item in ordered} != {row.audit_id for row in source.rows}:
        raise ValueError("semantic matrix rows differ from the authenticated source")
    records: list[dict[str, str]] = []
    for item in ordered:
        vector = np.ascontiguousarray(item.vector, dtype=np.float32)
        vector_sha256 = hashlib.sha256(vector.tobytes()).hexdigest()
        if item.vector_sha256 != vector_sha256:
            raise ValueError("semantic vector digest does not reproduce")
        records.append(
            {
                "audit_id": item.preview.audit_id,
                "preview_sha256": item.preview.preview_sha256,
                "vector_sha256": vector_sha256,
            }
        )
    return _canonical_sha256(
        {
            "schema": "triage-dinov2-semantic-matrix-v1",
            "source_manifest_sha256": source.manifest_sha256,
            "encoder_key": encoder.key,
            "records": records,
        }
    )


def embed_authenticated_candidate_store(
    store: AuthenticatedCandidateStore,
    *,
    session: object,
    verified_onnx_sha256: str,
    batch_size: int = 8,
    memory_limit_bytes: int = MAX_SEMANTIC_PROCESS_BYTES,
) -> SemanticUniverse:
    """Stream authenticated pixels through a digest-verified DINOv2 session."""
    if verified_onnx_sha256 != EXPECTED_ONNX_SHA256:
        raise ValueError("semantic refinement requires the pinned DINOv2 ONNX digest")
    if not 1 <= batch_size <= 32:
        raise ValueError("semantic embedding batch size must be between one and 32")
    estimate = estimated_semantic_working_set_bytes(
        len(store.rows),
        batch_size=batch_size,
        cluster_count=min(96, len(store.rows)),
    )
    if estimate >= memory_limit_bytes:
        raise MemoryError("configured semantic embedding cannot fit its memory limit")
    _assert_memory_limit(memory_limit_bytes)
    encoder = SemanticEncoderIdentity(
        encoder_id=f"{MODEL_ID}@{MODEL_REVISION}",
        weights_sha256=verified_onnx_sha256,
        preprocess_version=PREPROCESS_VERSION,
        layout_version=SEMANTIC_LAYOUT_VERSION,
    )
    inputs = session.get_inputs()  # type: ignore[attr-defined]
    if len(inputs) != 1 or not getattr(inputs[0], "name", ""):
        raise ValueError("DINOv2 ONNX session must expose exactly one named input")
    input_name = str(inputs[0].name)
    embeddings: list[SemanticEmbedding] = []
    for start in range(0, len(store.rows), batch_size):
        chunk = store.rows[start : start + batch_size]
        images: list[np.ndarray] = []
        for preview in chunk:
            payload = preview.image_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != preview.preview_sha256:
                raise ValueError("candidate preview bytes changed after store authentication")
            images.append(_preprocess_authenticated_payload(payload))
        batch = np.stack(images)
        tokens = _onnx_token_output(session.run(None, {input_name: batch}))  # type: ignore[attr-defined]
        if len(tokens) != len(chunk):
            raise ValueError("DINOv2 ONNX output batch size differs from its input")
        vectors = semantic_vectors(tokens)
        for preview, vector in zip(chunk, vectors, strict=True):
            frozen = np.ascontiguousarray(vector, dtype=np.float32)
            vector_sha256 = hashlib.sha256(frozen.tobytes()).hexdigest()
            embeddings.append(
                SemanticEmbedding(
                    preview=preview,
                    vector=frozen,
                    vector_sha256=vector_sha256,
                )
            )
        _assert_memory_limit(memory_limit_bytes)
    semantic_matrix_digest = semantic_matrix_sha256(store, encoder, embeddings)
    return SemanticUniverse(
        source=store,
        encoder=encoder,
        embeddings=tuple(embeddings),
        semantic_matrix_sha256=semantic_matrix_digest,
    )


def _validated_semantic_matrix(
    embeddings: tuple[SemanticEmbedding, ...] | list[SemanticEmbedding],
) -> np.ndarray:
    if not embeddings:
        raise ValueError("semantic embeddings cannot be empty")
    audit_ids = [embedding.preview.audit_id for embedding in embeddings]
    if len(set(audit_ids)) != len(audit_ids):
        raise ValueError("semantic embeddings contain duplicate audit ids")
    vectors = np.stack([embedding.vector for embedding in embeddings]).astype(np.float32)
    if vectors.shape != (len(embeddings), SEMANTIC_DIM) or not np.isfinite(vectors).all():
        raise ValueError("semantic embedding matrix violates its shape or finiteness contract")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=2e-5):
        raise ValueError("semantic embeddings must be unit normalized")
    return vectors


def semantic_clustering_sha256(
    candidates: tuple[ClusteredSemanticCandidate, ...] | list[ClusteredSemanticCandidate],
    *,
    cluster_count: int,
    seed: str,
) -> str:
    records = [
        {
            "audit_id": candidate.preview.audit_id,
            "vector_sha256": candidate.embedding.vector_sha256,
            "cluster_label": candidate.cluster_label,
            "cluster_distance_f32": np.float32(candidate.cluster_distance).tobytes().hex(),
        }
        for candidate in sorted(candidates, key=lambda item: item.preview.audit_id)
    ]
    return _canonical_sha256(
        {
            "schema": "triage-dinov2-semantic-clustering-v1",
            "semantic_layout_version": SEMANTIC_LAYOUT_VERSION,
            "seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
            "cluster_count": cluster_count,
            "records": records,
        }
    )


def cluster_semantic_embeddings(
    embeddings: tuple[SemanticEmbedding, ...] | list[SemanticEmbedding],
    *,
    cluster_count: int,
    seed: str,
    memory_limit_bytes: int = MAX_SEMANTIC_PROCESS_BYTES,
) -> SemanticClustering:
    """Freeze deterministic spherical-equivalent clusters over DINO semantics."""
    if not seed.strip():
        raise ValueError("semantic clustering seed must not be empty")
    ordered = tuple(sorted(embeddings, key=lambda item: item.preview.audit_id))
    if not 2 <= cluster_count <= len(ordered):
        raise ValueError("semantic cluster count must fit inside the candidate universe")
    estimate = estimated_semantic_working_set_bytes(
        len(ordered),
        batch_size=1,
        cluster_count=cluster_count,
    )
    if estimate >= memory_limit_bytes:
        raise MemoryError("semantic clustering cannot fit its configured memory limit")
    _assert_memory_limit(memory_limit_bytes)
    vectors = _validated_semantic_matrix(ordered)

    from sklearn.cluster import MiniBatchKMeans

    random_state = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    model = MiniBatchKMeans(
        n_clusters=cluster_count,
        batch_size=min(1024, max(64, cluster_count * 4)),
        n_init=10,
        max_iter=200,
        random_state=random_state,
        reassignment_ratio=0.0,
    )
    labels = model.fit_predict(vectors).astype(np.int32, copy=False)
    all_distances = model.transform(vectors).astype(np.float32, copy=False)
    distances = all_distances[np.arange(len(ordered)), labels]
    counts = Counter(int(label) for label in labels)
    probabilities = np.asarray(list(counts.values()), dtype=np.float64) / len(ordered)
    entropy = float(-(probabilities * np.log(probabilities)).sum() / math.log(cluster_count))
    medoid_indices: list[int] = []
    for label in sorted(counts):
        members = np.flatnonzero(labels == label)
        medoid_indices.append(int(members[np.argmin(distances[members])]))
    candidates = tuple(
        ClusteredSemanticCandidate(
            embedding=embedding,
            cluster_label=int(label),
            cluster_distance=float(distance),
        )
        for embedding, label, distance in zip(ordered, labels, distances, strict=True)
    )
    clustering_sha256 = semantic_clustering_sha256(
        candidates,
        cluster_count=cluster_count,
        seed=seed,
    )
    _assert_memory_limit(memory_limit_bytes)
    return SemanticClustering(
        candidates=candidates,
        cluster_count=cluster_count,
        normalized_entropy=entropy,
        coverage=len(counts) / cluster_count,
        largest_cluster_share=max(counts.values()) / len(ordered),
        medoid_audit_ids=tuple(ordered[index].preview.audit_id for index in medoid_indices),
        clustering_sha256=clustering_sha256,
    )


def nearest_semantic_pairs(
    candidates: tuple[ClusteredSemanticCandidate, ...] | list[ClusteredSemanticCandidate],
    *,
    limit: int,
    block_size: int = 128,
    memory_limit_bytes: int = MAX_SEMANTIC_PROCESS_BYTES,
) -> tuple[SemanticNeighborPair, ...]:
    """Report the closest semantic pairs using an O(block*n) similarity buffer."""
    if limit < 1 or not 1 <= block_size <= 1024:
        raise ValueError("neighbor limit and block size must be positive and bounded")
    ordered = tuple(sorted(candidates, key=lambda item: item.preview.audit_id))
    if len(ordered) < 2:
        return ()
    vectors = _validated_semantic_matrix([candidate.embedding for candidate in ordered])
    scratch_bytes = block_size * len(ordered) * np.dtype(np.float32).itemsize
    if scratch_bytes + vectors.nbytes >= memory_limit_bytes:
        raise MemoryError("semantic neighbor audit cannot fit its configured memory limit")
    _assert_memory_limit(memory_limit_bytes)
    heap: list[tuple[float, int, int, int]] = []
    for start in range(0, len(ordered), block_size):
        stop = min(len(ordered), start + block_size)
        similarities = vectors[start:stop] @ vectors.T
        for local_index, left in enumerate(range(start, stop)):
            available = len(ordered) - left - 1
            if available < 1:
                continue
            row = similarities[local_index, left + 1 :]
            keep = min(limit, available)
            if keep == available:
                offsets = np.arange(available)
            else:
                offsets = np.argpartition(row, -keep)[-keep:]
            for offset in offsets:
                right = left + 1 + int(offset)
                similarity = float(row[int(offset)])
                pair_key = ":".join(
                    (ordered[left].preview.audit_id, ordered[right].preview.audit_id)
                )
                tie_rank = int(hashlib.sha256(pair_key.encode()).hexdigest(), 16)
                entry = (similarity, tie_rank, left, right)
                if len(heap) < limit:
                    heapq.heappush(heap, entry)
                elif entry[:2] > heap[0][:2]:
                    heapq.heapreplace(heap, entry)
        _assert_memory_limit(memory_limit_bytes)
    ordered_pairs = sorted(
        heap,
        key=lambda entry: (
            -entry[0],
            ordered[entry[2]].preview.audit_id,
            ordered[entry[3]].preview.audit_id,
        ),
    )
    return tuple(
        SemanticNeighborPair(
            left_audit_id=ordered[left].preview.audit_id,
            right_audit_id=ordered[right].preview.audit_id,
            cosine_similarity=similarity,
        )
        for similarity, _tie_rank, left, right in ordered_pairs
    )


def _stable_rank(seed: str, scope: str, audit_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{audit_id}".encode()).hexdigest()


def _selection_spec(config: SemanticRefinementConfig) -> CohortSpec:
    return CohortSpec(
        target_size=config.final_target,
        minimum_size=config.final_target,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        min_distinct_days=config.final_target,
        min_year_quarter_coverage=config.min_year_quarter_coverage,
        max_sqrt_quota_tv=config.max_sqrt_quota_tv,
    )


def _balanced_stratum_targets(
    candidates: tuple[ClusteredSemanticCandidate, ...],
    *,
    source_counts: Counter[str],
    target: int,
    seed: str,
) -> dict[str, int]:
    capacities = Counter(candidate.preview.stratum for candidate in candidates)
    feasible = [stratum for stratum, count in capacities.items() if count > 0]
    coverage_order = sorted(
        feasible,
        key=lambda stratum: (
            -math.sqrt(source_counts[stratum]),
            _stable_rank(seed, "semantic-stratum-coverage", stratum),
            stratum,
        ),
    )
    targets: Counter[str] = Counter(
        dict.fromkeys(coverage_order[: min(target, len(coverage_order))], 1)
    )
    while sum(targets.values()) < target:
        available = [
            stratum for stratum, capacity in capacities.items() if targets[stratum] < capacity
        ]
        if not available:
            break
        chosen = min(
            available,
            key=lambda stratum: (
                (targets[stratum] + 0.5) / math.sqrt(source_counts[stratum]),
                _stable_rank(seed, "semantic-stratum-quota", stratum),
                stratum,
            ),
        )
        targets[chosen] += 1
    return dict(targets)


def _authenticated_pixel_descriptors(
    candidates: tuple[ClusteredSemanticCandidate, ...],
    *,
    memory_limit_bytes: int,
) -> tuple[dict[str, PixelDescriptor], dict[str, PixelMorphologyMetrics], str]:
    descriptors: dict[str, PixelDescriptor] = {}
    metrics_by_audit: dict[str, PixelMorphologyMetrics] = {}
    metric_records: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates, start=1):
        preview = candidate.preview
        payload = preview.image_path.read_bytes()
        descriptor = describe_preview_bytes(payload)
        if descriptor.preview_sha256 != preview.preview_sha256:
            raise ValueError("candidate preview changed before raw-pixel duplicate audit")
        metrics = describe_pixel_morphology(payload)
        descriptors[preview.audit_id] = descriptor
        metrics_by_audit[preview.audit_id] = metrics
        metric_records.append(
            {
                "audit_id": preview.audit_id,
                "preview_sha256": preview.preview_sha256,
                "near_white_f64": np.float64(metrics.near_white_fraction).tobytes().hex(),
                "near_black_f64": np.float64(metrics.near_black_fraction).tobytes().hex(),
                "edge_f64": np.float64(metrics.edge_fraction).tobytes().hex(),
                "laplacian_variance_f64": np.float64(metrics.laplacian_variance).tobytes().hex(),
                "mean_saturation_f64": np.float64(metrics.mean_saturation).tobytes().hex(),
                "screen_signature": metrics.screen_signature,
                "conservative_quality_failure": metrics.conservative_quality_failure,
            }
        )
        if index % 128 == 0:
            _assert_memory_limit(memory_limit_bytes)
    morphology_sha256 = _canonical_sha256(
        {
            "schema": "triage-pixel-morphology-evidence-v1",
            "version": PIXEL_MORPHOLOGY_VERSION,
            "records": metric_records,
        }
    )
    return descriptors, metrics_by_audit, morphology_sha256


def _truth_overlap(
    previews: tuple[AuthenticatedPreview, ...] | list[AuthenticatedPreview],
    blocked: ReservationBlocklist,
) -> tuple[int, int, int]:
    return (
        sum(preview.asset_id in blocked.asset_ids for preview in previews),
        sum(preview.component_key in blocked.component_keys for preview in previews),
        sum(preview.moment_key in blocked.moment_keys for preview in previews),
    )


def select_semantic_cohort(
    candidates: tuple[ClusteredSemanticCandidate, ...] | list[ClusteredSemanticCandidate],
    *,
    source: AuthenticatedCandidateStore,
    config: SemanticRefinementConfig,
    manual_rejections: frozenset[str] = frozenset(),
    blocked: ReservationBlocklist = ReservationBlocklist(),
    require_quality: bool = True,
) -> SemanticSelection:
    """Select a varied cohort without task labels, confidence, or head outputs."""
    ordered = tuple(sorted(candidates, key=lambda item: item.preview.audit_id))
    if len(ordered) != config.candidate_count or len(source.rows) != config.candidate_count:
        raise ValueError(
            f"semantic refinement requires exactly {config.candidate_count} candidates"
        )
    source_by_audit = {row.audit_id: row for row in source.rows}
    if len(source_by_audit) != len(source.rows):
        raise ValueError("source candidate store has duplicate audit ids")
    candidate_ids = [candidate.preview.audit_id for candidate in ordered]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("semantic candidates have duplicate audit ids")
    if set(candidate_ids) != set(source_by_audit):
        raise ValueError("semantic candidates differ from the authenticated source store")
    if any(
        candidate.preview != source_by_audit[candidate.preview.audit_id] for candidate in ordered
    ):
        raise ValueError("semantic candidate metadata differs from the authenticated source")
    unknown_rejections = manual_rejections - set(candidate_ids)
    if unknown_rejections:
        raise ValueError("manual rejection list contains an unknown opaque audit id")
    source_truth_overlap = _truth_overlap(list(source.rows), blocked)
    if any(source_truth_overlap):
        raise ValueError("authenticated candidate source overlaps reserved truth")
    if {candidate.cluster_label for candidate in ordered} - set(range(config.cluster_count)):
        raise ValueError("semantic candidate has a cluster outside the frozen universe")
    _validated_semantic_matrix([candidate.embedding for candidate in ordered])
    descriptors, pixel_metrics, pixel_morphology_sha256 = _authenticated_pixel_descriptors(
        ordered,
        memory_limit_bytes=config.memory_limit_bytes,
    )

    quality_failure_ids = {
        audit_id
        for audit_id, metrics in pixel_metrics.items()
        if metrics.conservative_quality_failure
    }
    screen_signature_ids = {
        audit_id for audit_id, metrics in pixel_metrics.items() if metrics.screen_signature
    }
    remaining = tuple(
        candidate
        for candidate in ordered
        if candidate.preview.audit_id not in manual_rejections
        and (
            not config.exclude_conservative_quality_failures
            or candidate.preview.audit_id not in quality_failure_ids
        )
    )
    source_strata = Counter(row.stratum for row in source.rows)
    stratum_targets = _balanced_stratum_targets(
        remaining,
        source_counts=source_strata,
        target=config.final_target,
        seed=config.seed,
    )
    candidate_morphologies = {
        row.audit_id: frozenset(
            {row.morphology}
            | ({"screen_signature"} if pixel_metrics[row.audit_id].screen_signature else set())
        )
        for row in source.rows
    }
    source_morphologies: Counter[str] = Counter()
    for modes in candidate_morphologies.values():
        source_morphologies.update(modes)
    morphology_count_caps = {
        cap.morphology: prevalence_count_cap(
            source_count=source_morphologies[cap.morphology],
            candidate_count=config.candidate_count,
            final_target=config.final_target,
            multiplier=cap.prevalence_multiplier,
        )
        for cap in config.morphology_caps
        if source_morphologies[cap.morphology]
    }
    cluster_capacities = Counter(candidate.cluster_label for candidate in remaining)

    selected: list[ClusteredSemanticCandidate] = []
    selected_ids: set[str] = set()
    selected_days: Counter[str] = Counter()
    selected_moments: Counter[str] = Counter()
    selected_components: set[str] = set()
    selected_strata: Counter[str] = Counter()
    selected_clusters: Counter[int] = Counter()
    selected_morphologies: Counter[str] = Counter()
    near_duplicate_rejections: set[str] = set()
    similarity_rejections: set[str] = set()

    while len(selected) < config.final_target:
        available: list[tuple[object, ...]] = []
        for candidate in remaining:
            preview = candidate.preview
            audit_id = preview.audit_id
            if audit_id in selected_ids:
                continue
            if (
                selected_days[preview.capture_day] >= config.max_per_day
                or selected_moments[preview.moment_key] >= config.max_per_moment
                or preview.component_key in selected_components
                or selected_strata[preview.stratum] >= stratum_targets.get(preview.stratum, 0)
            ):
                continue
            capped_modes = candidate_morphologies[audit_id] & morphology_count_caps.keys()
            if any(
                selected_morphologies[mode] >= morphology_count_caps[mode] for mode in capped_modes
            ):
                continue
            if any(
                confirmed_near_duplicate(
                    descriptors[audit_id],
                    descriptors[retained.preview.audit_id],
                )
                for retained in selected
            ):
                near_duplicate_rejections.add(audit_id)
                continue
            maximum_similarity = max(
                (float(candidate.vector @ retained.vector) for retained in selected),
                default=-1.0,
            )
            if maximum_similarity > config.max_selected_cosine_similarity + 1e-12:
                similarity_rejections.add(audit_id)
                continue
            cluster_capacity = cluster_capacities[candidate.cluster_label]
            available.append(
                (
                    int(selected_clusters[candidate.cluster_label] > 0),
                    (selected_clusters[candidate.cluster_label] + 0.5)
                    / math.sqrt(cluster_capacity),
                    maximum_similarity,
                    selected_strata[preview.stratum] / stratum_targets[preview.stratum],
                    candidate.cluster_distance,
                    _stable_rank(config.seed, "semantic-final", audit_id),
                    audit_id,
                    candidate,
                )
            )
        if not available:
            break
        chosen = min(available)[-1]
        preview = chosen.preview
        selected.append(chosen)
        selected_ids.add(preview.audit_id)
        selected_days[preview.capture_day] += 1
        selected_moments[preview.moment_key] += 1
        selected_components.add(preview.component_key)
        selected_strata[preview.stratum] += 1
        selected_clusters[chosen.cluster_label] += 1
        selected_morphologies.update(candidate_morphologies[preview.audit_id])
        if len(selected) % 64 == 0:
            _assert_memory_limit(config.memory_limit_bytes)

    selected_tuple = tuple(selected)
    selected_previews = tuple(candidate.preview for candidate in selected_tuple)
    temporal = audit_cohort(  # type: ignore[arg-type]
        selected_previews,
        source.rows,
        _selection_spec(config),
    )
    if selected_tuple:
        cluster_metrics = selected_cluster_metrics(
            [candidate.cluster_label for candidate in selected_tuple],
            universe_cluster_count=config.cluster_count,
        )
    else:
        cluster_metrics = {
            "represented_clusters": 0,
            "cluster_coverage": 0.0,
            "normalized_cluster_entropy": 0.0,
            "largest_cluster_share": 0.0,
        }
    selected_duplicate_pairs = sum(
        confirmed_near_duplicate(
            descriptors[left.preview.audit_id],
            descriptors[right.preview.audit_id],
        )
        for index, left in enumerate(selected_tuple)
        for right in selected_tuple[index + 1 :]
    )
    selected_semantic_pairs = nearest_semantic_pairs(
        selected_tuple,
        limit=1,
        memory_limit_bytes=config.memory_limit_bytes,
    )
    maximum_similarity = (
        selected_semantic_pairs[0].cosine_similarity if selected_semantic_pairs else -1.0
    )
    truth_overlap = _truth_overlap(list(selected_previews), blocked)
    selected_quality_failures = len(selected_ids & quality_failure_ids)
    selected_screen_ids = selected_ids & screen_signature_ids
    selected_quality_ids = selected_ids & quality_failure_ids
    failures = list(temporal.failures)
    if len(selected_tuple) != config.final_target:
        failures.append("target_not_reached")
    if float(cluster_metrics["cluster_coverage"]) + 1e-12 < config.min_cluster_coverage:
        failures.append("semantic_cluster_coverage_below_gate")
    if float(cluster_metrics["normalized_cluster_entropy"]) + 1e-12 < config.min_cluster_entropy:
        failures.append("semantic_cluster_entropy_below_gate")
    if selected_duplicate_pairs:
        failures.append("raw_pixel_near_duplicates_selected")
    if maximum_similarity > config.max_selected_cosine_similarity + 1e-12:
        failures.append("semantic_similarity_cap_exceeded")
    if any(truth_overlap):
        failures.append("reserved_truth_overlap")
    if config.exclude_conservative_quality_failures and selected_quality_failures:
        failures.append("conservative_quality_failure_selected")
    for morphology, count_cap in morphology_count_caps.items():
        if selected_morphologies[morphology] > count_cap:
            failures.append(f"morphology_cap_exceeded:{morphology}")
    manual_rejection_sha256 = _canonical_sha256(
        {
            "schema": "triage-opaque-manual-rejections-v1",
            "audit_ids": sorted(manual_rejections),
        }
    )
    audit = SemanticSelectionAudit(
        temporal=temporal,
        represented_clusters=int(cluster_metrics["represented_clusters"]),
        cluster_coverage=float(cluster_metrics["cluster_coverage"]),
        normalized_cluster_entropy=float(cluster_metrics["normalized_cluster_entropy"]),
        largest_cluster_share=float(cluster_metrics["largest_cluster_share"]),
        source_morphology_counts=dict(sorted(source_morphologies.items())),
        selected_morphology_counts=dict(sorted(selected_morphologies.items())),
        morphology_count_caps=dict(sorted(morphology_count_caps.items())),
        pixel_morphology_sha256=pixel_morphology_sha256,
        source_conservative_quality_failure_count=len(quality_failure_ids),
        excluded_conservative_quality_failure_count=(
            len(quality_failure_ids) if config.exclude_conservative_quality_failures else 0
        ),
        selected_conservative_quality_failure_count=selected_quality_failures,
        total_policy_exclusion_count=len(
            set(manual_rejections)
            | (quality_failure_ids if config.exclude_conservative_quality_failures else set())
        ),
        source_screen_signature_id_set_sha256=opaque_audit_id_set_sha256(screen_signature_ids),
        source_quality_failure_id_set_sha256=opaque_audit_id_set_sha256(quality_failure_ids),
        selected_screen_signature_id_set_sha256=opaque_audit_id_set_sha256(selected_screen_ids),
        selected_quality_failure_id_set_sha256=opaque_audit_id_set_sha256(selected_quality_ids),
        manual_rejection_count=len(manual_rejections),
        manual_rejection_sha256=manual_rejection_sha256,
        raw_pixel_near_duplicate_rejections=len(near_duplicate_rejections - selected_ids),
        semantic_similarity_rejections=len(similarity_rejections - selected_ids),
        selected_raw_pixel_near_duplicate_pairs=selected_duplicate_pairs,
        maximum_selected_cosine_similarity=maximum_similarity,
        truth_asset_overlap=truth_overlap[0],
        truth_component_overlap=truth_overlap[1],
        truth_moment_overlap=truth_overlap[2],
        failures=tuple(dict.fromkeys(failures)),
    )
    if require_quality and not audit.passed:
        raise SemanticSelectionQualityError(audit)
    selection_sha256 = cohort_selection_sha256(selected_previews)  # type: ignore[arg-type]
    _assert_memory_limit(config.memory_limit_bytes)
    return SemanticSelection(
        selected=selected_tuple,
        selection_sha256=selection_sha256,
        audit=audit,
        pixel_metrics=pixel_metrics,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write_create_only_idempotent(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"existing {path.name} has different immutable content")
        os.chmod(path, 0o600)
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError(f"existing {path.name} has different immutable content")
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _render_review_pages(
    entries: list[tuple[Path, str]],
    *,
    output_dir: Path,
    group: str,
    columns: int = 5,
    rows: int = 4,
) -> list[dict[str, object]]:
    page_size = columns * rows
    tile_width, image_height, caption_height = 256, 192, 40
    pages: list[dict[str, object]] = []
    for page_number, start in enumerate(range(0, len(entries), page_size), start=1):
        page_entries = entries[start : start + page_size]
        sheet = Image.new(
            "RGB",
            (columns * tile_width, rows * (image_height + caption_height)),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for local_index, (image_path, caption) in enumerate(page_entries):
            column = local_index % columns
            row = local_index // columns
            x = column * tile_width
            y = row * (image_height + caption_height)
            with Image.open(image_path) as source:
                image = ImageOps.contain(source.convert("RGB"), (tile_width, image_height))
            sheet.paste(
                image,
                (x + (tile_width - image.width) // 2, y + (image_height - image.height) // 2),
            )
            draw.multiline_text((x + 5, y + image_height + 4), caption, fill="black", spacing=2)
        relative_path = f"{group}-{page_number:02d}.png"
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG", optimize=True)
        payload = buffer.getvalue()
        _write_create_only_idempotent(output_dir / relative_path, payload)
        pages.append(
            {
                "group": group,
                "page": page_number,
                "relative_path": relative_path,
                "tile_count": len(page_entries),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return pages


def _materialize_review_bundle(
    *,
    output_dir: Path,
    clustering: SemanticClustering,
    selection: SemanticSelection,
    final_store: AuthenticatedCandidateStore,
    source_selection_sha256: str,
) -> dict[str, object]:
    _private_directory(output_dir)
    candidate_by_audit = {
        candidate.preview.audit_id: candidate for candidate in clustering.candidates
    }
    final_by_asset = {row.asset_id: row for row in final_store.rows}
    medoid_entries = [
        (
            candidate_by_audit[audit_id].preview.image_path,
            (f"cluster {candidate_by_audit[audit_id].cluster_label:03d} | {audit_id}"),
        )
        for audit_id in clustering.medoid_audit_ids
    ]
    nearest_entries: list[tuple[Path, str]] = []
    pairs = nearest_semantic_pairs(selection.selected, limit=min(32, len(selection.selected)))
    selected_by_audit = {candidate.preview.audit_id: candidate for candidate in selection.selected}
    for pair_number, pair in enumerate(pairs, start=1):
        for side, audit_id in (
            ("A", pair.left_audit_id),
            ("B", pair.right_audit_id),
        ):
            candidate = selected_by_audit[audit_id]
            final = final_by_asset[candidate.preview.asset_id]
            nearest_entries.append(
                (
                    final.image_path,
                    (
                        f"pair {pair_number:02d}{side} | {final.audit_id}\n"
                        f"cos={pair.cosine_similarity:.5f}"
                    ),
                )
            )
    overview_entries = [
        (
            final_by_asset[candidate.preview.asset_id].image_path,
            (
                f"cluster {candidate.cluster_label:03d} | "
                f"{final_by_asset[candidate.preview.asset_id].audit_id}"
            ),
        )
        for candidate in sorted(
            selection.selected,
            key=lambda item: final_by_asset[item.preview.asset_id].audit_id,
        )
    ]
    pages: list[dict[str, object]] = []
    pages.extend(
        _render_review_pages(
            medoid_entries,
            output_dir=output_dir,
            group="semantic-cluster-medoids",
        )
    )
    pages.extend(
        _render_review_pages(
            nearest_entries,
            output_dir=output_dir,
            group="nearest-selected-pairs",
        )
    )
    pages.extend(
        _render_review_pages(
            overview_entries,
            output_dir=output_dir,
            group="selected-overview",
        )
    )
    manifest: dict[str, object] = {
        "schema": "triage-private-semantic-review-v3",
        "blindness": (
            "authenticated pixels, DINO semantics, and opaque audit ids only; "
            "no truth labels, head outputs, captions, or semantic search"
        ),
        "source_selection_sha256": source_selection_sha256,
        "semantic_clustering_sha256": clustering.clustering_sha256,
        "final_selection_sha256": selection.selection_sha256,
        "pages": pages,
    }
    manifest["review_manifest_sha256"] = _canonical_sha256(manifest)
    _write_create_only_idempotent(
        output_dir / "review-manifest.json",
        _canonical_bytes(manifest),
    )
    return manifest


def materialize_semantic_refinement(
    *,
    output_dir: Path,
    universe: SemanticUniverse,
    clustering: SemanticClustering,
    selection: SemanticSelection,
    config: SemanticRefinementConfig,
    rejections: ResolvedOpaqueRejections,
    truth_ledger: TruthReservationLedger,
    truth_ledger_sha256_at_selection: str,
) -> SemanticRefinementArtifacts:
    """Seal a separate v3 evidence tree; the authenticated v2 source stays read-only."""
    if not selection.audit.passed or len(selection.selected) != config.final_target:
        raise ValueError("only a fully passing semantic selection may be materialized")
    if truth_ledger.ledger_sha256() != truth_ledger_sha256_at_selection:
        raise RuntimeError("truth ledger changed after semantic selection")
    source = universe.source
    output_dir = Path(output_dir)
    source_root = source.path.resolve()
    output_root = output_dir.resolve()
    if (
        output_root == source_root
        or output_root.is_relative_to(source_root)
        or source_root.is_relative_to(output_root)
    ):
        raise ValueError("v3 output must be separate from immutable v2 evidence")
    _private_directory(output_dir)
    ordered_embeddings = tuple(sorted(universe.embeddings, key=lambda item: item.preview.audit_id))
    if {item.preview.audit_id for item in ordered_embeddings} != {
        row.audit_id for row in source.rows
    }:
        raise ValueError("semantic universe differs from its authenticated source")
    vector_payload = b"".join(
        np.ascontiguousarray(item.vector, dtype=np.float32).tobytes() for item in ordered_embeddings
    )
    vector_path = output_dir / "semantic-vectors.f32"
    _write_create_only_idempotent(vector_path, vector_payload)
    vector_file_sha256 = hashlib.sha256(vector_payload).hexdigest()
    clustered_by_audit = {
        candidate.preview.audit_id: candidate for candidate in clustering.candidates
    }
    vector_rows = [
        {
            "audit_id": item.preview.audit_id,
            "asset_id": item.preview.asset_id,
            "preview_sha256": item.preview.preview_sha256,
            "vector_sha256": item.vector_sha256,
            "byte_offset": index * SEMANTIC_DIM * np.dtype(np.float32).itemsize,
            "byte_length": SEMANTIC_DIM * np.dtype(np.float32).itemsize,
            "semantic_cluster_label": clustered_by_audit[item.preview.audit_id].cluster_label,
            "semantic_cluster_distance": clustered_by_audit[item.preview.audit_id].cluster_distance,
        }
        for index, item in enumerate(ordered_embeddings)
    ]
    vector_index: dict[str, object] = {
        "schema": "triage-private-semantic-vector-index-v3",
        "source_manifest_sha256": source.manifest_sha256,
        "semantic_matrix_sha256": universe.semantic_matrix_sha256,
        "encoder_key": universe.encoder.key,
        "vector_file": vector_path.name,
        "vector_file_sha256": vector_file_sha256,
        "dtype": "float32-le",
        "dimension": SEMANTIC_DIM,
        "row_count": len(vector_rows),
        "medoid_audit_ids": list(clustering.medoid_audit_ids),
        "rows": vector_rows,
    }
    vector_index["private_vector_index_sha256"] = _canonical_sha256(vector_index)
    vector_index_bytes = _canonical_bytes(vector_index)
    vector_index_path = output_dir / "semantic-vectors-private-index.json"
    _write_create_only_idempotent(vector_index_path, vector_index_bytes)
    vector_index_file_sha256 = hashlib.sha256(vector_index_bytes).hexdigest()

    selected_by_asset = {candidate.preview.asset_id: candidate for candidate in selection.selected}
    final_store_path = output_dir / "final-previews-semantic-v3"
    final_manifest = pin_preview_store(
        final_store_path,
        [candidate.preview for candidate in selection.selected],
        cohort_name="fresh-location-certification-semantic-provisional-v3",
        inventory_sha256=source.inventory_sha256,
        selection_sha256=selection.selection_sha256,
        fetch_preview=lambda asset_id: selected_by_asset[asset_id].preview.image_path.read_bytes(),
        audit_prefix="S",
        workers=1,
        memory_limit_bytes=config.memory_limit_bytes,
    )
    final_store = load_authenticated_candidate_store(
        final_store_path,
        expected_count=config.final_target,
    )
    final_by_asset = {row.asset_id: row for row in final_store.rows}
    review_manifest = _materialize_review_bundle(
        output_dir=output_dir / "blind-review-v3",
        clustering=clustering,
        selection=selection,
        final_store=final_store,
        source_selection_sha256=source.selection_sha256,
    )
    embedding_by_audit = {item.preview.audit_id: item for item in ordered_embeddings}
    private_rows: list[dict[str, object]] = []
    for candidate in sorted(selection.selected, key=lambda item: item.preview.audit_id):
        preview = candidate.preview
        metrics = selection.pixel_metrics[preview.audit_id]
        private_rows.append(
            {
                "asset_id": preview.asset_id,
                "source_audit_id": preview.audit_id,
                "final_audit_id": final_by_asset[preview.asset_id].audit_id,
                "component_key": preview.component_key,
                "moment_key": preview.moment_key,
                "capture_day": preview.capture_day,
                "stratum": preview.stratum,
                "preview_sha256": preview.preview_sha256,
                "semantic_vector_sha256": embedding_by_audit[preview.audit_id].vector_sha256,
                "semantic_cluster_label": candidate.cluster_label,
                "semantic_cluster_distance": candidate.cluster_distance,
                "aspect_morphology": preview.morphology,
                "near_white_fraction": metrics.near_white_fraction,
                "near_black_fraction": metrics.near_black_fraction,
                "edge_fraction": metrics.edge_fraction,
                "laplacian_variance": metrics.laplacian_variance,
                "mean_saturation": metrics.mean_saturation,
                "screen_signature": metrics.screen_signature,
                "conservative_quality_failure": metrics.conservative_quality_failure,
            }
        )
    private_selection: dict[str, object] = {
        "schema": "triage-private-semantic-refinement-v3",
        "source": {
            "inventory_sha256": source.inventory_sha256,
            "selection_sha256": source.selection_sha256,
            "manifest_sha256": source.manifest_sha256,
            "private_index_sha256": source.private_index_sha256,
            "selection_lock_sha256": source.selection_lock_sha256,
        },
        "truth_ledger_sha256_at_selection": truth_ledger_sha256_at_selection,
        "config": config.public_dict(),
        "selection_seed": config.seed,
        "encoder_key": universe.encoder.key,
        "semantic_matrix_sha256": universe.semantic_matrix_sha256,
        "semantic_clustering_sha256": clustering.clustering_sha256,
        "pixel_morphology_sha256": selection.audit.pixel_morphology_sha256,
        "manual_rejections": {
            **rejections.public_dict(),
            "resolved_candidate_audit_ids": sorted(
                # Only opaque ids are persisted; asset identifiers never enter this list.
                rejections.candidate_audit_ids
            ),
        },
        "selection_sha256": selection.selection_sha256,
        "vector_file_sha256": vector_file_sha256,
        "vector_index_file_sha256": vector_index_file_sha256,
        "final_preview_manifest_sha256": final_manifest["manifest_sha256"],
        "review_manifest_sha256": review_manifest["review_manifest_sha256"],
        "selected_rows": private_rows,
    }
    private_selection["private_selection_sha256"] = _canonical_sha256(private_selection)
    private_selection_bytes = _canonical_bytes(private_selection)
    private_selection_path = output_dir / "private-selection.json"
    _write_create_only_idempotent(private_selection_path, private_selection_bytes)
    private_selection_file_sha256 = hashlib.sha256(private_selection_bytes).hexdigest()

    if truth_ledger.ledger_sha256() != truth_ledger_sha256_at_selection:
        raise RuntimeError("truth ledger changed before semantic evidence commit")
    public_manifest: dict[str, object] = {
        "schema": "triage-semantic-refinement-public-v3",
        "status": "pending_private_visual_inspection_not_truth_reserved",
        "privacy": "aggregate provenance only; asset mappings remain in mode-0600 indices",
        "blindness": ("no truth labels, triage-head outputs, captions, or semantic-search results"),
        "source": {
            "inventory_sha256": source.inventory_sha256,
            "selection_sha256": source.selection_sha256,
            "manifest_sha256": source.manifest_sha256,
            "private_index_sha256": source.private_index_sha256,
            "selection_lock_sha256": source.selection_lock_sha256,
            "candidate_count": len(source.rows),
        },
        "truth": {
            "ledger_sha256_at_selection": truth_ledger_sha256_at_selection,
            "reservation_performed": False,
        },
        "encoder": {
            "encoder_id": universe.encoder.encoder_id,
            "weights_sha256": universe.encoder.weights_sha256,
            "preprocess_version": universe.encoder.preprocess_version,
            "semantic_layout_version": universe.encoder.layout_version,
            "encoder_key": universe.encoder.key,
        },
        "config": config.public_dict(),
        "semantic_universe": {
            "row_count": len(universe.embeddings),
            "dimension": SEMANTIC_DIM,
            "semantic_matrix_sha256": universe.semantic_matrix_sha256,
        },
        "clustering": {
            "clustering_sha256": clustering.clustering_sha256,
            "cluster_count": clustering.cluster_count,
            "coverage": clustering.coverage,
            "normalized_entropy": clustering.normalized_entropy,
            "largest_cluster_share": clustering.largest_cluster_share,
        },
        "manual_rejections": rejections.public_dict(),
        "final_selection": {
            "selected_count": len(selection.selected),
            "selection_sha256": selection.selection_sha256,
            "audit": selection.audit.public_dict(),
        },
        "final_preview_store": {
            "relative_path": final_store_path.name,
            "row_count": final_manifest["row_count"],
            "manifest_sha256": final_manifest["manifest_sha256"],
            "private_index_sha256": final_manifest["private_index_sha256"],
        },
        "private_artifacts": {
            "vector_file_sha256": vector_file_sha256,
            "vector_index_file_sha256": vector_index_file_sha256,
            "private_selection_file_sha256": private_selection_file_sha256,
            "private_selection_sha256": private_selection["private_selection_sha256"],
            "review_manifest_sha256": review_manifest["review_manifest_sha256"],
            "review_page_count": len(review_manifest["pages"]),
            "review_manifest_relative_path": "blind-review-v3/review-manifest.json",
        },
    }
    public_manifest["run_sha256"] = _canonical_sha256(public_manifest)
    _write_create_only_idempotent(
        output_dir / "semantic-refinement-public.json",
        _canonical_bytes(public_manifest),
    )
    _fsync_directory(output_dir)
    _assert_memory_limit(config.memory_limit_bytes)
    return SemanticRefinementArtifacts(
        output_dir=output_dir,
        public_manifest=public_manifest,
    )


def _config_from_sealed_payloads(
    public: Mapping[str, object],
    private: Mapping[str, object],
) -> SemanticRefinementConfig:
    raw = public.get("config")
    if not isinstance(raw, Mapping):
        raise ValueError("semantic public manifest has no config")
    raw_caps = raw.get("morphology_caps")
    if not isinstance(raw_caps, list):
        raise ValueError("semantic morphology caps are malformed")
    caps = tuple(
        MorphologyPrevalenceCap(
            morphology=str(cap["morphology"]),
            prevalence_multiplier=float(cap["prevalence_multiplier"]),
        )
        for cap in raw_caps
        if isinstance(cap, Mapping)
    )
    if len(caps) != len(raw_caps):
        raise ValueError("semantic morphology cap entry is malformed")
    config = SemanticRefinementConfig(
        seed=str(private.get("selection_seed", "")),
        candidate_count=int(raw["candidate_count"]),
        final_target=int(raw["final_target"]),
        cluster_count=int(raw["cluster_count"]),
        max_per_day=int(raw["max_per_day"]),
        max_per_moment=int(raw["max_per_moment"]),
        min_year_quarter_coverage=float(raw["min_year_quarter_coverage"]),
        max_sqrt_quota_tv=float(raw["max_sqrt_quota_tv"]),
        min_cluster_coverage=float(raw["min_cluster_coverage"]),
        min_cluster_entropy=float(raw["min_cluster_entropy"]),
        max_selected_cosine_similarity=float(raw["max_selected_cosine_similarity"]),
        morphology_caps=caps,
        exclude_conservative_quality_failures=bool(raw["exclude_conservative_quality_failures"]),
        memory_limit_bytes=int(raw["memory_limit_bytes"]),
    )
    if config.public_dict() != dict(raw):
        raise ValueError("semantic config does not reproduce from its private seed")
    return config


def load_authenticated_semantic_refinement(
    output_dir: Path,
    *,
    source_candidate_store: Path,
    truth_ledger: TruthReservationLedger,
    exclude_truth_cohort: str | None = None,
) -> AuthenticatedSemanticRefinement:
    """Authenticate and independently replay every v3 selection gate.

    ``exclude_truth_cohort`` is only for an exact retry after that named cohort
    was atomically reserved; the ledger exposes the corresponding pre-commit
    digest and blocklist view.
    """
    output_dir = Path(output_dir)
    public_path = output_dir / "semantic-refinement-public.json"
    private_path = output_dir / "private-selection.json"
    vector_path = output_dir / "semantic-vectors.f32"
    vector_index_path = output_dir / "semantic-vectors-private-index.json"
    for path in (public_path, private_path, vector_path, vector_index_path):
        if not path.is_file():
            raise ValueError(f"semantic refinement is missing {path.name}")
    for path in (private_path, vector_path, vector_index_path):
        if path.stat().st_mode & 0o077:
            raise PermissionError(f"semantic private artifact {path.name} must be mode 0600")

    public = json.loads(public_path.read_text(encoding="utf-8"))
    private_bytes = private_path.read_bytes()
    private = json.loads(private_bytes)
    vector_index_bytes = vector_index_path.read_bytes()
    vector_index = json.loads(vector_index_bytes)
    if public.get("schema") != "triage-semantic-refinement-public-v3":
        raise ValueError("semantic public manifest has an unsupported schema")
    if private.get("schema") != "triage-private-semantic-refinement-v3":
        raise ValueError("semantic private selection has an unsupported schema")
    if vector_index.get("schema") != "triage-private-semantic-vector-index-v3":
        raise ValueError("semantic vector index has an unsupported schema")
    _verify_self_digest(public, "run_sha256", what="semantic public manifest")
    _verify_self_digest(private, "private_selection_sha256", what="semantic private selection")
    _verify_self_digest(
        vector_index,
        "private_vector_index_sha256",
        what="semantic vector index",
    )
    private_file_sha256 = hashlib.sha256(private_bytes).hexdigest()
    vector_index_file_sha256 = hashlib.sha256(vector_index_bytes).hexdigest()
    vector_payload = vector_path.read_bytes()
    vector_file_sha256 = hashlib.sha256(vector_payload).hexdigest()
    public_private = public.get("private_artifacts")
    if not isinstance(public_private, Mapping):
        raise ValueError("semantic public private-artifact bindings are malformed")
    if public_private.get("private_selection_file_sha256") != private_file_sha256:
        raise ValueError("semantic private selection file digest does not reproduce")
    if public_private.get("vector_index_file_sha256") != vector_index_file_sha256:
        raise ValueError("semantic vector index file digest does not reproduce")
    if public_private.get("vector_file_sha256") != vector_file_sha256:
        raise ValueError("semantic vector file digest does not reproduce")
    if vector_index.get("vector_file_sha256") != vector_file_sha256:
        raise ValueError("semantic vector payload differs from its private index")

    config = _config_from_sealed_payloads(public, private)
    source = load_authenticated_candidate_store(
        source_candidate_store,
        expected_count=config.candidate_count,
    )
    sealed_source = private.get("source")
    public_source = public.get("source")
    if not isinstance(sealed_source, Mapping) or not isinstance(public_source, Mapping):
        raise ValueError("semantic source bindings are malformed")
    expected_source = {
        "inventory_sha256": source.inventory_sha256,
        "selection_sha256": source.selection_sha256,
        "manifest_sha256": source.manifest_sha256,
        "private_index_sha256": source.private_index_sha256,
        "selection_lock_sha256": source.selection_lock_sha256,
    }
    if dict(sealed_source) != expected_source:
        raise ValueError("semantic private source binding differs from authenticated v2")
    if any(public_source.get(key) != value for key, value in expected_source.items()):
        raise ValueError("semantic public source binding differs from authenticated v2")

    selection_ledger_sha256 = str(private.get("truth_ledger_sha256_at_selection", ""))
    if public.get("truth", {}).get("ledger_sha256_at_selection") != selection_ledger_sha256:  # type: ignore[union-attr]
        raise ValueError("semantic truth-ledger bindings disagree")
    current_view_sha256 = truth_ledger.ledger_sha256(exclude_cohort=exclude_truth_cohort)
    if current_view_sha256 != selection_ledger_sha256:
        raise ValueError("truth ledger no longer matches the semantic selection-time view")
    blocked = truth_ledger.blocklist(exclude_cohort=exclude_truth_cohort)

    encoder_raw = public.get("encoder")
    if not isinstance(encoder_raw, Mapping):
        raise ValueError("semantic encoder provenance is malformed")
    encoder = SemanticEncoderIdentity(
        encoder_id=str(encoder_raw["encoder_id"]),
        weights_sha256=str(encoder_raw["weights_sha256"]),
        preprocess_version=str(encoder_raw["preprocess_version"]),
        layout_version=str(encoder_raw["semantic_layout_version"]),
    )
    if (
        encoder.encoder_id != f"{MODEL_ID}@{MODEL_REVISION}"
        or encoder.weights_sha256 != EXPECTED_ONNX_SHA256
        or encoder.preprocess_version != PREPROCESS_VERSION
        or encoder.layout_version != SEMANTIC_LAYOUT_VERSION
        or encoder.key != encoder_raw.get("encoder_key")
    ):
        raise ValueError("semantic encoder differs from the pinned identity")

    raw_vector_rows = vector_index.get("rows")
    if not isinstance(raw_vector_rows, list) or len(raw_vector_rows) != config.candidate_count:
        raise ValueError("semantic vector index row count is wrong")
    source_by_audit = {row.audit_id: row for row in source.rows}
    embeddings: list[SemanticEmbedding] = []
    clustered_candidates: list[ClusteredSemanticCandidate] = []
    row_width = SEMANTIC_DIM * np.dtype(np.float32).itemsize
    seen_audits: set[str] = set()
    for index, raw in enumerate(raw_vector_rows):
        if not isinstance(raw, Mapping):
            raise ValueError("semantic vector index contains a malformed row")
        audit_id = str(raw["audit_id"])
        preview = source_by_audit.get(audit_id)
        if preview is None or audit_id in seen_audits:
            raise ValueError("semantic vector index differs from its source universe")
        if (
            raw.get("asset_id") != preview.asset_id
            or raw.get("preview_sha256") != preview.preview_sha256
        ):
            raise ValueError("semantic vector row differs from its authenticated preview")
        offset = int(raw["byte_offset"])
        length = int(raw["byte_length"])
        if offset != index * row_width or length != row_width:
            raise ValueError("semantic vector index has a non-canonical byte layout")
        vector = np.frombuffer(vector_payload[offset : offset + length], dtype="<f4").copy()
        if vector.shape != (SEMANTIC_DIM,):
            raise ValueError("semantic vector payload is truncated")
        vector_sha256 = hashlib.sha256(vector.astype(np.float32).tobytes()).hexdigest()
        if vector_sha256 != raw.get("vector_sha256"):
            raise ValueError("semantic vector row digest does not reproduce")
        embedding = SemanticEmbedding(
            preview=preview,
            vector=vector.astype(np.float32, copy=False),
            vector_sha256=vector_sha256,
        )
        embeddings.append(embedding)
        clustered_candidates.append(
            ClusteredSemanticCandidate(
                embedding=embedding,
                cluster_label=int(raw["semantic_cluster_label"]),
                cluster_distance=float(raw["semantic_cluster_distance"]),
            )
        )
        seen_audits.add(audit_id)
    if len(vector_payload) != config.candidate_count * row_width:
        raise ValueError("semantic vector payload has trailing or missing bytes")
    matrix_digest = semantic_matrix_sha256(source, encoder, embeddings)
    if matrix_digest != private.get("semantic_matrix_sha256") or matrix_digest != vector_index.get(
        "semantic_matrix_sha256"
    ):
        raise ValueError("semantic matrix digest does not reproduce")
    public_universe = public.get("semantic_universe")
    if (
        not isinstance(public_universe, Mapping)
        or public_universe.get("semantic_matrix_sha256") != matrix_digest
    ):
        raise ValueError("semantic public matrix binding does not reproduce")
    universe = SemanticUniverse(
        source=source,
        encoder=encoder,
        embeddings=tuple(embeddings),
        semantic_matrix_sha256=matrix_digest,
    )

    clustering_digest = semantic_clustering_sha256(
        clustered_candidates,
        cluster_count=config.cluster_count,
        seed=config.seed,
    )
    if clustering_digest != private.get("semantic_clustering_sha256"):
        raise ValueError("semantic clustering digest does not reproduce")
    counts = Counter(candidate.cluster_label for candidate in clustered_candidates)
    probabilities = np.asarray(list(counts.values()), dtype=np.float64) / len(clustered_candidates)
    entropy = float(-(probabilities * np.log(probabilities)).sum() / math.log(config.cluster_count))
    medoid_ids = tuple(str(value) for value in vector_index.get("medoid_audit_ids", []))
    if len(medoid_ids) != len(counts) or any(value not in seen_audits for value in medoid_ids):
        raise ValueError("semantic medoid index is malformed")
    clustering = SemanticClustering(
        candidates=tuple(clustered_candidates),
        cluster_count=config.cluster_count,
        normalized_entropy=entropy,
        coverage=len(counts) / config.cluster_count,
        largest_cluster_share=max(counts.values()) / len(clustered_candidates),
        medoid_audit_ids=medoid_ids,
        clustering_sha256=clustering_digest,
    )
    public_clustering = public.get("clustering")
    if (
        not isinstance(public_clustering, Mapping)
        or public_clustering.get("clustering_sha256") != clustering_digest
    ):
        raise ValueError("semantic public clustering binding does not reproduce")

    private_rejections = private.get("manual_rejections")
    if not isinstance(private_rejections, Mapping):
        raise ValueError("semantic private rejection list is malformed")
    raw_resolved = private_rejections.get("resolved_candidate_audit_ids")
    if not isinstance(raw_resolved, list):
        raise ValueError("semantic resolved rejection list is malformed")
    resolved_rejections = frozenset(str(value) for value in raw_resolved)
    selection = select_semantic_cohort(
        clustering.candidates,
        source=source,
        config=config,
        manual_rejections=resolved_rejections,
        blocked=blocked,
    )
    public_selection = public.get("final_selection")
    if not isinstance(public_selection, Mapping):
        raise ValueError("semantic public selection binding is malformed")
    if (
        selection.selection_sha256 != private.get("selection_sha256")
        or selection.selection_sha256 != public_selection.get("selection_sha256")
        or selection.audit.public_dict() != public_selection.get("audit")
    ):
        raise ValueError("semantic selection or independently replayed audit differs")
    private_rows = private.get("selected_rows")
    if not isinstance(private_rows, list):
        raise ValueError("semantic private selected rows are malformed")
    if {str(row.get("source_audit_id")) for row in private_rows if isinstance(row, Mapping)} != {
        candidate.preview.audit_id for candidate in selection.selected
    }:
        raise ValueError("semantic private selected rows differ from replay")

    final_path = output_dir / "final-previews-semantic-v3"
    final_store = load_authenticated_candidate_store(
        final_path,
        expected_count=config.final_target,
    )
    if (
        final_store.inventory_sha256 != source.inventory_sha256
        or final_store.selection_sha256 != selection.selection_sha256
    ):
        raise ValueError("semantic final preview store has different lineage")
    final_manifest = json.loads((final_path / "manifest.json").read_text(encoding="utf-8"))
    public_final = public.get("final_preview_store")
    if not isinstance(public_final, Mapping) or (
        public_final.get("manifest_sha256") != final_manifest.get("manifest_sha256")
        or public_final.get("private_index_sha256") != final_store.private_index_sha256
    ):
        raise ValueError("semantic final preview store digest binding differs")
    final_by_asset = {row.asset_id: row for row in final_store.rows}
    for raw in private_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("semantic private selected row is malformed")
        final = final_by_asset.get(str(raw.get("asset_id")))
        if final is None or (
            raw.get("final_audit_id") != final.audit_id
            or raw.get("preview_sha256") != final.preview_sha256
        ):
            raise ValueError("semantic private selection differs from final preview store")

    review_path = output_dir / "blind-review-v3" / "review-manifest.json"
    if not review_path.is_file() or review_path.stat().st_mode & 0o077:
        raise ValueError("semantic review manifest is missing or not private")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review_sha256 = _verify_self_digest(
        review,
        "review_manifest_sha256",
        what="semantic review manifest",
    )
    if (
        review_sha256 != private.get("review_manifest_sha256")
        or review_sha256 != public_private.get("review_manifest_sha256")
        or review.get("semantic_clustering_sha256") != clustering.clustering_sha256
        or review.get("final_selection_sha256") != selection.selection_sha256
        or review.get("source_selection_sha256") != source.selection_sha256
    ):
        raise ValueError("semantic review lineage binding differs")
    raw_pages = review.get("pages")
    if not isinstance(raw_pages, list):
        raise ValueError("semantic review page index is malformed")
    review_pages: list[tuple[str, str]] = []
    seen_review_paths: set[str] = set()
    review_root = review_path.parent.resolve()
    for raw in raw_pages:
        if not isinstance(raw, Mapping):
            raise ValueError("semantic review page entry is malformed")
        relative = str(raw.get("relative_path", ""))
        expected_sha256 = str(raw.get("sha256", ""))
        page_path = (review_path.parent / relative).resolve()
        if (
            not page_path.is_relative_to(review_root)
            or not page_path.is_file()
            or relative in seen_review_paths
        ):
            raise ValueError("semantic review page path is unsafe or missing")
        if _file_sha256(page_path) != expected_sha256:
            raise ValueError("semantic review page digest does not reproduce")
        review_pages.append((relative, expected_sha256))
        seen_review_paths.add(relative)
    if len(review_pages) != public_private.get("review_page_count"):
        raise ValueError("semantic review page count differs from public manifest")
    return AuthenticatedSemanticRefinement(
        output_dir=output_dir,
        public_manifest=public,
        universe=universe,
        clustering=clustering,
        selection=selection,
        config=config,
        final_store=final_store,
        review_pages=tuple(review_pages),
        review_manifest_sha256=review_sha256,
    )


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate-store",
        type=Path,
        required=True,
        help="immutable exact-2k candidate-previews-v2 directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new semantic-refinement-v3 evidence directory",
    )
    parser.add_argument(
        "--truth-ledger",
        type=Path,
        required=True,
        help="append-only truth reservation SQLite ledger (read-only in this command)",
    )
    parser.add_argument(
        "--onnx",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR / "dinov2-small-ed25f3a" / "model.onnx",
        help="pinned DINOv2-small ONNX export",
    )
    parser.add_argument(
        "--prior-private-selection",
        type=Path,
        help="authenticated v2 private-selection.json used to resolve F-style audit ids",
    )
    parser.add_argument(
        "--reject-audit-id",
        action="append",
        default=[],
        help="opaque U-style candidate or F-style prior-final audit id; repeat as needed",
    )
    parser.add_argument("--provider", choices=("cpu", "coreml", "auto"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--memory-limit-gib", type=float, default=8.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    _pipeline_lock = acquire_recovery_pipeline_lock()
    memory_limit_bytes = int(args.memory_limit_gib * 1024**3)
    config = SemanticRefinementConfig(memory_limit_bytes=memory_limit_bytes)
    source = load_authenticated_candidate_store(
        args.candidate_store,
        expected_count=config.candidate_count,
    )
    ledger = TruthReservationLedger(args.truth_ledger)
    ledger_sha256 = ledger.ledger_sha256()
    rejections = resolve_opaque_rejections(
        source,
        frozenset(str(value) for value in args.reject_audit_id),
        prior_private_selection=args.prior_private_selection,
    )
    verified_digest = verify_onnx_artifact(args.onnx)
    session = create_onnx_session(args.onnx, args.provider)
    universe = embed_authenticated_candidate_store(
        source,
        session=session,
        verified_onnx_sha256=verified_digest,
        batch_size=args.batch_size,
        memory_limit_bytes=config.memory_limit_bytes,
    )
    clustering = cluster_semantic_embeddings(
        universe.embeddings,
        cluster_count=config.cluster_count,
        seed=config.seed,
        memory_limit_bytes=config.memory_limit_bytes,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed during semantic embedding")
    selection = select_semantic_cohort(
        clustering.candidates,
        source=source,
        config=config,
        manual_rejections=rejections.candidate_audit_ids,
        blocked=ledger.blocklist(),
    )
    artifacts = materialize_semantic_refinement(
        output_dir=args.output_dir,
        universe=universe,
        clustering=clustering,
        selection=selection,
        config=config,
        rejections=rejections,
        truth_ledger=ledger,
        truth_ledger_sha256_at_selection=ledger_sha256,
    )
    print(json.dumps(artifacts.public_manifest, indent=2, sort_keys=True))
    return 0


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    values = np.asarray(vectors, dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("semantic source contains non-finite values")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= np.finfo(np.float32).tiny):
        raise ValueError("semantic source contains a zero-length vector")
    return np.divide(values, norms, dtype=np.float32)


def semantic_vectors(tokens_or_packs: np.ndarray) -> np.ndarray:
    """Derive unit semantic vectors from full tokens or six-part token packs.

    CLS and the global patch mean are normalized independently, concatenated,
    and scaled by ``sqrt(2)``.  The four quadrant pools stay available for the
    downstream heads but do not make this label-blind audit needlessly large.
    """
    values = np.asarray(tokens_or_packs, dtype=np.float32)
    if values.ndim == 2 and values.shape == (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM):
        values = values[None, ...]
    if values.ndim == 3:
        packs = pool_token_pack(values)
    elif values.ndim == 1 and values.shape == (PACK_DIM,):
        packs = values[None, ...]
    elif values.ndim == 2 and values.shape[1] == PACK_DIM:
        packs = values
    else:
        raise ValueError("expected DINO tokens [batch, 257, 384] or pooled packs [batch, 2304]")
    cls = _normalize_rows(packs[:, :TOKEN_DIM])
    global_mean = _normalize_rows(packs[:, TOKEN_DIM : 2 * TOKEN_DIM])
    semantic = np.concatenate((cls, global_mean), axis=1) / math.sqrt(2.0)
    return np.ascontiguousarray(semantic, dtype=np.float32)


if __name__ == "__main__":
    raise SystemExit(main())
