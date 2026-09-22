"""Build a provisional, label-blind, visually diverse certification cohort.

This module deliberately stops before truth reservation.  It scans no labels,
captions, semantic-search results, or model outputs: full-library metadata picks
the candidate pool and raw preview pixels provide the only diversity signal.
"""

from __future__ import annotations

import hashlib
import heapq
import io
import json
import math
import os
import resource
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .cohorts import (
    CohortAudit,
    CohortQualityError,
    CohortSelection,
    CohortSpec,
    InventoryAsset,
    InventorySnapshot,
    ReservationBlocklist,
    TruthReservationLedger,
    audit_cohort,
    cohort_selection_sha256,
    select_cohort,
)
from .image_diversity import (
    DEFAULT_VISUAL_CLUSTERS,
    DESCRIPTOR_VERSION,
    PixelDescriptor,
    VisualClustering,
    cluster_descriptors,
    confirmed_near_duplicate,
    describe_preview_bytes,
    estimated_descriptor_memory_bytes,
    selected_cluster_metrics,
)
from .preview_store import (
    MAX_PREVIEW_BYTES,
    pin_available_preview_store,
    pin_preview_store,
)

MAX_VISUAL_PROCESS_BYTES = 8 * 1024**3
DEFAULT_CANDIDATE_TARGET = 2_000
DEFAULT_CANDIDATE_SURPLUS = 500
DEFAULT_FINAL_TARGET = 400
DEFAULT_CLUSTER_COVERAGE_GATE = 0.90
DEFAULT_CLUSTER_ENTROPY_GATE = 0.90
_VISUAL_RUN_DOMAIN = b"triage-label-blind-visual-cohort-v1\0"
_PRIVATE_PLAN_DOMAIN = b"triage-private-visual-plan-v1\0"


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


