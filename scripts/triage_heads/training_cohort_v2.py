"""Build owner-training cohort v2 from the sealed v1 acquisition pixels.

The v2 lane never contacts Immich. Its first safety boundary is an exact,
label-blind cosine-neighborhood filter over the frozen 112-D raw-pixel
descriptor used by the visual review.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from . import training_cohort as _v1
from .cohorts import (
    CohortSelection,
    InventoryAsset,
    InventorySnapshot,
    TruthReservationLedger,
    cohort_selection_sha256,
)
from .image_diversity import (
    DESCRIPTOR_DIM,
    DESCRIPTOR_VERSION,
    VisualClustering,
    cluster_descriptors,
    describe_preview_bytes,
)
from .preview_store import pin_preview_store, verify_committed_preview_store
from .semantic_refinement import PIXEL_MORPHOLOGY_VERSION, describe_pixel_morphology
from .training_cohort import (
    SYSTEM_MEMORY_CEILING_BYTES,
    TrainingCandidate,
    TrainingCohortConfig,
    TrainingSelection,
)

COSINE_DUPLICATE_THRESHOLD: Final = 0.99
COSINE_RADIUS_BATCH_SIZE: Final = 128
COSINE_DUPLICATE_ALGORITHM: Final = "exact-normalized-ball-tree-independent-set-v1"
GLOBAL_NEAREST_ALGORITHM: Final = "exact-normalized-ball-tree-nearest-v1"
_NEAREST_AUDIT_DOMAIN = b"triage-owner-training-global-nearest-audit-v2\0"
_V2_RUN_DOMAIN = b"triage-owner-training-cohort-v2\0"
_V2_REVIEW_DOMAIN = b"triage-owner-training-review-v2\0"
_VISUAL_APPROVAL_DOMAIN = b"triage-owner-training-visual-review-approval-v2\0"
_V2_DESCRIPTOR_DOMAIN = b"triage-owner-training-descriptors-v2\0"
_V2_BINDING_DOMAIN = b"triage-owner-training-preview-descriptor-binding-v2\0"
_V2_RETENTION_DOMAIN = b"triage-owner-training-retention-audit-v2\0"
_V1_RUN_DOMAIN = b"triage-owner-training-cohort-v1\0"
_V1_REVIEW_DOMAIN = b"triage-owner-training-review-v1\0"
_V1_DESCRIPTOR_DOMAIN = b"triage-owner-training-descriptors-v1\0"
V2_COHORT_NAME: Final = "location-training-v2"
V2_FINAL_STORE_NAME: Final = "training-previews-v2"
V2_DESCRIPTOR_AUDIT_FILENAME: Final = "descriptor-audit.json"
V2_PRIVATE_SELECTION_FILENAME: Final = "private-selection.json"
VISUAL_REVIEW_DECISION_INPUT_SCHEMA: Final = "triage-owner-training-visual-review-decision-input-v1"
V2_VISUAL_REVIEW_DECISION_INPUT_SCHEMA: Final = (
    "triage-owner-training-visual-review-decision-input-v2"
)
VISUAL_REVIEW_APPROVAL_SCHEMA: Final = "triage-owner-training-visual-review-approval-v2"
VISUAL_REVIEW_APPROVAL_FILENAME: Final = "visual-review-approval.json"


@dataclass(frozen=True, slots=True)
class CosineDuplicateFamilyResult:
    retained: tuple[TrainingCandidate, ...]
    rejected_count: int
    threshold: float
    radius_query_count: int
    radius_neighbor_edge_count: int
    max_batch_neighbor_count: int


@dataclass(frozen=True, slots=True)
class V2CandidatePool:
    quality_passed: tuple[TrainingCandidate, ...]
    legacy: _v1.DeduplicationResult
    cosine: CosineDuplicateFamilyResult
    source_quality_failure_count: int


@dataclass(frozen=True, slots=True)
class GlobalNearestSimilarityAudit:
    selected_count: int
    threshold: float
    max_nearest_similarity: float
    nearest_pair_audit_ids: tuple[str, str] | None
    nearest_pair_sha256: str
    descriptor_set_sha256: str
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "triage-owner-training-global-nearest-audit-v2",
            "algorithm": GLOBAL_NEAREST_ALGORITHM,
            "selected_count": self.selected_count,
            "threshold": self.threshold,
            "comparison": "strictly_below",
            "max_nearest_similarity": self.max_nearest_similarity,
            "nearest_pair_sha256": self.nearest_pair_sha256,
            "descriptor_set_sha256": self.descriptor_set_sha256,
            "passed": self.passed,
            "failures": list(self.failures),
        }
        payload["nearest_audit_sha256"] = _canonical_sha256(
            _NEAREST_AUDIT_DOMAIN,
            payload,
        )
        return payload


@dataclass(frozen=True, slots=True)
class SourceV1AcquisitionEvidence:
    source_root: Path
    acquisition_store: Path
    acquisition_rows: tuple[InventoryAsset, ...]
    available_private_rows: Mapping[str, Mapping[str, object]]
    source_descriptor_rows: Mapping[str, Mapping[str, object]]
    source_run_sha256: str
    source_review_manifest_sha256: str
    source_private_selection_sha256: str
    source_descriptor_sha256: str
    source_acquisition_manifest_sha256: str
    source_acquisition_private_index_sha256: str
    source_acquisition_selection_lock_sha256: str
    source_rejection_decision_sha256: str
    acquisition_selection_sha256: str
    ledger_sha256: str
    fresh_truth_selection_sha256: str
    fresh_truth_approval: Mapping[str, object]
    attempted_count: int
    available_count: int
    unavailable_count: int
    error_counts: Mapping[str, int]
    source_file_seals: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _CandidateScan:
    candidates: tuple[TrainingCandidate, ...]
    clustering: VisualClustering
    descriptor_sha256: str
    descriptor_bytes: bytes


@dataclass(frozen=True, slots=True)
class TrainingCohortV2Run:
    selection: TrainingSelection
    duplicate_filter: CosineDuplicateFamilyResult
    nearest_audit: GlobalNearestSimilarityAudit
    public_manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class V2RetentionAudit:
    source_run_sha256: str
    acquisition_selection_sha256: str
    source_descriptor_sha256: str
    v2_descriptor_sha256: str
    source_available_count: int
    source_quality_failure_count: int
    quality_pass_count: int
    legacy_duplicate_rejected_count: int
    legacy_retained_count: int
    cosine_duplicate_rejected_count: int
    cosine_retained_count: int
    cosine_threshold: float
    retained_max_nearest_similarity: float
    target_size: int

    @property
    def target_reachable(self) -> bool:
        """Only assert raw retained capacity; final hard gates still run in build."""
        return self.cosine_retained_count >= self.target_size

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "triage-owner-training-retention-audit-v2",
            "source_run_sha256": self.source_run_sha256,
            "acquisition_selection_sha256": self.acquisition_selection_sha256,
            "source_descriptor_sha256": self.source_descriptor_sha256,
            "v2_descriptor_sha256": self.v2_descriptor_sha256,
            "source_available_count": self.source_available_count,
            "source_quality_failure_count": self.source_quality_failure_count,
            "quality_pass_count": self.quality_pass_count,
            "legacy_duplicate_rejected_count": self.legacy_duplicate_rejected_count,
            "legacy_retained_count": self.legacy_retained_count,
            "cosine_duplicate_rejected_count": self.cosine_duplicate_rejected_count,
            "cosine_retained_count": self.cosine_retained_count,
            "cosine_threshold": self.cosine_threshold,
            "retained_max_nearest_similarity": self.retained_max_nearest_similarity,
            "target_size": self.target_size,
            "target_reachable_before_final_hard_gates": self.target_reachable,
        }
        payload["retention_audit_sha256"] = _canonical_sha256(
            _V2_RETENTION_DOMAIN,
            payload,
        )
        return payload


def _stable_rank(seed: str, scope: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{value}".encode()).hexdigest()


def _canonical_sha256(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + _canonical_bytes(payload)).hexdigest()


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
    ).encode()


def _file_sha256(path: Path) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _private_snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_private_bytes(path: Path, *, label: str) -> bytes:
    """Read one immutable private file through a no-follow descriptor snapshot."""
    candidate = Path(path)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - macOS/Linux both provide it
        raise RuntimeError(f"{label} cannot be read safely on this platform")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise RuntimeError(f"{label} must be a regular local file") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{label} must be a regular local file")
        if stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1:
            raise PermissionError(f"{label} must be single-link mode 0600")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(descriptor)
        if _private_snapshot(before) != _private_snapshot(after):
            raise RuntimeError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)
    try:
        current = candidate.lstat()
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} changed while it was read") from error
    if (
        not stat.S_ISREG(current.st_mode)
        or stat.S_IMODE(current.st_mode) != 0o600
        or current.st_nlink != 1
        or _private_snapshot(current) != _private_snapshot(after)
    ):
        raise RuntimeError(f"{label} changed while it was read")
    return raw


def _load_private_json(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    raw = _read_private_bytes(Path(path), label=label)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON") from error
    if not isinstance(payload, dict) or raw != _canonical_bytes(payload):
        raise RuntimeError(f"{label} must be a canonical JSON object")
    return payload, raw


def _verify_self_digest(
    payload: dict[str, object],
    *,
    field: str,
    domain: bytes,
    label: str,
) -> str:
    claimed = payload.get(field)
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if not _is_sha256(claimed) or _canonical_sha256(domain, unsigned) != claimed:
        raise RuntimeError(f"{label} self-digest does not reproduce")
    return str(claimed)


def _write_create_only(path: Path, payload: bytes) -> None:
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_IMODE(existing.st_mode) != 0o600
            or existing.st_nlink != 1
            or _read_private_bytes(path, label=f"existing {path.name}") != payload
        ):
            raise RuntimeError(f"existing {path.name} differs from immutable approval")
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
            try:
                raced = path.lstat()
            except FileNotFoundError as error:
                raise RuntimeError(
                    f"existing {path.name} vanished during immutable publish"
                ) from error
            if (
                not stat.S_ISREG(raced.st_mode)
                or stat.S_IMODE(raced.st_mode) != 0o600
                or raced.st_nlink != 1
                or _read_private_bytes(path, label=f"existing {path.name}") != payload
            ):
                raise RuntimeError(f"existing {path.name} differs from immutable approval")
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    metadata = path.stat()
    if metadata.st_nlink != 1 or metadata.st_mode & 0o777 != 0o600:
        raise RuntimeError(f"created {path.name} is not a single-link mode-0600 file")


def _normalized_vectors(candidates: tuple[TrainingCandidate, ...]) -> np.ndarray:
    vectors = np.stack(
        [np.asarray(candidate.descriptor.vector, dtype=np.float64) for candidate in candidates]
    ).astype(np.float64, copy=False)
    if vectors.shape != (len(candidates), DESCRIPTOR_DIM) or not np.isfinite(vectors).all():
        raise ValueError("cosine-family descriptors must be finite frozen 112-D vectors")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms <= 0.0):
        raise ValueError("cosine-family descriptors must have positive norms")
    return np.asarray(vectors / norms, dtype=np.float64)


def reject_cosine_duplicate_families(
    candidates: tuple[TrainingCandidate, ...],
    *,
    seed: str,
) -> CosineDuplicateFamilyResult:
    """Return a deterministic maximal subset with every cosine pair below 0.99.

    An exact Euclidean-radius query over normalized vectors is equivalent to a
    cosine query. Batching bounds result memory; stable greedy suppression also
    avoids materializing a quadratic pair matrix or traversing dense rejected
    families repeatedly.
    """
    if not candidates:
        raise ValueError("cosine-family filtering requires at least one candidate")
    asset_ids = [candidate.row.asset_id for candidate in candidates]
    audit_ids = [candidate.audit_id for candidate in candidates]
    if (
        len(set(asset_ids)) != len(asset_ids)
        or len(set(audit_ids)) != len(audit_ids)
        or any(not value for value in (*asset_ids, *audit_ids))
    ):
        raise ValueError("cosine-family candidates require unique nonempty identities")
    ordered = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                _stable_rank(seed, "cosine-family", candidate.audit_id),
                candidate.audit_id,
            ),
        )
    )
    normalized = _normalized_vectors(ordered)
    from sklearn.neighbors import BallTree

    tree = BallTree(normalized, leaf_size=40, metric="euclidean")
    radius = np.nextafter(
        math.sqrt(2.0 - 2.0 * COSINE_DUPLICATE_THRESHOLD),
        math.inf,
    )
    rejected = np.zeros(len(ordered), dtype=bool)
    retained_indices: list[int] = []
    radius_query_count = 0
    neighbor_edge_count = 0
    max_batch_neighbor_count = 0
    for start in range(0, len(ordered), COSINE_RADIUS_BATCH_SIZE):
        stop = min(start + COSINE_RADIUS_BATCH_SIZE, len(ordered))
        neighborhoods = tree.query_radius(
            normalized[start:stop],
            r=radius,
            return_distance=False,
            sort_results=False,
        )
        radius_query_count += stop - start
        batch_neighbor_count = sum(len(neighbors) for neighbors in neighborhoods)
        max_batch_neighbor_count = max(max_batch_neighbor_count, batch_neighbor_count)
        for offset, raw_neighbors in enumerate(neighborhoods):
            index = start + offset
            if rejected[index]:
                continue
            retained_indices.append(index)
            for neighbor_index in raw_neighbors:
                neighbor = int(neighbor_index)
                if neighbor <= index or rejected[neighbor]:
                    continue
                similarity = float(np.dot(normalized[index], normalized[neighbor]))
                if similarity + 1e-12 >= COSINE_DUPLICATE_THRESHOLD:
                    rejected[neighbor] = True
                    neighbor_edge_count += 1
    retained = tuple(ordered[index] for index in retained_indices)
    return CosineDuplicateFamilyResult(
        retained=retained,
        rejected_count=int(rejected.sum()),
        threshold=COSINE_DUPLICATE_THRESHOLD,
        radius_query_count=radius_query_count,
        radius_neighbor_edge_count=neighbor_edge_count,
        max_batch_neighbor_count=max_batch_neighbor_count,
    )


def filter_v2_candidate_pool(
    candidates: tuple[TrainingCandidate, ...],
    *,
    seed: str,
    max_checks_per_candidate: int,
) -> V2CandidatePool:
    """Apply frozen quality exclusion before either duplicate-family filter."""
    source_quality_failure_count = sum(
        bool(
            candidate.pixel_morphology is not None
            and candidate.pixel_morphology.conservative_quality_failure
        )
        for candidate in candidates
    )
    quality_passed = tuple(
        candidate
        for candidate in candidates
        if candidate.pixel_morphology is not None
        and not candidate.pixel_morphology.conservative_quality_failure
    )
    if not quality_passed:
        raise RuntimeError("conservative quality exclusion left no v2 candidates")
    legacy = _v1.reject_near_duplicates(
        quality_passed,
        seed=seed,
        max_checks_per_candidate=max_checks_per_candidate,
    )
    if legacy.bucket_overflow_count:
        raise RuntimeError("raw-pixel duplicate index overflowed its bounded comparison budget")
    cosine = reject_cosine_duplicate_families(legacy.retained, seed=seed)
    return V2CandidatePool(
        quality_passed=quality_passed,
        legacy=legacy,
        cosine=cosine,
        source_quality_failure_count=source_quality_failure_count,
    )


def _authenticate_source_v1_acquisition(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    source_root: Path,
    source_rejection_decision_path: Path,
    config: TrainingCohortConfig,
    fresh_truth_approval_dir: Path | None,
) -> SourceV1AcquisitionEvidence:
    source_root = Path(source_root)
    rejection_sha256 = _authenticate_source_v1_rejection(
        source_root,
        Path(source_rejection_decision_path),
    )
    _v1._require_inventory_binding(snapshot, config)  # noqa: SLF001
    truth_binding = _v1._require_fresh_truth(  # noqa: SLF001
        snapshot,
        ledger,
        approval_dir=fresh_truth_approval_dir,
    )
    current_ledger_sha256 = truth_binding.ledger_sha256
    review_context = _load_v1_review_decision_context(source_root)
    public, _public_bytes = _load_private_json(
        source_root / "training-cohort-public.json",
        label="source v1 cohort manifest",
    )
    private, _private_bytes = _load_private_json(
        source_root / V2_PRIVATE_SELECTION_FILENAME,
        label="source v1 private selection",
    )
    descriptor, _descriptor_bytes = _load_private_json(
        source_root / V2_DESCRIPTOR_AUDIT_FILENAME,
        label="source v1 descriptor audit",
    )
    source_private_sha256 = _verify_self_digest(
        private,
        field="private_selection_sha256",
        domain=_V1_RUN_DOMAIN,
        label="source v1 private selection",
    )
    if (
        public.get("schema") != "triage-owner-training-cohort-public-v1"
        or private.get("schema") != "triage-private-owner-training-selection-v1"
        or descriptor.get("schema") != "triage-private-owner-training-descriptors-v1"
    ):
        raise RuntimeError("source acquisition is not the sealed owner cohort v1")
    source_config = public.get("config")
    acquisition = public.get("acquisition")
    raw_pixel_audit = public.get("raw_pixel_audit")
    private_evidence = public.get("private_evidence")
    final_selection = public.get("final_selection")
    if not all(
        isinstance(section, dict)
        for section in (
            source_config,
            acquisition,
            raw_pixel_audit,
            private_evidence,
            final_selection,
        )
    ):
        raise RuntimeError("source v1 cohort sections are malformed")
    if (
        public.get("inventory_sha256") != snapshot.inventory_sha256
        or private.get("inventory_sha256") != snapshot.inventory_sha256
        or public.get("truth_ledger_sha256") != current_ledger_sha256
        or private.get("truth_ledger_sha256") != current_ledger_sha256
        or source_config.get("target_size") != config.target_size
        or source_config.get("acquisition_target") != config.acquisition_target
        or final_selection.get("name") != "location-training-v1"
        or private_evidence.get("private_selection_sha256") != source_private_sha256
        or private_evidence.get("review_manifest_sha256") != review_context.review_manifest_sha256
    ):
        raise RuntimeError("source v1 outer/private lineage differs from frozen inputs")
    truth_reservation = ledger.cohort_reservation(_v1.REQUIRED_FRESH_TRUTH_COHORT)
    if (
        truth_reservation is None
        or public.get("fresh_truth_selection_sha256") != truth_reservation.selection_sha256
    ):
        raise RuntimeError("source v1 fresh-truth selection differs from the current ledger")
    source_truth_approval = public.get("fresh_truth_approval")
    if (
        not isinstance(source_truth_approval, dict)
        or set(source_truth_approval) != {"authenticated", "public_sha256", "private_sha256"}
        or type(source_truth_approval.get("authenticated")) is not bool
    ):
        raise RuntimeError("source v1 fresh-truth approval binding is malformed")
    if source_truth_approval["authenticated"]:
        if not _is_sha256(source_truth_approval.get("public_sha256")) or not _is_sha256(
            source_truth_approval.get("private_sha256")
        ):
            raise RuntimeError("source v1 fresh-truth approval digests are malformed")
    elif (
        source_truth_approval.get("public_sha256") is not None
        or source_truth_approval.get("private_sha256") is not None
    ):
        raise RuntimeError("unauthenticated source v1 truth approval cannot claim digests")
    current_truth_approval: dict[str, object] = {
        "authenticated": truth_binding.approval_authenticated,
        "public_sha256": truth_binding.approval_public_sha256,
        "private_sha256": truth_binding.approval_private_sha256,
    }
    if truth_binding.approval_authenticated and source_truth_approval not in (
        current_truth_approval,
        {"authenticated": False, "public_sha256": None, "private_sha256": None},
    ):
        raise RuntimeError("source v1 and current fresh-truth approvals disagree")
    acquisition_selection_sha256 = acquisition.get("selection_sha256")
    source_descriptor_sha256 = private.get("descriptor_sha256")
    if (
        not _is_sha256(acquisition_selection_sha256)
        or private.get("acquisition_selection_sha256") != acquisition_selection_sha256
        or descriptor.get("acquisition_selection_sha256") != acquisition_selection_sha256
        or not _is_sha256(source_descriptor_sha256)
        or raw_pixel_audit.get("descriptor_sha256") != source_descriptor_sha256
        or _canonical_sha256(_V1_DESCRIPTOR_DOMAIN, descriptor) != source_descriptor_sha256
    ):
        raise RuntimeError("source v1 acquisition/descriptor seal does not reproduce")

    acquisition_store = source_root / "training-preview-acquisition-v1"
    store_manifest, store_private, store_lock = verify_committed_preview_store(acquisition_store)
    if (
        store_manifest.get("schema") != "triage-pinned-preview-availability-v1"
        or store_manifest.get("cohort_name") != "location-training-preview-acquisition-v1"
        or store_manifest.get("inventory_sha256") != snapshot.inventory_sha256
        or store_manifest.get("selection_sha256") != acquisition_selection_sha256
        or store_private.get("selection_sha256") != acquisition_selection_sha256
        or store_lock.get("selection_sha256") != acquisition_selection_sha256
        or store_manifest.get("attempted_count") != config.acquisition_target
        or type(store_manifest.get("available_count")) is not int
        or int(store_manifest["available_count"]) < config.minimum_available
    ):
        raise RuntimeError("source v1 acquisition preview store has the wrong sealed lineage")
    attempted_count = int(store_manifest["attempted_count"])
    available_count = int(store_manifest["available_count"])
    unavailable_count = int(store_manifest.get("unavailable_count", -1))
    raw_error_counts = store_manifest.get("error_counts")
    if (
        attempted_count != available_count + unavailable_count
        or not isinstance(raw_error_counts, dict)
        or any(
            not isinstance(key, str) or type(value) is not int or value < 0
            for key, value in raw_error_counts.items()
        )
        or sum(raw_error_counts.values()) != unavailable_count
    ):
        raise RuntimeError("source v1 acquisition availability aggregates are malformed")
    raw_lock_rows = store_lock.get("rows")
    raw_available_rows = store_private.get("available_rows")
    if (
        not isinstance(raw_lock_rows, list)
        or len(raw_lock_rows) != config.acquisition_target
        or not isinstance(raw_available_rows, list)
        or len(raw_available_rows) != store_manifest["available_count"]
    ):
        raise RuntimeError("source v1 acquisition row counts are malformed")
    inventory_by_id = {row.asset_id: row for row in snapshot.rows}
    acquisition_rows: list[InventoryAsset] = []
    for raw_row in raw_lock_rows:
        if not isinstance(raw_row, dict):
            raise RuntimeError("source v1 acquisition selection row is malformed")
        row = inventory_by_id.get(str(raw_row.get("asset_id", "")))
        if row is None or (
            raw_row.get("source_updated"),
            raw_row.get("capture_day"),
            raw_row.get("component_key"),
            raw_row.get("moment_key"),
        ) != (
            row.updated_at,
            row.capture_day,
            row.component_key,
            row.moment_key,
        ):
            raise RuntimeError("source v1 acquisition differs from the frozen inventory")
        acquisition_rows.append(row)
    if cohort_selection_sha256(acquisition_rows) != acquisition_selection_sha256:
        raise RuntimeError("source v1 acquisition selection digest does not reproduce")
    blocked = ledger.blocklist()
    if any(
        row.asset_id in blocked.asset_ids
        or row.component_key in blocked.component_keys
        or row.moment_key in blocked.moment_keys
        for row in acquisition_rows
    ):
        raise RuntimeError("source v1 acquisition overlaps current immutable truth")

    available_by_id: dict[str, Mapping[str, object]] = {}
    for raw_row in raw_available_rows:
        if not isinstance(raw_row, dict):
            raise RuntimeError("source v1 available preview row is malformed")
        asset_id = str(raw_row.get("asset_id", ""))
        if asset_id in available_by_id or asset_id not in inventory_by_id:
            raise RuntimeError("source v1 available previews contain invalid identities")
        available_by_id[asset_id] = raw_row
    descriptor_rows = descriptor.get("rows")
    if not isinstance(descriptor_rows, list):
        raise RuntimeError("source v1 descriptor rows are malformed")
    descriptors_by_id: dict[str, Mapping[str, object]] = {}
    for raw_row in descriptor_rows:
        if not isinstance(raw_row, dict):
            raise RuntimeError("source v1 descriptor row is malformed")
        asset_id = str(raw_row.get("asset_id", ""))
        if asset_id in descriptors_by_id:
            raise RuntimeError("source v1 descriptor rows contain duplicate identities")
        descriptors_by_id[asset_id] = raw_row
    if set(descriptors_by_id) != set(available_by_id):
        raise RuntimeError("source v1 descriptor audit and available pixels differ")
    source_seal_paths = (
        Path("training-cohort-public.json"),
        Path(V2_PRIVATE_SELECTION_FILENAME),
        Path(V2_DESCRIPTOR_AUDIT_FILENAME),
        Path("training-preview-acquisition-v1/manifest.json"),
        Path("training-preview-acquisition-v1/private-index.json"),
        Path("training-preview-acquisition-v1/selection-lock.json"),
        *tuple(
            path.relative_to(source_root)
            for path in sorted(
                (source_root / "blind-review").iterdir(),
                key=lambda path: path.name,
            )
        ),
    )
    source_file_seals = tuple(
        (str(relative), _file_sha256(source_root / relative)) for relative in source_seal_paths
    )
    return SourceV1AcquisitionEvidence(
        source_root=source_root,
        acquisition_store=acquisition_store,
        acquisition_rows=tuple(acquisition_rows),
        available_private_rows=available_by_id,
        source_descriptor_rows=descriptors_by_id,
        source_run_sha256=review_context.cohort_run_sha256,
        source_review_manifest_sha256=review_context.review_manifest_sha256,
        source_private_selection_sha256=source_private_sha256,
        source_descriptor_sha256=str(source_descriptor_sha256),
        source_acquisition_manifest_sha256=str(store_manifest["manifest_sha256"]),
        source_acquisition_private_index_sha256=str(store_manifest["private_index_sha256"]),
        source_acquisition_selection_lock_sha256=str(store_manifest["selection_lock_sha256"]),
        source_rejection_decision_sha256=rejection_sha256,
        acquisition_selection_sha256=str(acquisition_selection_sha256),
        ledger_sha256=current_ledger_sha256,
        fresh_truth_selection_sha256=truth_reservation.selection_sha256,
        fresh_truth_approval=current_truth_approval,
        attempted_count=attempted_count,
        available_count=available_count,
        unavailable_count=unavailable_count,
        error_counts={str(key): int(value) for key, value in raw_error_counts.items()},
        source_file_seals=source_file_seals,
    )


def _recheck_source_v1_seals(
    evidence: SourceV1AcquisitionEvidence,
    *,
    decision_path: Path,
    ledger: TruthReservationLedger,
    memory_limit_bytes: int,
    progress: Callable[[int, int], None] | None,
) -> None:
    """Close source/decision/ledger TOCTOU immediately before atomic publish."""
    if ledger.ledger_sha256() != evidence.ledger_sha256:
        raise RuntimeError("truth ledger changed before v2 atomic publish")
    if (
        _authenticate_source_v1_rejection(evidence.source_root, decision_path)
        != evidence.source_rejection_decision_sha256
    ):
        raise RuntimeError("source v1 rejection decision changed before v2 commit")
    labels = {
        "training-cohort-public.json": "source v1 public manifest",
        V2_PRIVATE_SELECTION_FILENAME: "source v1 private selection",
        V2_DESCRIPTOR_AUDIT_FILENAME: "source v1 descriptor",
        "training-preview-acquisition-v1/manifest.json": ("source v1 acquisition manifest"),
        "training-preview-acquisition-v1/private-index.json": (
            "source v1 acquisition private index"
        ),
        "training-preview-acquisition-v1/selection-lock.json": (
            "source v1 acquisition selection lock"
        ),
        "blind-review/review-manifest.json": "source v1 review manifest",
    }
    for relative, expected_sha256 in evidence.source_file_seals:
        path = evidence.source_root / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"source v1 sealed file disappeared: {relative}")
        metadata = path.stat()
        if metadata.st_mode & 0o777 != 0o600 or metadata.st_nlink != 1:
            raise RuntimeError(f"source v1 sealed file permissions changed: {relative}")
        if _file_sha256(path) != expected_sha256:
            if relative == V2_DESCRIPTOR_AUDIT_FILENAME:
                raise RuntimeError("source v1 descriptor changed before v2 commit")
            raise RuntimeError(f"source v1 sealed file changed before v2 commit: {relative}")
        if relative.endswith(".json"):
            _load_private_json(path, label=labels.get(relative, "source v1 review evidence"))
    total_images = len(evidence.available_private_rows)
    for index, asset_id in enumerate(sorted(evidence.available_private_rows), start=1):
        private_row = evidence.available_private_rows[asset_id]
        image_path = _v1._safe_store_image(  # noqa: SLF001
            evidence.acquisition_store,
            str(private_row["image_relpath"]),
        )
        metadata = image_path.stat()
        if metadata.st_mode & 0o777 != 0o600 or metadata.st_nlink != 1:
            raise RuntimeError("source v1 acquisition image permissions changed")
        if _file_sha256(image_path) != private_row["preview_sha256"]:
            raise RuntimeError("source v1 acquisition image changed before v2 commit")
        if progress is not None:
            progress(index, total_images)
        if index % 128 == 0:
            _v1._assert_memory_limit(memory_limit_bytes)  # noqa: SLF001
    if ledger.ledger_sha256() != evidence.ledger_sha256:
        raise RuntimeError("truth ledger changed during the final v1 image seal recheck")


def _scan_source_v1_candidates(
    evidence: SourceV1AcquisitionEvidence,
    *,
    config: TrainingCohortConfig,
    progress: Callable[[int, int], None] | None,
) -> _CandidateScan:
    acquisition_by_id = {row.asset_id: row for row in evidence.acquisition_rows}
    ordered_ids = sorted(evidence.available_private_rows)
    descriptors = []
    pixel_metrics = []
    paths: list[Path] = []
    audit_ids: list[str] = []
    for index, asset_id in enumerate(ordered_ids, start=1):
        private_row = evidence.available_private_rows[asset_id]
        source_descriptor = evidence.source_descriptor_rows[asset_id]
        row = acquisition_by_id.get(asset_id)
        if row is None:
            raise RuntimeError("source v1 available pixel lies outside its acquisition")
        path = _v1._safe_store_image(  # noqa: SLF001 - same sealed cohort subsystem
            evidence.acquisition_store,
            str(private_row["image_relpath"]),
        )
        payload = path.read_bytes()
        descriptor = describe_preview_bytes(payload)
        metrics = describe_pixel_morphology(payload)
        audit_id = str(private_row.get("audit_id", ""))
        provisional = TrainingCandidate(
            row=row,
            audit_id=audit_id,
            image_path=path,
            descriptor=descriptor,
            raw_cluster_label=0,
            raw_cluster_distance=0.0,
            pixel_morphology=metrics,
        )
        actual_source_fields = {
            **_v1._pixel_morphology_record(  # noqa: SLF001
                provisional
            ),
            "asset_id": asset_id,
            "average_hash": descriptor.average_hash,
            "difference_hash": descriptor.difference_hash,
            "aspect_ratio": descriptor.aspect_ratio,
            "vector_sha256": hashlib.sha256(descriptor.vector.tobytes()).hexdigest(),
        }
        if any(
            source_descriptor.get(field) != value for field, value in actual_source_fields.items()
        ):
            raise RuntimeError("source v1 pixel descriptor no longer reproduces")
        descriptors.append(descriptor)
        pixel_metrics.append(metrics)
        paths.append(path)
        audit_ids.append(audit_id)
        if progress is not None:
            progress(index, len(ordered_ids))
        if index % 128 == 0:
            _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    if len(descriptors) < config.minimum_available:
        raise RuntimeError("source v1 available pixels fell below the v2 success floor")
    clustering = cluster_descriptors(
        descriptors,
        cluster_count=config.cluster_count,
        seed=int(hashlib.sha256(config.seed.encode()).hexdigest()[:8], 16),
    )
    candidates = tuple(
        TrainingCandidate(
            row=acquisition_by_id[asset_id],
            audit_id=audit_id,
            image_path=path,
            descriptor=descriptor,
            raw_cluster_label=int(label),
            raw_cluster_distance=float(distance),
            pixel_morphology=metrics,
        )
        for asset_id, audit_id, path, descriptor, metrics, label, distance in zip(
            ordered_ids,
            audit_ids,
            paths,
            descriptors,
            pixel_metrics,
            clustering.labels,
            clustering.distances,
            strict=True,
        )
    )
    records = [
        {
            **_v1._pixel_morphology_record(candidate),  # noqa: SLF001
            "asset_id": candidate.row.asset_id,
            "average_hash": candidate.descriptor.average_hash,
            "difference_hash": candidate.descriptor.difference_hash,
            "aspect_ratio": candidate.descriptor.aspect_ratio,
            "vector_sha256": hashlib.sha256(candidate.descriptor.vector.tobytes()).hexdigest(),
            "raw_cluster_label": candidate.raw_cluster_label,
            "raw_cluster_distance": candidate.raw_cluster_distance,
        }
        for candidate in candidates
    ]
    descriptor_payload = {
        "schema": "triage-private-owner-training-descriptors-v2",
        "descriptor_version": DESCRIPTOR_VERSION,
        "pixel_morphology_version": PIXEL_MORPHOLOGY_VERSION,
        "pixel_morphology_sha256": _v1._pixel_morphology_sha256(  # noqa: SLF001
            candidates
        ),
        "source_v1_descriptor_sha256": evidence.source_descriptor_sha256,
        "acquisition_selection_sha256": evidence.acquisition_selection_sha256,
        "rows": records,
    }
    descriptor_bytes = _canonical_bytes(descriptor_payload)
    descriptor_sha256 = _canonical_sha256(_V2_DESCRIPTOR_DOMAIN, descriptor_payload)
    _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    return _CandidateScan(
        candidates=candidates,
        clustering=clustering,
        descriptor_sha256=descriptor_sha256,
        descriptor_bytes=descriptor_bytes,
    )


def audit_source_v1_retention(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    source_root: Path,
    source_rejection_decision_path: Path,
    config: TrainingCohortConfig = TrainingCohortConfig(seed="location-recovery-1b-training-v2"),
    fresh_truth_approval_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> V2RetentionAudit:
    """Authenticate and count v2 survivors without creating cohort output."""
    _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    evidence = _authenticate_source_v1_acquisition(
        snapshot=snapshot,
        ledger=ledger,
        source_root=Path(source_root),
        source_rejection_decision_path=Path(source_rejection_decision_path),
        config=config,
        fresh_truth_approval_dir=fresh_truth_approval_dir,
    )
    scan = _scan_source_v1_candidates(
        evidence,
        config=config,
        progress=progress,
    )
    filtered = filter_v2_candidate_pool(
        scan.candidates,
        seed=config.seed,
        max_checks_per_candidate=config.max_near_duplicate_checks,
    )
    nearest = audit_global_nearest_similarity(filtered.cosine.retained)
    if not nearest.passed:
        raise RuntimeError("v2 retained pool violates its global cosine-family gate")
    if ledger.ledger_sha256() != evidence.ledger_sha256:
        raise RuntimeError("truth ledger changed during the v2 retention audit")
    audit = V2RetentionAudit(
        source_run_sha256=evidence.source_run_sha256,
        acquisition_selection_sha256=evidence.acquisition_selection_sha256,
        source_descriptor_sha256=evidence.source_descriptor_sha256,
        v2_descriptor_sha256=scan.descriptor_sha256,
        source_available_count=len(scan.candidates),
        source_quality_failure_count=filtered.source_quality_failure_count,
        quality_pass_count=len(filtered.quality_passed),
        legacy_duplicate_rejected_count=filtered.legacy.rejected_count,
        legacy_retained_count=len(filtered.legacy.retained),
        cosine_duplicate_rejected_count=filtered.cosine.rejected_count,
        cosine_retained_count=len(filtered.cosine.retained),
        cosine_threshold=filtered.cosine.threshold,
        retained_max_nearest_similarity=nearest.max_nearest_similarity,
        target_size=config.target_size,
    )
    audit.public_dict()
    _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    return audit


def _global_nearest_review_pairs(
    selected: tuple[TrainingCandidate, ...],
    *,
    pair_count: int,
) -> tuple[tuple[TrainingCandidate, TrainingCandidate, float], ...]:
    ordered = tuple(sorted(selected, key=lambda candidate: candidate.audit_id))
    if len(ordered) < 2:
        return ()
    normalized = _normalized_vectors(ordered)
    from sklearn.neighbors import BallTree

    tree = BallTree(normalized, leaf_size=40, metric="euclidean")
    neighbor_count = min(len(ordered), max(2, pair_count + 1))
    _distances, indices = tree.query(
        normalized,
        k=neighbor_count,
        return_distance=True,
        dualtree=True,
        breadth_first=True,
    )
    pairs: dict[tuple[int, int], float] = {}
    for left_index, neighbor_indices in enumerate(indices):
        for raw_right_index in neighbor_indices:
            right_index = int(raw_right_index)
            if right_index == left_index:
                continue
            key = tuple(sorted((left_index, right_index)))
            similarity = float(np.dot(normalized[left_index], normalized[right_index]))
            pairs[key] = max(pairs.get(key, -1.0), similarity)
    ranked = sorted(
        (
            similarity,
            ordered[left].audit_id,
            ordered[right].audit_id,
            left,
            right,
        )
        for (left, right), similarity in pairs.items()
    )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(
        (ordered[left], ordered[right], similarity)
        for similarity, _left_id, _right_id, left, right in ranked[:pair_count]
    )


def _render_v2_review_bundle(
    *,
    output_dir: Path,
    final_store: Path,
    selection: TrainingSelection,
    nearest_audit: GlobalNearestSimilarityAudit,
    config: TrainingCohortConfig,
) -> dict[str, object]:
    _v1._private_directory(output_dir)  # noqa: SLF001
    private_rows = _v1._load_preview_rows(  # noqa: SLF001
        final_store,
        selection_sha256=selection.cohort.selection_sha256,
        available=False,
    )

    def entry(candidate: TrainingCandidate, caption: str) -> tuple[Path, str]:
        private = private_rows[str(candidate.row.asset_id)]
        return (
            _v1._safe_store_image(  # noqa: SLF001
                final_store,
                str(private["image_relpath"]),
            ),
            f"{caption} | {private['audit_id']}",
        )

    by_cluster: dict[int, list[TrainingCandidate]] = defaultdict(list)
    for candidate in selection.selected:
        by_cluster[candidate.raw_cluster_label].append(candidate)
    medoids = [
        min(
            members,
            key=lambda candidate: (candidate.raw_cluster_distance, candidate.audit_id),
        )
        for _label, members in sorted(by_cluster.items())
    ]
    medoid_entries = [
        entry(candidate, f"morph {candidate.raw_cluster_label:03d}") for candidate in medoids
    ]
    random_candidates = sorted(
        selection.selected,
        key=lambda candidate: (
            _stable_rank(config.seed, "v2-blind-random-review", candidate.audit_id),
            candidate.audit_id,
        ),
    )[: config.review_sample_size]
    random_entries = [entry(candidate, "random") for candidate in random_candidates]
    nearest_pairs = _global_nearest_review_pairs(
        selection.selected,
        pair_count=max(1, config.review_sample_size // 2),
    )
    nearest_entries: list[tuple[Path, str]] = []
    for pair_index, (left, right, similarity) in enumerate(nearest_pairs, start=1):
        nearest_entries.append(entry(left, f"global {pair_index:02d}A {similarity:.6f}"))
        nearest_entries.append(entry(right, f"global {pair_index:02d}B {similarity:.6f}"))
    groups = {
        "medoids": _v1._render_pages(  # noqa: SLF001
            medoid_entries,
            output_dir=output_dir,
            stem="medoids",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "random": _v1._render_pages(  # noqa: SLF001
            random_entries,
            output_dir=output_dir,
            stem="random",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "nearest": _v1._render_pages(  # noqa: SLF001
            nearest_entries,
            output_dir=output_dir,
            stem="nearest",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
    }
    manifest: dict[str, object] = {
        "schema": "triage-private-owner-training-review-v2",
        "blindness": "opaque audit ids and raw pixels only; no labels or head output",
        "final_selection_sha256": selection.cohort.selection_sha256,
        "nearest_method": "exact normalized-descriptor global nearest neighbors",
        "complexity": "exact BallTree nearest query; no centroid-probe shortcut",
        "global_nearest_similarity": nearest_audit.public_dict(),
        "groups": groups,
    }
    manifest["review_manifest_sha256"] = _canonical_sha256(
        _V2_REVIEW_DOMAIN,
        manifest,
    )
    _write_create_only(output_dir / "review-manifest.json", _canonical_bytes(manifest))
    return manifest


def _build_training_cohort_v2_staged(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    source_root: Path,
    source_rejection_decision_path: Path,
    output_dir: Path,
    publish_output_dir: Path,
    config: TrainingCohortConfig,
    fresh_truth_approval_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> TrainingCohortV2Run:
    """Build v2 solely from authenticated pixels in the rejected v1 acquisition."""
    _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    decision_path = Path(source_rejection_decision_path)
    if decision_path.is_symlink() or not decision_path.is_file():
        raise RuntimeError("formal v1 rejection decision input is missing")
    source_root = Path(source_root)
    output_dir = Path(output_dir)
    publish_output_dir = Path(publish_output_dir)
    if source_root.resolve() == publish_output_dir.resolve():
        raise RuntimeError("v2 output must not replace the rejected v1 source")
    if publish_output_dir.exists() or publish_output_dir.is_symlink():
        raise RuntimeError("v2 output already exists before source authentication")
    evidence = _authenticate_source_v1_acquisition(
        snapshot=snapshot,
        ledger=ledger,
        source_root=source_root,
        source_rejection_decision_path=decision_path,
        config=config,
        fresh_truth_approval_dir=fresh_truth_approval_dir,
    )
    scan = _scan_source_v1_candidates(
        evidence,
        config=config,
        progress=progress,
    )
    filtered = filter_v2_candidate_pool(
        scan.candidates,
        seed=config.seed,
        max_checks_per_candidate=config.max_near_duplicate_checks,
    )
    quality_passed = filtered.quality_passed
    legacy_deduplicated = filtered.legacy
    cosine_filtered = filtered.cosine
    if len(cosine_filtered.retained) < config.target_size:
        raise RuntimeError(
            "global cosine duplicate-family removal left fewer than the final target"
        )
    selection_v1 = _v1.select_final_training_cohort(
        cosine_filtered.retained,
        acquisition_candidates=scan.candidates,
        eligible_inventory=snapshot.rows,
        blocked=ledger.blocklist(),
        config=config,
    )
    selection = TrainingSelection(
        cohort=CohortSelection(
            name=V2_COHORT_NAME,
            rows=selection_v1.cohort.rows,
            selection_sha256=selection_v1.cohort.selection_sha256,
            audit=selection_v1.cohort.audit,
        ),
        selected=selection_v1.selected,
        audit=selection_v1.audit,
    )
    nearest_audit = audit_global_nearest_similarity(selection.selected)
    if not nearest_audit.passed:
        raise RuntimeError(
            "global nearest-similarity gate failed: " + ", ".join(nearest_audit.failures)
        )
    if ledger.ledger_sha256() != evidence.ledger_sha256:
        raise RuntimeError("truth ledger changed before v2 evidence staging")

    if output_dir.is_symlink() or not output_dir.is_dir() or any(output_dir.iterdir()):
        raise RuntimeError("v2 staging root is not a new empty private directory")
    os.chmod(output_dir, 0o700)
    _write_create_only(
        output_dir / V2_DESCRIPTOR_AUDIT_FILENAME,
        scan.descriptor_bytes,
    )
    selected_by_id = {str(candidate.row.asset_id): candidate for candidate in selection.selected}

    def descriptor_bound_preview(asset_id: str) -> bytes:
        candidate = selected_by_id[asset_id]
        payload = candidate.image_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != candidate.descriptor.preview_sha256:
            raise RuntimeError("selected v1 preview changed after the v2 descriptor audit")
        return payload

    final_store = output_dir / V2_FINAL_STORE_NAME
    final_manifest = pin_preview_store(
        final_store,
        selection.cohort.rows,
        cohort_name=V2_COHORT_NAME,
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=selection.cohort.selection_sha256,
        fetch_preview=descriptor_bound_preview,
        audit_prefix="T",
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
        progress=progress,
    )
    if ledger.ledger_sha256() != evidence.ledger_sha256:
        raise RuntimeError("truth ledger changed while v2 final previews were pinned")
    final_rows = _v1._load_preview_rows(  # noqa: SLF001
        final_store,
        selection_sha256=selection.cohort.selection_sha256,
        available=False,
    )
    preview_descriptor_bindings: list[dict[str, object]] = []
    private_rows: list[dict[str, object]] = []
    for candidate in sorted(selection.selected, key=lambda item: item.row.asset_id):
        final_row = final_rows[candidate.row.asset_id]
        final_preview_sha256 = str(final_row["preview_sha256"])
        if final_preview_sha256 != candidate.descriptor.preview_sha256:
            raise RuntimeError("final v2 preview differs from its descriptor evidence")
        descriptor_vector_sha256 = hashlib.sha256(candidate.descriptor.vector.tobytes()).hexdigest()
        preview_descriptor_bindings.append(
            {
                "asset_id": candidate.row.asset_id,
                "acquisition_audit_id": candidate.audit_id,
                "final_audit_id": final_row["audit_id"],
                "preview_sha256": final_preview_sha256,
                "descriptor_vector_sha256": descriptor_vector_sha256,
            }
        )
        private_rows.append(
            {
                "asset_id": candidate.row.asset_id,
                "component_key": candidate.row.component_key,
                "moment_key": candidate.row.moment_key,
                "capture_day": candidate.row.capture_day,
                "stratum": candidate.row.stratum,
                "acquisition_audit_id": candidate.audit_id,
                "final_audit_id": final_row["audit_id"],
                "raw_cluster_label": candidate.raw_cluster_label,
                "preview_sha256": final_preview_sha256,
                "descriptor_vector_sha256": descriptor_vector_sha256,
            }
        )
    binding_payload = {
        "schema": "triage-training-preview-descriptor-binding-v2",
        "descriptor_sha256": scan.descriptor_sha256,
        "rows": preview_descriptor_bindings,
    }
    preview_descriptor_binding_sha256 = _canonical_sha256(
        _V2_BINDING_DOMAIN,
        binding_payload,
    )
    nearest_public = nearest_audit.public_dict()
    duplicate_public = {
        "algorithm": COSINE_DUPLICATE_ALGORITHM,
        "threshold": cosine_filtered.threshold,
        "comparison": "strictly_below",
        "input_count": len(legacy_deduplicated.retained),
        "rejected_count": cosine_filtered.rejected_count,
        "retained_count": len(cosine_filtered.retained),
        "radius_query_count": cosine_filtered.radius_query_count,
        "radius_neighbor_edge_count": cosine_filtered.radius_neighbor_edge_count,
        "max_batch_neighbor_count": cosine_filtered.max_batch_neighbor_count,
    }
    source_lineage: dict[str, object] = {
        "cohort_run_sha256": evidence.source_run_sha256,
        "review_manifest_sha256": evidence.source_review_manifest_sha256,
        "rejection_decision_sha256": evidence.source_rejection_decision_sha256,
        "private_selection_sha256": evidence.source_private_selection_sha256,
        "descriptor_sha256": evidence.source_descriptor_sha256,
        "acquisition_selection_sha256": evidence.acquisition_selection_sha256,
        "acquisition_preview_manifest_sha256": (evidence.source_acquisition_manifest_sha256),
        "acquisition_private_index_sha256": (evidence.source_acquisition_private_index_sha256),
        "acquisition_selection_lock_sha256": (evidence.source_acquisition_selection_lock_sha256),
    }
    private_selection: dict[str, object] = {
        "schema": "triage-private-owner-training-selection-v2",
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256": evidence.ledger_sha256,
        "source_v1": source_lineage,
        "acquisition_selection_sha256": evidence.acquisition_selection_sha256,
        "descriptor_sha256": scan.descriptor_sha256,
        "preview_descriptor_binding_sha256": preview_descriptor_binding_sha256,
        "final_selection_sha256": selection.cohort.selection_sha256,
        "final_quota_feasibility": selection.audit.quota_feasibility.public_dict(),
        "cosine_duplicate_filter": duplicate_public,
        "global_nearest_similarity": nearest_public,
        "global_nearest_pair_audit_ids": (
            list(nearest_audit.nearest_pair_audit_ids)
            if nearest_audit.nearest_pair_audit_ids is not None
            else []
        ),
        "rows": private_rows,
    }
    private_selection["private_selection_sha256"] = _canonical_sha256(
        _V2_RUN_DOMAIN,
        private_selection,
    )
    _write_create_only(
        output_dir / V2_PRIVATE_SELECTION_FILENAME,
        _canonical_bytes(private_selection),
    )
    review_manifest = _render_v2_review_bundle(
        output_dir=output_dir / "blind-review",
        final_store=final_store,
        selection=selection,
        nearest_audit=nearest_audit,
        config=config,
    )

    cluster_counts = Counter(int(label) for label in scan.clustering.labels)
    source_quality_failure_count = filtered.source_quality_failure_count
    public_manifest: dict[str, object] = {
        "schema": "triage-owner-training-cohort-public-v2",
        "status": "awaiting_visual_review",
        "privacy": "aggregate only; asset mapping is restricted to private indices",
        "inventory_sha256": snapshot.inventory_sha256,
        "inventory_asset_count": len(snapshot.rows),
        "truth_ledger_sha256": evidence.ledger_sha256,
        "required_truth_cohort": _v1.REQUIRED_FRESH_TRUTH_COHORT,
        "fresh_truth_selection_sha256": evidence.fresh_truth_selection_sha256,
        "fresh_truth_approval": dict(evidence.fresh_truth_approval),
        "semantic_inputs": "disabled",
        "config": config.public_dict(),
        "source_v1": source_lineage,
        "acquisition": {
            "selection_sha256": evidence.acquisition_selection_sha256,
            "attempted_count": evidence.attempted_count,
            "available_count": evidence.available_count,
            "unavailable_count": evidence.unavailable_count,
            "error_counts": dict(evidence.error_counts),
            "reuse_mode": "authenticated-sealed-v1-acquisition-no-refetch",
        },
        "raw_pixel_audit": {
            "descriptor_version": DESCRIPTOR_VERSION,
            "descriptor_sha256": scan.descriptor_sha256,
            "cluster_count": config.cluster_count,
            "represented_clusters": len(cluster_counts),
            "normalized_cluster_entropy": scan.clustering.normalized_entropy,
            "largest_cluster_share": scan.clustering.largest_cluster_share,
            "cluster_counts": {
                str(label): cluster_counts[label] for label in sorted(cluster_counts)
            },
            "conservative_quality_failure_count": source_quality_failure_count,
            "quality_pass_count": len(quality_passed),
            "legacy_near_duplicate_rejections": legacy_deduplicated.rejected_count,
            "legacy_near_duplicate_comparisons": legacy_deduplicated.comparison_count,
            "legacy_near_duplicate_bucket_probes": legacy_deduplicated.bucket_probe_count,
            "legacy_bucket_overflow_count": legacy_deduplicated.bucket_overflow_count,
            "cosine_duplicate_filter": duplicate_public,
            "global_nearest_similarity": nearest_public,
            "complexity": (
                "bounded perceptual-hash prefilter plus exact float64 normalized "
                "BallTree cosine-radius family suppression"
            ),
        },
        "final_selection": {
            "name": selection.cohort.name,
            "selection_sha256": selection.cohort.selection_sha256,
            "audit": selection.cohort.audit.public_dict(),
            "quota_feasibility": selection.audit.quota_feasibility.public_dict(),
            "morphology_audit": selection.audit.public_dict(),
            "global_nearest_similarity": nearest_public,
        },
        "private_evidence": {
            "final_preview_count": final_manifest["row_count"],
            "final_preview_manifest_sha256": final_manifest["manifest_sha256"],
            "descriptor_sha256": scan.descriptor_sha256,
            "private_selection_sha256": private_selection["private_selection_sha256"],
            "preview_descriptor_binding_sha256": (preview_descriptor_binding_sha256),
            "review_manifest_sha256": review_manifest["review_manifest_sha256"],
            "review_sheet_counts": {
                name: len(pages) for name, pages in review_manifest["groups"].items()
            },
        },
        "memory_provenance": {
            "process_cap_bytes": config.memory_limit_bytes,
            "global_ceiling_bytes": SYSTEM_MEMORY_CEILING_BYTES,
            "process_cap_gib": config.memory_limit_bytes / 1024**3,
            "global_ceiling_gib": SYSTEM_MEMORY_CEILING_BYTES / 1024**3,
        },
    }
    public_manifest["run_sha256"] = _canonical_sha256(
        _V2_RUN_DOMAIN,
        public_manifest,
    )
    public_bytes = _canonical_bytes(public_manifest)

    def commit_public_manifest() -> None:
        _recheck_source_v1_seals(
            evidence,
            decision_path=decision_path,
            ledger=ledger,
            memory_limit_bytes=config.memory_limit_bytes,
            progress=progress,
        )
        _write_create_only(output_dir / "training-cohort-public.json", public_bytes)
        if publish_output_dir.exists() or publish_output_dir.is_symlink():
            raise RuntimeError("v2 output appeared before the atomic publish")
        os.rename(output_dir, publish_output_dir)
        _v1._fsync_directory(publish_output_dir.parent)  # noqa: SLF001

    ledger.commit_if_unchanged(
        expected_ledger_sha256=evidence.ledger_sha256,
        commit=commit_public_manifest,
    )
    _v1._assert_memory_limit(config.memory_limit_bytes)  # noqa: SLF001
    return TrainingCohortV2Run(
        selection=selection,
        duplicate_filter=cosine_filtered,
        nearest_audit=nearest_audit,
        public_manifest=public_manifest,
    )


def build_training_cohort_v2(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    source_root: Path,
    source_rejection_decision_path: Path,
    output_dir: Path,
    config: TrainingCohortConfig = TrainingCohortConfig(seed="location-recovery-1b-training-v2"),
    fresh_truth_approval_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> TrainingCohortV2Run:
    """Stage privately, then atomically publish a complete create-only v2 root."""
    final_output = Path(output_dir)
    if final_output.exists() or final_output.is_symlink():
        raise RuntimeError("v2 output already exists before source authentication")
    final_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{final_output.name}.staging-",
        dir=final_output.parent,
    ) as temporary_root:
        staging_root = Path(temporary_root)
        os.chmod(staging_root, 0o700)
        return _build_training_cohort_v2_staged(
            snapshot=snapshot,
            ledger=ledger,
            source_root=source_root,
            source_rejection_decision_path=source_rejection_decision_path,
            output_dir=staging_root,
            publish_output_dir=final_output,
            config=config,
            fresh_truth_approval_dir=fresh_truth_approval_dir,
            progress=progress,
        )


def audit_global_nearest_similarity(
    candidates: tuple[TrainingCandidate, ...],
) -> GlobalNearestSimilarityAudit:
    """Compute the exact closest normalized-descriptor pair and enforce <0.99."""
    if not candidates:
        raise ValueError("global nearest audit requires at least one selected candidate")
    audit_ids = [candidate.audit_id for candidate in candidates]
    if len(set(audit_ids)) != len(audit_ids) or any(not value for value in audit_ids):
        raise ValueError("global nearest audit requires unique nonempty audit ids")
    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.audit_id))
    normalized = _normalized_vectors(ordered)
    descriptor_set_sha256 = _canonical_sha256(
        _NEAREST_AUDIT_DOMAIN,
        {
            "schema": "triage-owner-training-descriptor-set-v2",
            "rows": [
                {
                    "audit_id": candidate.audit_id,
                    "preview_sha256": candidate.descriptor.preview_sha256,
                    "vector_sha256": hashlib.sha256(
                        candidate.descriptor.vector.tobytes()
                    ).hexdigest(),
                }
                for candidate in ordered
            ],
        },
    )
    nearest_pair: tuple[str, str] | None = None
    maximum = 0.0
    if len(ordered) >= 2:
        from sklearn.neighbors import BallTree

        tree = BallTree(normalized, leaf_size=40, metric="euclidean")
        _distances, indices = tree.query(
            normalized,
            k=2,
            return_distance=True,
            dualtree=True,
            breadth_first=True,
        )
        best_key: tuple[float, str, str] | None = None
        for index, neighbor_indices in enumerate(indices):
            neighbor = next(int(value) for value in neighbor_indices if int(value) != index)
            left_id, right_id = sorted((ordered[index].audit_id, ordered[neighbor].audit_id))
            similarity = float(np.dot(normalized[index], normalized[neighbor]))
            key = (-similarity, left_id, right_id)
            if best_key is None or key < best_key:
                best_key = key
                maximum = similarity
                nearest_pair = (left_id, right_id)
    nearest_pair_sha256 = _canonical_sha256(
        _NEAREST_AUDIT_DOMAIN,
        {
            "schema": "triage-owner-training-nearest-pair-v2",
            "audit_ids": list(nearest_pair) if nearest_pair is not None else [],
        },
    )
    failures = (
        ("global_nearest_similarity_at_or_above_gate",)
        if maximum + 1e-12 >= COSINE_DUPLICATE_THRESHOLD
        else ()
    )
    audit = GlobalNearestSimilarityAudit(
        selected_count=len(ordered),
        threshold=COSINE_DUPLICATE_THRESHOLD,
        max_nearest_similarity=maximum,
        nearest_pair_audit_ids=nearest_pair,
        nearest_pair_sha256=nearest_pair_sha256,
        descriptor_set_sha256=descriptor_set_sha256,
        failures=failures,
    )
    audit.public_dict()
    return audit


@dataclass(frozen=True, slots=True)
class _VisualReviewContext:
    cohort_run_sha256: str
    final_selection_sha256: str
    final_preview_manifest_sha256: str
    final_preview_count: int
    review_manifest_sha256: str
    nearest_audit_sha256: str
    max_nearest_similarity: float
    reviewed_pages: dict[str, list[dict[str, object]]]


@dataclass(frozen=True, slots=True)
class _ReviewDecisionContext:
    cohort_run_sha256: str
    final_selection_sha256: str
    review_manifest_sha256: str
    reviewed_pages: dict[str, list[dict[str, object]]]


def _load_v1_review_decision_context(cohort_root: Path) -> _ReviewDecisionContext:
    root = Path(cohort_root)
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("v1 cohort root must be a private mode-0700 directory")
    public, _public_bytes = _load_private_json(
        root / "training-cohort-public.json",
        label="v1 owner cohort manifest",
    )
    run_sha256 = _verify_self_digest(
        public,
        field="run_sha256",
        domain=b"triage-owner-training-cohort-v1\0",
        label="v1 owner cohort manifest",
    )
    if (
        public.get("schema") != "triage-owner-training-cohort-public-v1"
        or public.get("status") != "sealed_exact_training_cohort"
    ):
        raise RuntimeError("rejection decision requires a sealed owner training v1 cohort")
    final_selection = public.get("final_selection")
    private_evidence = public.get("private_evidence")
    if not isinstance(final_selection, dict) or not isinstance(private_evidence, dict):
        raise RuntimeError("v1 cohort review lineage is malformed")
    selection_sha256 = final_selection.get("selection_sha256")
    expected_review_sha256 = private_evidence.get("review_manifest_sha256")
    if not _is_sha256(selection_sha256) or not _is_sha256(expected_review_sha256):
        raise RuntimeError("v1 cohort review lineage contains an invalid digest")
    review_root = root / "blind-review"
    if (
        review_root.is_symlink()
        or not review_root.is_dir()
        or review_root.stat().st_mode & 0o777 != 0o700
    ):
        raise PermissionError("v1 blind-review root must be a private mode-0700 directory")
    review, _review_bytes = _load_private_json(
        review_root / "review-manifest.json",
        label="v1 visual review manifest",
    )
    review_sha256 = _verify_self_digest(
        review,
        field="review_manifest_sha256",
        domain=b"triage-owner-training-review-v1\0",
        label="v1 visual review manifest",
    )
    if (
        review.get("schema") != "triage-private-owner-training-review-v1"
        or review.get("final_selection_sha256") != selection_sha256
        or review_sha256 != expected_review_sha256
    ):
        raise RuntimeError("v1 visual review manifest differs from its cohort seal")
    groups = review.get("groups")
    required_groups = {"medoids", "random", "nearest"}
    if not isinstance(groups, dict) or set(groups) != required_groups:
        raise RuntimeError("v1 review must contain medoid, random, and nearest sheets")
    reviewed_pages: dict[str, list[dict[str, object]]] = {}
    expected_files = {"review-manifest.json"}
    for name in sorted(required_groups):
        raw_pages = groups[name]
        if not isinstance(raw_pages, list) or not raw_pages:
            raise RuntimeError(f"v1 {name} review group is empty")
        pages: list[dict[str, object]] = []
        for expected_page, raw_page in enumerate(raw_pages, start=1):
            if (
                not isinstance(raw_page, dict)
                or set(raw_page) != {"page", "tile_count", "sha256"}
                or raw_page.get("page") != expected_page
                or type(raw_page.get("tile_count")) is not int
                or int(raw_page["tile_count"]) < 1
                or not _is_sha256(raw_page.get("sha256"))
            ):
                raise RuntimeError(f"v1 {name} review page evidence is malformed")
            filename = f"{name}-{expected_page:02d}.png"
            page_path = review_root / filename
            if page_path.is_symlink() or not page_path.is_file():
                raise RuntimeError(f"v1 {name} review page is missing")
            metadata = page_path.stat()
            if metadata.st_mode & 0o777 != 0o600 or metadata.st_nlink != 1:
                raise PermissionError(f"v1 {name} review page must be single-link mode 0600")
            if hashlib.sha256(page_path.read_bytes()).hexdigest() != raw_page["sha256"]:
                raise RuntimeError(f"v1 {name} review page digest does not reproduce")
            expected_files.add(filename)
            pages.append({"page": expected_page, "sha256": raw_page["sha256"]})
        reviewed_pages[name] = pages
    if {path.name for path in review_root.iterdir()} != expected_files:
        raise RuntimeError("v1 blind-review directory has unexpected or missing files")
    return _ReviewDecisionContext(
        cohort_run_sha256=run_sha256,
        final_selection_sha256=str(selection_sha256),
        review_manifest_sha256=review_sha256,
        reviewed_pages=reviewed_pages,
    )


def create_visual_review_decision_input(
    *,
    cohort_root: Path,
    output_path: Path,
    decision: str,
    reviewer: str,
    notes: str,
    dino_audit_root: Path | None = None,
) -> dict[str, object]:
    """Create a human decision bound to all required raw and semantic sheets."""
    if decision not in {"approved", "rejected"}:
        raise ValueError("visual-review decision must be approved or rejected")
    if not reviewer.strip() or not notes.strip():
        raise ValueError("visual-review decision requires reviewer and notes")
    public, _bytes = _load_private_json(
        Path(cohort_root) / "training-cohort-public.json",
        label="owner cohort manifest",
    )
    if public.get("schema") == "triage-owner-training-cohort-public-v1":
        context = _load_v1_review_decision_context(cohort_root)
        payload: dict[str, object] = {
            "schema": VISUAL_REVIEW_DECISION_INPUT_SCHEMA,
            "decision": decision,
            "reviewer": reviewer.strip(),
            "notes": notes.strip(),
            "cohort_run_sha256": context.cohort_run_sha256,
            "final_selection_sha256": context.final_selection_sha256,
            "review_manifest_sha256": context.review_manifest_sha256,
            "reviewed_pages": context.reviewed_pages,
        }
    elif public.get("schema") == "triage-owner-training-cohort-public-v2":
        v2_context = _load_visual_review_context(cohort_root)
        from .training_cohort_dino_audit import (
            default_dino_audit_root,
            load_dino_diversity_audit,
        )

        dino_root = (
            Path(dino_audit_root)
            if dino_audit_root is not None
            else default_dino_audit_root(Path(cohort_root))
        )
        dino = load_dino_diversity_audit(
            dino_root,
            expected_raw_context={
                "cohort_run_sha256": v2_context.cohort_run_sha256,
                "final_selection_sha256": v2_context.final_selection_sha256,
                "final_preview_manifest_sha256": (v2_context.final_preview_manifest_sha256),
                "final_preview_count": v2_context.final_preview_count,
            },
        )
        payload = {
            "schema": V2_VISUAL_REVIEW_DECISION_INPUT_SCHEMA,
            "decision": decision,
            "reviewer": reviewer.strip(),
            "notes": notes.strip(),
            "cohort_run_sha256": v2_context.cohort_run_sha256,
            "final_selection_sha256": v2_context.final_selection_sha256,
            "review_manifest_sha256": v2_context.review_manifest_sha256,
            "reviewed_pages": v2_context.reviewed_pages,
            "dino_audit_manifest_sha256": dino.audit_manifest_sha256,
            "dino_nearest_pair_count": dino.nearest_pair_count,
            "dino_nearest_pairs_sha256": dino.nearest_pairs_sha256,
            "dino_reviewed_pages": dino.reviewed_pages,
        }
    else:
        raise RuntimeError("visual-review decision requires a sealed owner cohort")
    _write_create_only(Path(output_path), _canonical_bytes(payload))
    return payload


def _authenticate_source_v1_rejection(source_root: Path, decision_path: Path) -> str:
    context = _load_v1_review_decision_context(source_root)
    decision, decision_bytes = _load_private_json(
        decision_path,
        label="source v1 rejection decision input",
    )
    expected_keys = {
        "schema",
        "decision",
        "reviewer",
        "notes",
        "cohort_run_sha256",
        "final_selection_sha256",
        "review_manifest_sha256",
        "reviewed_pages",
    }
    if (
        set(decision) != expected_keys
        or decision.get("schema") != VISUAL_REVIEW_DECISION_INPUT_SCHEMA
    ):
        raise RuntimeError("source v1 rejection decision has the wrong schema")
    if decision.get("decision") != "rejected":
        raise RuntimeError("source v1 decision must be rejected before v2 reuse")
    if (
        not isinstance(decision.get("reviewer"), str)
        or not str(decision["reviewer"]).strip()
        or not isinstance(decision.get("notes"), str)
        or not str(decision["notes"]).strip()
        or decision.get("cohort_run_sha256") != context.cohort_run_sha256
        or decision.get("final_selection_sha256") != context.final_selection_sha256
        or decision.get("review_manifest_sha256") != context.review_manifest_sha256
        or decision.get("reviewed_pages") != context.reviewed_pages
    ):
        raise RuntimeError("source v1 rejection decision does not bind its exact review set")
    return hashlib.sha256(decision_bytes).hexdigest()


def _load_visual_review_context(cohort_root: Path) -> _VisualReviewContext:
    root = Path(cohort_root)
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("v2 cohort root must be a private mode-0700 directory")
    public, _public_bytes = _load_private_json(
        root / "training-cohort-public.json",
        label="v2 owner cohort manifest",
    )
    if public.get("schema") != "triage-owner-training-cohort-public-v2":
        raise RuntimeError("visual review requires an owner training v2 cohort")
    run_sha256 = _verify_self_digest(
        public,
        field="run_sha256",
        domain=_V2_RUN_DOMAIN,
        label="v2 owner cohort manifest",
    )
    if public.get("status") != "awaiting_visual_review":
        raise RuntimeError("v2 cohort is not awaiting visual review")
    final_selection = public.get("final_selection")
    private_evidence = public.get("private_evidence")
    if not isinstance(final_selection, dict) or not isinstance(private_evidence, dict):
        raise RuntimeError("v2 cohort review lineage is malformed")
    selection_sha256 = final_selection.get("selection_sha256")
    preview_manifest_sha256 = private_evidence.get("final_preview_manifest_sha256")
    preview_count = private_evidence.get("final_preview_count")
    expected_review_sha256 = private_evidence.get("review_manifest_sha256")
    if (
        not all(
            _is_sha256(value)
            for value in (
                selection_sha256,
                preview_manifest_sha256,
                expected_review_sha256,
            )
        )
        or type(preview_count) is not int
        or preview_count < 2
    ):
        raise RuntimeError("v2 cohort review lineage contains an invalid digest")

    review_root = root / "blind-review"
    if (
        review_root.is_symlink()
        or not review_root.is_dir()
        or review_root.stat().st_mode & 0o777 != 0o700
    ):
        raise PermissionError("v2 blind-review root must be a private mode-0700 directory")
    review, _review_bytes = _load_private_json(
        review_root / "review-manifest.json",
        label="v2 visual review manifest",
    )
    review_sha256 = _verify_self_digest(
        review,
        field="review_manifest_sha256",
        domain=_V2_REVIEW_DOMAIN,
        label="v2 visual review manifest",
    )
    if (
        review.get("schema") != "triage-private-owner-training-review-v2"
        or review.get("final_selection_sha256") != selection_sha256
        or review_sha256 != expected_review_sha256
    ):
        raise RuntimeError("v2 visual review manifest differs from its cohort seal")
    nearest = review.get("global_nearest_similarity")
    if not isinstance(nearest, dict) or nearest != final_selection.get("global_nearest_similarity"):
        raise RuntimeError("v2 global-nearest evidence differs across review seals")
    nearest_sha256 = _verify_self_digest(
        nearest,
        field="nearest_audit_sha256",
        domain=_NEAREST_AUDIT_DOMAIN,
        label="v2 global-nearest audit",
    )
    maximum = nearest.get("max_nearest_similarity")
    if (
        nearest.get("schema") != "triage-owner-training-global-nearest-audit-v2"
        or nearest.get("algorithm") != GLOBAL_NEAREST_ALGORITHM
        or nearest.get("threshold") != COSINE_DUPLICATE_THRESHOLD
        or nearest.get("comparison") != "strictly_below"
        or nearest.get("passed") is not True
        or nearest.get("failures") != []
        or not isinstance(maximum, (int, float))
        or isinstance(maximum, bool)
        or not math.isfinite(float(maximum))
        or float(maximum) + 1e-12 >= COSINE_DUPLICATE_THRESHOLD
    ):
        raise RuntimeError("v2 global-nearest similarity gate did not pass")

    groups = review.get("groups")
    required_groups = {"medoids", "random", "nearest"}
    if not isinstance(groups, dict) or set(groups) != required_groups:
        raise RuntimeError("v2 review must contain medoid, random, and nearest sheets")
    reviewed_pages: dict[str, list[dict[str, object]]] = {}
    expected_review_files = {"review-manifest.json"}
    for name in sorted(required_groups):
        raw_pages = groups[name]
        if not isinstance(raw_pages, list) or not raw_pages:
            raise RuntimeError(f"v2 {name} review group is empty")
        pages: list[dict[str, object]] = []
        for expected_page, raw_page in enumerate(raw_pages, start=1):
            if (
                not isinstance(raw_page, dict)
                or set(raw_page) != {"page", "tile_count", "sha256"}
                or raw_page.get("page") != expected_page
                or type(raw_page.get("tile_count")) is not int
                or int(raw_page["tile_count"]) < 1
                or not _is_sha256(raw_page.get("sha256"))
            ):
                raise RuntimeError(f"v2 {name} review page evidence is malformed")
            filename = f"{name}-{expected_page:02d}.png"
            page_path = review_root / filename
            if page_path.is_symlink() or not page_path.is_file():
                raise RuntimeError(f"v2 {name} review page is missing")
            page_metadata = page_path.stat()
            if page_metadata.st_mode & 0o777 != 0o600 or page_metadata.st_nlink != 1:
                raise PermissionError(f"v2 {name} review page must be single-link mode 0600")
            if hashlib.sha256(page_path.read_bytes()).hexdigest() != raw_page["sha256"]:
                raise RuntimeError(f"v2 {name} review page digest does not reproduce")
            expected_review_files.add(filename)
            pages.append({"page": expected_page, "sha256": raw_page["sha256"]})
        reviewed_pages[name] = pages
    if {path.name for path in review_root.iterdir()} != expected_review_files:
        raise RuntimeError("v2 blind-review directory has unexpected or missing files")
    return _VisualReviewContext(
        cohort_run_sha256=run_sha256,
        final_selection_sha256=str(selection_sha256),
        final_preview_manifest_sha256=str(preview_manifest_sha256),
        final_preview_count=preview_count,
        review_manifest_sha256=review_sha256,
        nearest_audit_sha256=nearest_sha256,
        max_nearest_similarity=float(maximum),
        reviewed_pages=reviewed_pages,
    )


def _reviewed_pages_sha256(
    reviewed_pages: dict[str, list[dict[str, object]]],
    *,
    lane: str,
) -> str:
    if lane not in {"raw", "dino"}:
        raise ValueError("visual-review page lane must be raw or dino")
    return _canonical_sha256(
        _VISUAL_APPROVAL_DOMAIN,
        {
            "schema": "triage-owner-training-reviewed-pages-v2",
            "lane": lane,
            "groups": reviewed_pages,
        },
    )


def _load_required_dino_context(
    cohort_root: Path,
    raw: _VisualReviewContext,
    dino_audit_root: Path | None,
) -> object:
    from .training_cohort_dino_audit import (
        default_dino_audit_root,
        load_dino_diversity_audit,
    )

    root = (
        Path(dino_audit_root)
        if dino_audit_root is not None
        else default_dino_audit_root(Path(cohort_root))
    )
    return load_dino_diversity_audit(
        root,
        expected_raw_context={
            "cohort_run_sha256": raw.cohort_run_sha256,
            "final_selection_sha256": raw.final_selection_sha256,
            "final_preview_manifest_sha256": raw.final_preview_manifest_sha256,
            "final_preview_count": raw.final_preview_count,
        },
    )


def finalize_visual_review_approval(
    *,
    cohort_root: Path,
    decision_path: Path,
    dino_audit_root: Path | None = None,
) -> dict[str, object]:
    """Approve only after the exact raw and DINO sheet groups both pass."""
    context = _load_visual_review_context(cohort_root)
    dino = _load_required_dino_context(cohort_root, context, dino_audit_root)
    decision, decision_bytes = _load_private_json(
        decision_path,
        label="owner visual-review decision input",
    )
    expected_keys = {
        "schema",
        "decision",
        "reviewer",
        "notes",
        "cohort_run_sha256",
        "final_selection_sha256",
        "review_manifest_sha256",
        "reviewed_pages",
        "dino_audit_manifest_sha256",
        "dino_nearest_pair_count",
        "dino_nearest_pairs_sha256",
        "dino_reviewed_pages",
    }
    if (
        set(decision) != expected_keys
        or decision.get("schema") != V2_VISUAL_REVIEW_DECISION_INPUT_SCHEMA
    ):
        raise RuntimeError("owner visual-review decision input has the wrong schema")
    if decision.get("decision") == "rejected":
        raise RuntimeError("owner visual-review decision rejected this cohort")
    if decision.get("decision") != "approved":
        raise RuntimeError("owner visual-review decision must be approved or rejected")
    reviewer = decision.get("reviewer")
    notes = decision.get("notes")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or not isinstance(notes, str)
        or not notes.strip()
    ):
        raise RuntimeError("approved visual review requires reviewer and notes")
    if (
        decision.get("cohort_run_sha256") != context.cohort_run_sha256
        or decision.get("final_selection_sha256") != context.final_selection_sha256
        or decision.get("review_manifest_sha256") != context.review_manifest_sha256
        or decision.get("reviewed_pages") != context.reviewed_pages
        or decision.get("dino_audit_manifest_sha256") != dino.audit_manifest_sha256
        or decision.get("dino_nearest_pair_count") != dino.nearest_pair_count
        or decision.get("dino_nearest_pairs_sha256") != dino.nearest_pairs_sha256
        or decision.get("dino_reviewed_pages") != dino.reviewed_pages
    ):
        raise RuntimeError(
            "owner visual-review decision does not bind the exact raw and DINO review set"
        )
    approval: dict[str, object] = {
        "schema": VISUAL_REVIEW_APPROVAL_SCHEMA,
        "status": "approved_for_training",
        "cohort_run_sha256": context.cohort_run_sha256,
        "final_selection_sha256": context.final_selection_sha256,
        "final_preview_manifest_sha256": context.final_preview_manifest_sha256,
        "review_manifest_sha256": context.review_manifest_sha256,
        "nearest_audit_sha256": context.nearest_audit_sha256,
        "duplicate_cosine_threshold": COSINE_DUPLICATE_THRESHOLD,
        "max_nearest_similarity": context.max_nearest_similarity,
        "decision_input_sha256": hashlib.sha256(decision_bytes).hexdigest(),
        "reviewed_pages_sha256": _reviewed_pages_sha256(
            context.reviewed_pages,
            lane="raw",
        ),
        "dino_audit_manifest_sha256": dino.audit_manifest_sha256,
        "dino_nearest_pair_count": dino.nearest_pair_count,
        "dino_nearest_pairs_sha256": dino.nearest_pairs_sha256,
        "dino_private_index_sha256": dino.private_index_sha256,
        "dino_pixel_set_sha256": dino.pixel_set_sha256,
        "dino_encoder_sha256": dino.encoder_sha256,
        "dino_vector_set_sha256": dino.vector_set_sha256,
        "dino_max_nearest_similarity": dino.max_nearest_similarity,
        "dino_reviewed_pages_sha256": _reviewed_pages_sha256(
            dino.reviewed_pages,
            lane="dino",
        ),
        "reviewer_sha256": hashlib.sha256(reviewer.strip().encode()).hexdigest(),
    }
    approval["approval_sha256"] = _canonical_sha256(
        _VISUAL_APPROVAL_DOMAIN,
        approval,
    )
    root = Path(cohort_root)
    _write_create_only(root / VISUAL_REVIEW_APPROVAL_FILENAME, _canonical_bytes(approval))
    return load_visual_review_approval(root, dino_audit_root=dino_audit_root)


def load_visual_review_approval(
    cohort_root: Path,
    *,
    dino_audit_root: Path | None = None,
) -> dict[str, object]:
    """Authenticate the create-only approval consumed by downstream training."""
    context = _load_visual_review_context(cohort_root)
    dino = _load_required_dino_context(cohort_root, context, dino_audit_root)
    approval_path = Path(cohort_root) / VISUAL_REVIEW_APPROVAL_FILENAME
    if not approval_path.exists():
        raise RuntimeError("v2 owner training cohort is not approved for training")
    approval, _approval_bytes = _load_private_json(
        approval_path,
        label="owner visual-review approval",
    )
    expected_keys = {
        "schema",
        "status",
        "cohort_run_sha256",
        "final_selection_sha256",
        "final_preview_manifest_sha256",
        "review_manifest_sha256",
        "nearest_audit_sha256",
        "duplicate_cosine_threshold",
        "max_nearest_similarity",
        "decision_input_sha256",
        "reviewed_pages_sha256",
        "dino_audit_manifest_sha256",
        "dino_nearest_pair_count",
        "dino_nearest_pairs_sha256",
        "dino_private_index_sha256",
        "dino_pixel_set_sha256",
        "dino_encoder_sha256",
        "dino_vector_set_sha256",
        "dino_max_nearest_similarity",
        "dino_reviewed_pages_sha256",
        "reviewer_sha256",
        "approval_sha256",
    }
    if set(approval) != expected_keys or approval.get("schema") != VISUAL_REVIEW_APPROVAL_SCHEMA:
        raise RuntimeError("owner visual-review approval has the wrong schema")
    _verify_self_digest(
        approval,
        field="approval_sha256",
        domain=_VISUAL_APPROVAL_DOMAIN,
        label="owner visual-review approval",
    )
    expected = {
        "status": "approved_for_training",
        "cohort_run_sha256": context.cohort_run_sha256,
        "final_selection_sha256": context.final_selection_sha256,
        "final_preview_manifest_sha256": context.final_preview_manifest_sha256,
        "review_manifest_sha256": context.review_manifest_sha256,
        "nearest_audit_sha256": context.nearest_audit_sha256,
        "duplicate_cosine_threshold": COSINE_DUPLICATE_THRESHOLD,
        "max_nearest_similarity": context.max_nearest_similarity,
        "reviewed_pages_sha256": _reviewed_pages_sha256(
            context.reviewed_pages,
            lane="raw",
        ),
        "dino_audit_manifest_sha256": dino.audit_manifest_sha256,
        "dino_nearest_pair_count": dino.nearest_pair_count,
        "dino_nearest_pairs_sha256": dino.nearest_pairs_sha256,
        "dino_private_index_sha256": dino.private_index_sha256,
        "dino_pixel_set_sha256": dino.pixel_set_sha256,
        "dino_encoder_sha256": dino.encoder_sha256,
        "dino_vector_set_sha256": dino.vector_set_sha256,
        "dino_max_nearest_similarity": dino.max_nearest_similarity,
        "dino_reviewed_pages_sha256": _reviewed_pages_sha256(
            dino.reviewed_pages,
            lane="dino",
        ),
    }
    if any(approval.get(field) != value for field, value in expected.items()):
        raise RuntimeError("owner visual-review approval differs from sealed review evidence")
    for field in ("decision_input_sha256", "reviewer_sha256"):
        if not _is_sha256(approval.get(field)):
            raise RuntimeError("owner visual-review approval contains an invalid digest")
    return approval