def _canonical_sha256(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + _canonical_bytes(payload)).hexdigest()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _stable_rank(seed: str, scope: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{value}".encode()).hexdigest()


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _assert_memory_limit(limit_bytes: int) -> None:
    observed = _peak_rss_bytes()
    if observed >= limit_bytes:
        raise MemoryError(
            f"visual-cohort process reached {observed / 1024**3:.2f} GiB; "
            f"limit is {limit_bytes / 1024**3:.2f} GiB"
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


@dataclass(frozen=True, slots=True)
class VisualCohortConfig:
    """Frozen knobs for one provisional visual-cohort run."""

    seed: str = "location-recovery-1b-visual-v1"
    candidate_target: int = DEFAULT_CANDIDATE_TARGET
    candidate_surplus: int = DEFAULT_CANDIDATE_SURPLUS
    final_target: int = DEFAULT_FINAL_TARGET
    cluster_count: int = DEFAULT_VISUAL_CLUSTERS
    min_year_quarter_coverage: float = 0.90
    max_sqrt_quota_tv: float = 0.10
    min_cluster_coverage: float = DEFAULT_CLUSTER_COVERAGE_GATE
    min_cluster_entropy: float = DEFAULT_CLUSTER_ENTROPY_GATE
    workers: int = 4
    memory_limit_bytes: int = MAX_VISUAL_PROCESS_BYTES
    nearest_pair_count: int = 32
    review_columns: int = 5
    review_rows: int = 4

    def __post_init__(self) -> None:
        if not self.seed.strip():
            raise ValueError("visual cohort seed must not be empty")
        if self.candidate_target < 2 or not 1 <= self.final_target <= self.candidate_target:
            raise ValueError("visual cohort sizes must satisfy 1 <= final <= candidate")
        if self.candidate_surplus < 1:
            raise ValueError("candidate surplus must be positive")
        if not 2 <= self.cluster_count <= self.candidate_target:
            raise ValueError("cluster count must fit inside the candidate pool")
        if not 0.0 <= self.min_year_quarter_coverage <= 1.0:
            raise ValueError("year-quarter coverage gate must be between zero and one")
        if not 0.0 <= self.max_sqrt_quota_tv <= 1.0:
            raise ValueError("sqrt-quota TV gate must be between zero and one")
        if not 0.0 <= self.min_cluster_coverage <= 1.0:
            raise ValueError("visual cluster coverage gate must be between zero and one")
        if not 0.0 <= self.min_cluster_entropy <= 1.0:
            raise ValueError("visual cluster entropy gate must be between zero and one")
        if not 1 <= self.workers <= 8:
            raise ValueError("preview workers must be between one and eight")
        if not 1 <= self.memory_limit_bytes <= MAX_VISUAL_PROCESS_BYTES:
            raise ValueError("visual process memory limit must be at most 8 GiB")
        if self.nearest_pair_count < 1:
            raise ValueError("nearest-pair count must be positive")
        if self.review_columns < 1 or self.review_rows < 1:
            raise ValueError("review sheet dimensions must be positive")
        descriptor_bound = estimated_descriptor_memory_bytes(self.candidate_target)
        fetch_bound = self.workers * 2 * MAX_PREVIEW_BYTES
        if descriptor_bound + fetch_bound >= self.memory_limit_bytes:
            raise ValueError("configured candidate pool cannot respect the memory limit")

    def public_dict(self) -> dict[str, object]:
        return {
            "seed_sha256": hashlib.sha256(self.seed.encode()).hexdigest(),
            "candidate_target": self.candidate_target,
            "candidate_surplus": self.candidate_surplus,
            "final_target": self.final_target,
            "cluster_count": self.cluster_count,
            "min_year_quarter_coverage": self.min_year_quarter_coverage,
            "max_sqrt_quota_tv": self.max_sqrt_quota_tv,
            "min_cluster_coverage": self.min_cluster_coverage,
            "min_cluster_entropy": self.min_cluster_entropy,
            "workers": self.workers,
            "memory_limit_bytes": self.memory_limit_bytes,
            "nearest_pair_count": self.nearest_pair_count,
        }


class ThreadLocalPreviewFetcher:
    """Give every preview worker its own synchronous Immich client.

    ``SyncImmichClient`` owns a persistent asyncio event loop.  Sharing one
    instance between worker threads can drive that loop concurrently, so the
    direct preview path keeps one client per executor thread instead.
    """

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._clients: list[Any] = []
        self._closed = False

    def __call__(self, asset_id: str) -> bytes:
        client = getattr(self._local, "client", None)
        if client is None:
            with self._lock:
                if self._closed:
                    raise RuntimeError("preview fetcher is closed")
                client = self._client_factory()
                self._clients.append(client)
            self._local.client = client
        payload = client.get_asset_thumbnail(asset_id, "preview")
        if not isinstance(payload, bytes):
            raise TypeError("Immich preview endpoint did not return bytes")
        return payload

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = tuple(self._clients)
        first_error: Exception | None = None
        for client in clients:
            try:
                client.close()
            except Exception as error:  # pragma: no cover - defensive close path
                first_error = first_error or error
        if first_error is not None:
            raise first_error

    def __enter__(self) -> ThreadLocalPreviewFetcher:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    row: InventoryAsset
    audit_id: str
    image_path: Path
    descriptor: PixelDescriptor
    cluster_label: int
    cluster_distance: float


@dataclass(frozen=True, slots=True)
class VisualCohortAudit:
    temporal: CohortAudit
    represented_clusters: int
    cluster_coverage: float
    normalized_cluster_entropy: float
    largest_cluster_share: float
    near_duplicate_rejections: int
    selected_near_duplicate_pairs: int
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
            "near_duplicate_rejections": self.near_duplicate_rejections,
            "selected_near_duplicate_pairs": self.selected_near_duplicate_pairs,
            "truth_overlap": {
                "asset_ids": self.truth_asset_overlap,
                "components": self.truth_component_overlap,
                "moments": self.truth_moment_overlap,
            },
            "passed": self.passed,
            "failures": list(self.failures),
        }


class VisualCohortQualityError(RuntimeError):
    def __init__(self, audit: VisualCohortAudit) -> None:
        super().__init__("visual cohort quality gates failed: " + ", ".join(audit.failures))
        self.audit = audit


@dataclass(frozen=True, slots=True)
class VisualSelection:
    cohort: CohortSelection
    selected: tuple[VisualCandidate, ...]
    audit: VisualCohortAudit


@dataclass(frozen=True, slots=True)
class VisualCohortRun:
    candidate_pool: CohortSelection
    visual_selection: VisualSelection
    candidate_clustering: VisualClustering
    public_manifest: Mapping[str, object]


def _eligible_rows(
    rows: Sequence[InventoryAsset], blocked: ReservationBlocklist
) -> tuple[InventoryAsset, ...]:
    return tuple(
        row
        for row in rows
        if row.asset_id not in blocked.asset_ids
        and row.component_key not in blocked.component_keys
        and row.moment_key not in blocked.moment_keys
    )


def _candidate_spec(config: VisualCohortConfig) -> CohortSpec:
    return CohortSpec(
        target_size=config.candidate_target,
        minimum_size=config.candidate_target,
        max_per_day=1,
        max_per_moment=1,
        min_distinct_days=config.candidate_target,
        min_year_quarter_coverage=config.min_year_quarter_coverage,
        max_sqrt_quota_tv=config.max_sqrt_quota_tv,
    )


def _candidate_surplus_spec(config: VisualCohortConfig) -> CohortSpec:
    target = config.candidate_target + config.candidate_surplus
    return CohortSpec(
        target_size=target,
        minimum_size=target,
        max_per_day=1,
        max_per_moment=1,
        min_distinct_days=target,
        min_year_quarter_coverage=config.min_year_quarter_coverage,
        max_sqrt_quota_tv=config.max_sqrt_quota_tv,
    )


def _final_spec(config: VisualCohortConfig) -> CohortSpec:
    return CohortSpec(
        target_size=config.final_target,
        minimum_size=config.final_target,
        max_per_day=1,
        max_per_moment=1,
        min_distinct_days=config.final_target,
        min_year_quarter_coverage=config.min_year_quarter_coverage,
        max_sqrt_quota_tv=config.max_sqrt_quota_tv,
    )


def select_metadata_candidate_pool(
    rows: Sequence[InventoryAsset],
    *,
    blocked: ReservationBlocklist,
    config: VisualCohortConfig,
) -> CohortSelection:
    """Choose exactly one metadata-only candidate per day, moment, and component."""
    return select_cohort(
        rows,
        name="location-certification-visual-candidates-v1",
        spec=_candidate_spec(config),
        seed=config.seed,
        blocked=blocked,
    )


def select_metadata_candidate_surplus(
    rows: Sequence[InventoryAsset],
    *,
    blocked: ReservationBlocklist,
    config: VisualCohortConfig,
) -> CohortSelection:
    """Choose the deterministic core plus replacements before preview fetching."""
    return select_cohort(
        rows,
        name="location-certification-visual-candidates-v1",
        spec=_candidate_surplus_spec(config),
        seed=config.seed,
        blocked=blocked,
    )


def _safe_store_image(store: Path, image_relpath: str) -> Path:
    root = store.resolve()
    path = (store / image_relpath).resolve()
    if not path.is_relative_to(root):
        raise ValueError("preview index contains a path outside its private store")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _load_store_rows(store: Path, *, selection_sha256: str) -> dict[str, dict[str, object]]:
    manifest_path = store / "manifest.json"
    private_path = store / "private-index.json"
    if private_path.stat().st_mode & 0o077:
        raise PermissionError("private preview index must be mode 0600")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    private = json.loads(private_path.read_text(encoding="utf-8"))
    if manifest.get("selection_sha256") != selection_sha256:
        raise ValueError("pinned preview manifest has a different selection")
    if private.get("selection_sha256") != selection_sha256:
        raise ValueError("private preview index has a different selection")
    if _file_sha256(private_path) != manifest.get("private_index_sha256"):
        raise ValueError("private preview index digest does not reproduce")
    rows = {str(row["asset_id"]): dict(row) for row in private["rows"]}
    if len(rows) != len(private["rows"]):
        raise ValueError("private preview index contains duplicate asset ids")
    return rows


def _load_available_store_rows(
    store: Path, *, selection_sha256: str
) -> dict[str, dict[str, object]]:
    manifest_path = store / "manifest.json"
    private_path = store / "private-index.json"
    if private_path.stat().st_mode & 0o077:
        raise PermissionError("private preview availability index must be mode 0600")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    private = json.loads(private_path.read_text(encoding="utf-8"))
    if manifest.get("selection_sha256") != selection_sha256:
        raise ValueError("preview availability manifest has a different selection")
    if private.get("selection_sha256") != selection_sha256:
        raise ValueError("private preview availability index has a different selection")
    if _file_sha256(private_path) != manifest.get("private_index_sha256"):
        raise ValueError("private preview availability index digest does not reproduce")
    rows = {str(row["asset_id"]): dict(row) for row in private.get("available_rows", [])}
    if len(rows) != len(private.get("available_rows", [])):
        raise ValueError("private preview availability index has duplicate asset ids")
    return rows


def describe_candidate_universe(
    candidate_store: Path,
    candidate_pool: CohortSelection,
    *,
    config: VisualCohortConfig,
) -> tuple[tuple[VisualCandidate, ...], VisualClustering, str]:
    """Stream pinned previews into raw-pixel descriptors, then freeze 64 clusters."""
    private_rows = _load_store_rows(
        candidate_store,
        selection_sha256=candidate_pool.selection_sha256,
    )
    ordered_rows = tuple(sorted(candidate_pool.rows, key=lambda row: row.asset_id))
    if set(private_rows) != {row.asset_id for row in ordered_rows}:
        raise ValueError("candidate preview store differs from the metadata candidate pool")

    descriptors: list[PixelDescriptor] = []
    descriptor_records: list[dict[str, object]] = []
    image_paths: list[Path] = []
    audit_ids: list[str] = []
    for index, row in enumerate(ordered_rows, start=1):
        private_row = private_rows[row.asset_id]
        image_path = _safe_store_image(candidate_store, str(private_row["image_relpath"]))
        payload = image_path.read_bytes()
        descriptor = describe_preview_bytes(payload)
        if descriptor.preview_sha256 != private_row["preview_sha256"]:
            raise ValueError("candidate preview bytes differ from their pinned digest")
        descriptors.append(descriptor)
        image_paths.append(image_path)
        audit_ids.append(str(private_row["audit_id"]))
        descriptor_records.append(
            {
                "audit_id": str(private_row["audit_id"]),
                "preview_sha256": descriptor.preview_sha256,
                "average_hash": descriptor.average_hash,
                "difference_hash": descriptor.difference_hash,
                "aspect_ratio": descriptor.aspect_ratio,
                "vector_sha256": hashlib.sha256(descriptor.vector.tobytes()).hexdigest(),
            }
        )
        if index % 128 == 0:
            _assert_memory_limit(config.memory_limit_bytes)

    clustering = cluster_descriptors(
        descriptors,
        cluster_count=config.cluster_count,
        seed=int(hashlib.sha256(config.seed.encode()).hexdigest()[:8], 16),
    )
    candidates = tuple(
        VisualCandidate(
            row=row,
            audit_id=audit_id,
            image_path=image_path,
            descriptor=descriptor,
            cluster_label=int(label),
            cluster_distance=float(distance),
        )
        for row, audit_id, image_path, descriptor, label, distance in zip(
            ordered_rows,
            audit_ids,
            image_paths,
            descriptors,
            clustering.labels,
            clustering.distances,
            strict=True,
        )
    )
    descriptor_sha256 = _canonical_sha256(
        _VISUAL_RUN_DOMAIN,
        {
            "descriptor_version": DESCRIPTOR_VERSION,
            "candidate_selection_sha256": candidate_pool.selection_sha256,
            "records": descriptor_records,
        },
    )
    _assert_memory_limit(config.memory_limit_bytes)
    return candidates, clustering, descriptor_sha256


def _truth_overlap(
    rows: Sequence[InventoryAsset], blocked: ReservationBlocklist
) -> tuple[int, int, int]:
    return (
        sum(row.asset_id in blocked.asset_ids for row in rows),
        sum(row.component_key in blocked.component_keys for row in rows),
        sum(row.moment_key in blocked.moment_keys for row in rows),
    )


def _selected_near_duplicate_pairs(selected: Sequence[VisualCandidate]) -> int:
    return sum(
        confirmed_near_duplicate(left.descriptor, right.descriptor)
        for index, left in enumerate(selected)
        for right in selected[index + 1 :]
    )


def _balanced_stratum_targets(
    candidates: Sequence[VisualCandidate],
    *,
    eligible_strata: Counter[str],
    target: int,
    seed: str,
) -> dict[str, int]:
    """Reserve feasible stratum coverage, then allocate by square-root weight."""
    capacities = Counter(candidate.row.stratum for candidate in candidates)
    feasible = [
        stratum
        for stratum, capacity in capacities.items()
        if capacity > 0 and eligible_strata[stratum] > 0
    ]
    coverage_order = sorted(
        feasible,
        key=lambda stratum: (
            -math.sqrt(eligible_strata[stratum]),
            _stable_rank(seed, "final-stratum-coverage", stratum),
            stratum,
        ),
    )
    covered_strata = coverage_order[: min(target, len(feasible))]
    targets: Counter[str] = Counter(dict.fromkeys(covered_strata, 1))
    while sum(targets.values()) < target:
        available = [
            stratum
            for stratum, capacity in capacities.items()
            if targets[stratum] < capacity and eligible_strata[stratum] > 0
        ]
        if not available:
            break
        chosen = min(
            available,
            key=lambda stratum: (
                (targets[stratum] + 0.5) / math.sqrt(eligible_strata[stratum]),
                _stable_rank(seed, "final-stratum-quota", stratum),
                stratum,
            ),
        )
        targets[chosen] += 1
    return dict(targets)


def select_final_visual_cohort(
    candidates: Sequence[VisualCandidate],
    *,
    eligible_inventory: Sequence[InventoryAsset],
    blocked: ReservationBlocklist,
    config: VisualCohortConfig,
    require_quality: bool = True,
) -> VisualSelection:
    """Greedily flatten visual clusters while preserving square-root time quotas."""
    if len({candidate.row.asset_id for candidate in candidates}) != len(candidates):
        raise ValueError("visual candidates contain duplicate asset ids")
    eligible = _eligible_rows(eligible_inventory, blocked)
    eligible_by_id = {row.asset_id: row for row in eligible}
    if any(eligible_by_id.get(candidate.row.asset_id) != candidate.row for candidate in candidates):
        raise ValueError("visual candidate lies outside the truth-filtered inventory")
    candidate_truth_overlap = _truth_overlap([candidate.row for candidate in candidates], blocked)
    if any(candidate_truth_overlap):
        raise ValueError("visual candidate pool overlaps reserved truth")

    eligible_strata = Counter(row.stratum for row in eligible)
    near_duplicate_rejections: set[str] = set()
    deduplicated_candidates: list[VisualCandidate] = []
    stable_candidates = sorted(
        candidates,
        key=lambda candidate: (
            _stable_rank(config.seed, "raw-pixel-dedup", candidate.row.asset_id),
            candidate.row.asset_id,
        ),
    )
    for index, candidate in enumerate(stable_candidates, start=1):
        if any(
            confirmed_near_duplicate(candidate.descriptor, retained.descriptor)
            for retained in deduplicated_candidates
        ):
            near_duplicate_rejections.add(candidate.row.asset_id)
            continue
        deduplicated_candidates.append(candidate)
        if index % 128 == 0:
            _assert_memory_limit(config.memory_limit_bytes)

    selected: list[VisualCandidate] = []
    selected_ids: set[str] = set()
    selected_days: set[str] = set()
    selected_moments: set[str] = set()
    selected_components: set[str] = set()
    selected_clusters: Counter[int] = Counter()
    selected_strata: Counter[str] = Counter()
    stratum_targets = _balanced_stratum_targets(
        deduplicated_candidates,
        eligible_strata=eligible_strata,
        target=config.final_target,
        seed=config.seed,
    )
    ordered_candidates = tuple(
        sorted(
            deduplicated_candidates,
            key=lambda candidate: (
                _stable_rank(config.seed, "visual-candidate", candidate.row.asset_id),
                candidate.row.asset_id,
            ),
        )
    )

    while len(selected) < config.final_target:
        available: list[tuple[object, ...]] = []
        for candidate in ordered_candidates:
            row = candidate.row
            if row.asset_id in selected_ids:
                continue
            if (
                row.capture_day in selected_days
                or row.moment_key in selected_moments
                or row.component_key in selected_components
                or selected_strata[row.stratum] >= stratum_targets.get(row.stratum, 0)
            ):
                continue
            available.append(
                (
                    selected_clusters[candidate.cluster_label],
                    selected_strata[row.stratum] / stratum_targets[row.stratum],
                    candidate.cluster_distance,
                    _stable_rank(config.seed, "visual-final", row.asset_id),
                    row.asset_id,
                    candidate,
                )
            )
        if not available:
            break
        chosen = min(available)[-1]
        selected.append(chosen)
        selected_ids.add(chosen.row.asset_id)
        selected_days.add(chosen.row.capture_day)
        selected_moments.add(chosen.row.moment_key)
        selected_components.add(chosen.row.component_key)
        selected_clusters[chosen.cluster_label] += 1
        selected_strata[chosen.row.stratum] += 1
        if len(selected) % 64 == 0:
            _assert_memory_limit(config.memory_limit_bytes)

    selected_tuple = tuple(selected)
    selected_rows = tuple(candidate.row for candidate in selected_tuple)
    temporal = audit_cohort(selected_rows, eligible, _final_spec(config))
    if selected_tuple:
        metrics = selected_cluster_metrics(
            [candidate.cluster_label for candidate in selected_tuple],
            universe_cluster_count=config.cluster_count,
        )
    else:
        metrics = {
            "represented_clusters": 0,
            "cluster_coverage": 0.0,
            "normalized_cluster_entropy": 0.0,
            "largest_cluster_share": 0.0,
        }
    duplicate_pairs = _selected_near_duplicate_pairs(selected_tuple)
    truth_overlap = _truth_overlap(selected_rows, blocked)
    failures = list(temporal.failures)
    if len(selected_tuple) != config.final_target:
        failures.append("target_not_reached")
    if float(metrics["cluster_coverage"]) + 1e-12 < config.min_cluster_coverage:
        failures.append("visual_cluster_coverage_below_gate")
    if float(metrics["normalized_cluster_entropy"]) + 1e-12 < config.min_cluster_entropy:
        failures.append("visual_cluster_entropy_below_gate")
    if duplicate_pairs:
        failures.append("raw_pixel_near_duplicates_selected")
    if any(truth_overlap):
        failures.append("reserved_truth_overlap")
    audit = VisualCohortAudit(
        temporal=temporal,
        represented_clusters=int(metrics["represented_clusters"]),
        cluster_coverage=float(metrics["cluster_coverage"]),
        normalized_cluster_entropy=float(metrics["normalized_cluster_entropy"]),
        largest_cluster_share=float(metrics["largest_cluster_share"]),
        near_duplicate_rejections=len(near_duplicate_rejections),
        selected_near_duplicate_pairs=duplicate_pairs,
        truth_asset_overlap=truth_overlap[0],
        truth_component_overlap=truth_overlap[1],
        truth_moment_overlap=truth_overlap[2],
        failures=tuple(dict.fromkeys(failures)),
    )
    if require_quality and not audit.passed:
        raise VisualCohortQualityError(audit)
    cohort = CohortSelection(
        name="fresh-location-certification-provisional-v2",
        rows=selected_rows,
        selection_sha256=cohort_selection_sha256(selected_rows),
        audit=temporal,
    )
    return VisualSelection(cohort=cohort, selected=selected_tuple, audit=audit)


def nearest_descriptor_pairs(
    candidates: Sequence[VisualCandidate], *, limit: int
) -> tuple[tuple[int, int, float], ...]:
    """Find the nearest raw-descriptor pairs with bounded, deterministic memory."""
    if limit < 1:
        raise ValueError("nearest-pair limit must be positive")
    if len(candidates) < 2:
        return ()
    vectors = np.stack([candidate.descriptor.vector for candidate in candidates]).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms != 0)
    pairs: list[tuple[float, int, int, int]] = []
    for left in range(len(candidates) - 1):
        similarities = normalized[left + 1 :] @ normalized[left]
        for offset, similarity in enumerate(similarities, start=left + 1):
            key = ":".join(sorted((candidates[left].audit_id, candidates[offset].audit_id)))
            tie_rank = int(hashlib.sha256(key.encode()).hexdigest(), 16)
            entry = (float(similarity), -tie_rank, left, offset)
            if len(pairs) < limit:
                heapq.heappush(pairs, entry)
            elif entry[:2] > pairs[0][:2]:
                heapq.heapreplace(pairs, entry)
    pairs.sort(key=lambda entry: (-entry[0], -entry[1]))
    return tuple((left, right, similarity) for similarity, _rank, left, right in pairs)


def _render_pages(
    entries: Sequence[tuple[Path, str]],
    *,
    output_dir: Path,
    stem: str,
    columns: int,
    rows: int,
) -> tuple[dict[str, object], ...]:
    page_size = columns * rows
    tile_width, image_height, caption_height = 256, 192, 38
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
            draw.multiline_text(
                (x + 5, y + image_height + 4),
                caption,
                fill="black",
                spacing=2,
            )
        destination = output_dir / f"{stem}-{page_number:02d}.png"
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG", optimize=True)
        payload = buffer.getvalue()
        _write_create_only_idempotent(destination, payload)
        pages.append(
            {
                "page": page_number,
                "tile_count": len(page_entries),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return tuple(pages)


def _review_store_rows(store: Path, selection_sha256: str) -> dict[str, dict[str, object]]:
    return _load_store_rows(store, selection_sha256=selection_sha256)


def render_private_review_bundle(
    *,
    output_dir: Path,
    candidate_store: Path,
    final_store: Path,
    candidates: Sequence[VisualCandidate],
    clustering: VisualClustering,
    selection: VisualSelection,
    config: VisualCohortConfig,
) -> dict[str, object]:
    """Render blinded medoids, closest selected pairs, and the full selection."""
    _private_directory(output_dir)
    candidate_store_rows = _review_store_rows(
        candidate_store,
        selection_sha256=cohort_selection_sha256(candidate.row for candidate in candidates),
    )
    final_store_rows = _review_store_rows(
        final_store,
        selection_sha256=selection.cohort.selection_sha256,
    )

    medoid_entries: list[tuple[Path, str]] = []
    for index in clustering.medoid_indices:
        candidate = candidates[index]
        store_row = candidate_store_rows[candidate.row.asset_id]
        medoid_entries.append(
            (
                _safe_store_image(candidate_store, str(store_row["image_relpath"])),
                f"cluster {candidate.cluster_label:02d} | {store_row['audit_id']}",
            )
        )
    medoid_entries.sort(key=lambda entry: entry[1])

    selected_by_asset = {candidate.row.asset_id: candidate for candidate in selection.selected}
    ordered_selected = tuple(
        selected_by_asset[asset_id]
        for asset_id in sorted(
            selected_by_asset,
            key=lambda asset_id: str(final_store_rows[asset_id]["audit_id"]),
        )
    )
    overview_entries = [
        (
            _safe_store_image(
                final_store,
                str(final_store_rows[candidate.row.asset_id]["image_relpath"]),
            ),
            (
                f"cluster {candidate.cluster_label:02d} | "
                f"{final_store_rows[candidate.row.asset_id]['audit_id']}"
            ),
        )
        for candidate in ordered_selected
    ]
    pair_entries: list[tuple[Path, str]] = []
    for pair_number, (left, right, similarity) in enumerate(
        nearest_descriptor_pairs(ordered_selected, limit=config.nearest_pair_count),
        start=1,
    ):
        for side, candidate in (("A", ordered_selected[left]), ("B", ordered_selected[right])):
            store_row = final_store_rows[candidate.row.asset_id]
            pair_entries.append(
                (
                    _safe_store_image(final_store, str(store_row["image_relpath"])),
                    f"pair {pair_number:02d}{side} | {store_row['audit_id']}\ncos={similarity:.4f}",
                )
            )

    groups = {
        "medoids": _render_pages(
            medoid_entries,
            output_dir=output_dir,
            stem="cluster-medoids",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "nearest_pairs": _render_pages(
            pair_entries,
            output_dir=output_dir,
            stem="nearest-selected-pairs",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "selected_overview": _render_pages(
            overview_entries,
            output_dir=output_dir,
            stem="selected-overview",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
    }
    manifest: dict[str, object] = {
        "schema": "triage-private-blind-visual-review-v1",
        "blindness": "raw pixels and opaque audit ids only; no truth or model output",
        "candidate_selection_sha256": cohort_selection_sha256(
            candidate.row for candidate in candidates
        ),
        "final_selection_sha256": selection.cohort.selection_sha256,
        "groups": groups,
    }
    manifest["review_manifest_sha256"] = _canonical_sha256(_PRIVATE_PLAN_DOMAIN, manifest)
    _write_create_only_idempotent(output_dir / "review-manifest.json", _canonical_bytes(manifest))
    return manifest


def _private_selection_row(candidate: VisualCandidate, *, final_audit_id: str) -> dict[str, object]:
    row = candidate.row
    return {
        "asset_id": row.asset_id,
        "component_key": row.component_key,
        "moment_key": row.moment_key,
        "capture_day": row.capture_day,
        "stratum": row.stratum,
        "candidate_audit_id": candidate.audit_id,
        "final_audit_id": final_audit_id,
        "cluster_label": candidate.cluster_label,
        "preview_sha256": candidate.descriptor.preview_sha256,
    }


def build_provisional_visual_cohort(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    output_dir: Path,
    fetch_preview: Callable[[str], bytes],
    config: VisualCohortConfig = VisualCohortConfig(),
    progress: Callable[[int, int], None] | None = None,
) -> VisualCohortRun:
    """Build and inspectable provisional cohort without mutating the truth ledger."""
    _assert_memory_limit(config.memory_limit_bytes)
    ledger_sha256 = ledger.ledger_sha256()
    blocked = ledger.blocklist()
    candidate_surplus = select_metadata_candidate_surplus(
        snapshot.rows,
        blocked=blocked,
        config=config,
    )
    output_dir = Path(output_dir)
    _private_directory(output_dir)
    availability_store = output_dir / "candidate-preview-acquisition-v2"
    availability_manifest = pin_available_preview_store(
        availability_store,
        candidate_surplus.rows,
        cohort_name="location-visual-candidate-acquisition-v2",
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=candidate_surplus.selection_sha256,
        fetch_preview=fetch_preview,
        audit_prefix="A",
        minimum_successes=config.candidate_target,
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
        progress=progress,
        reuse_stores=(output_dir / "candidate-previews",),
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed while surplus previews were being pinned")

    available_store_rows = _load_available_store_rows(
        availability_store,
        selection_sha256=candidate_surplus.selection_sha256,
    )
    surplus_by_id = {row.asset_id: row for row in candidate_surplus.rows}
    available_inventory = tuple(
        surplus_by_id[asset_id] for asset_id in sorted(available_store_rows)
    )
    candidate_pool = select_cohort(
        available_inventory,
        name="location-certification-visual-candidates-available-v2",
        spec=_candidate_spec(config),
        seed=config.seed,
    )
    full_eligible = _eligible_rows(snapshot.rows, blocked)
    full_inventory_audit = audit_cohort(
        candidate_pool.rows,
        full_eligible,
        _candidate_spec(config),
    )
    if not full_inventory_audit.passed:
        raise CohortQualityError(full_inventory_audit)
    candidate_pool = CohortSelection(
        name=candidate_pool.name,
        rows=candidate_pool.rows,
        selection_sha256=cohort_selection_sha256(candidate_pool.rows),
        audit=full_inventory_audit,
    )

    candidate_store = output_dir / "candidate-previews-v2"
    candidate_manifest = pin_preview_store(
        candidate_store,
        candidate_pool.rows,
        cohort_name="location-visual-candidates-available-v2",
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=candidate_pool.selection_sha256,
        fetch_preview=lambda asset_id: _safe_store_image(
            availability_store,
            str(available_store_rows[asset_id]["image_relpath"]),
        ).read_bytes(),
        audit_prefix="U",
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
        progress=progress,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed while candidate previews were being pinned")

    candidates, clustering, descriptor_sha256 = describe_candidate_universe(
        candidate_store,
        candidate_pool,
        config=config,
    )
    visual_selection = select_final_visual_cohort(
        candidates,
        eligible_inventory=snapshot.rows,
        blocked=blocked,
        config=config,
    )
    candidate_by_asset = {candidate.row.asset_id: candidate for candidate in candidates}

    final_store = output_dir / "final-previews-provisional"
    final_manifest = pin_preview_store(
        final_store,
        visual_selection.cohort.rows,
        cohort_name="fresh-location-certification-provisional-v2",
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=visual_selection.cohort.selection_sha256,
        fetch_preview=lambda asset_id: candidate_by_asset[asset_id].image_path.read_bytes(),
        audit_prefix="F",
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed before provisional cohort commit")

    review_manifest = render_private_review_bundle(
        output_dir=output_dir / "blind-review",
        candidate_store=candidate_store,
        final_store=final_store,
        candidates=candidates,
        clustering=clustering,
        selection=visual_selection,
        config=config,
    )
    final_private_rows = _load_store_rows(
        final_store,
        selection_sha256=visual_selection.cohort.selection_sha256,
    )
    private_selection_rows = [
        _private_selection_row(
            candidate,
            final_audit_id=str(final_private_rows[candidate.row.asset_id]["audit_id"]),
        )
        for candidate in sorted(
            visual_selection.selected,
            key=lambda item: item.row.asset_id,
        )
    ]
    private_plan: dict[str, object] = {
        "schema": "triage-private-provisional-visual-cohort-v1",
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256_at_selection": ledger_sha256,
        "candidate_availability_manifest_sha256": availability_manifest["manifest_sha256"],
        "candidate_preview_manifest_sha256": candidate_manifest["manifest_sha256"],
        "final_preview_manifest_sha256": final_manifest["manifest_sha256"],
        "descriptor_sha256": descriptor_sha256,
        "review_manifest_sha256": review_manifest["review_manifest_sha256"],
        "selected_rows": private_selection_rows,
    }
    private_plan["private_plan_sha256"] = _canonical_sha256(_PRIVATE_PLAN_DOMAIN, private_plan)
    private_plan_bytes = _canonical_bytes(private_plan)
    _write_create_only_idempotent(output_dir / "private-selection.json", private_plan_bytes)

    candidate_cluster_counts = Counter(int(label) for label in clustering.labels)
    public_manifest: dict[str, object] = {
        "schema": "triage-provisional-visual-cohort-public-v1",
        "status": "pending_private_visual_inspection_not_truth_reserved",
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256_at_selection": ledger_sha256,
        "config": config.public_dict(),
        "candidate_availability": {
            "attempted_count": availability_manifest["attempted_count"],
            "available_count": availability_manifest["available_count"],
            "unavailable_count": availability_manifest["unavailable_count"],
            "error_counts": availability_manifest["error_counts"],
        },
        "candidate_pool": candidate_pool.public_manifest(),
        "candidate_visual_universe": {
            "descriptor_version": DESCRIPTOR_VERSION,
            "descriptor_sha256": descriptor_sha256,
            "cluster_count": config.cluster_count,
            "represented_clusters": len(candidate_cluster_counts),
            "cluster_coverage": clustering.coverage,
            "normalized_cluster_entropy": clustering.normalized_entropy,
            "largest_cluster_share": clustering.largest_cluster_share,
            "cluster_counts": {
                str(label): candidate_cluster_counts[label]
                for label in sorted(candidate_cluster_counts)
            },
        },
        "final_selection": {
            **visual_selection.cohort.public_manifest(),
            "visual_audit": visual_selection.audit.public_dict(),
        },
        "private_evidence": {
            "candidate_preview_count": candidate_manifest["row_count"],
            "final_preview_count": final_manifest["row_count"],
            "review_sheet_counts": {
                name: len(pages) for name, pages in review_manifest["groups"].items()
            },
            "review_manifest_sha256": review_manifest["review_manifest_sha256"],
            "private_plan_sha256": private_plan["private_plan_sha256"],
        },
        "truth_reservation": {
            "performed": False,
            "required_next_step": "inspect private blind review, then reserve explicitly",
        },
    }
    public_manifest["run_sha256"] = _canonical_sha256(_VISUAL_RUN_DOMAIN, public_manifest)
    _write_create_only_idempotent(
        output_dir / "visual-cohort-public.json", _canonical_bytes(public_manifest)
    )
    _fsync_directory(output_dir)
    _assert_memory_limit(config.memory_limit_bytes)
    return VisualCohortRun(
        candidate_pool=candidate_pool,
        visual_selection=visual_selection,
        candidate_clustering=clustering,
        public_manifest=public_manifest,
    )
