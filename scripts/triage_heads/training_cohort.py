"""Build a durable, label-blind owner-image training cohort.

The training cohort is downstream of immutable certification evidence.  This
module therefore refuses to acquire pixels until the required fresh truth
cohort exists, and it treats every truth reservation as an asset/component/
moment blocklist throughout the run.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import resource
import sys
import tempfile
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .cohorts import (
    CohortAudit,
    CohortSelection,
    CohortSpec,
    InventoryAsset,
    InventorySnapshot,
    ReservationBlocklist,
    TruthReservationLedger,
    audit_cohort,
    cohort_selection_sha256,
)
from .image_diversity import (
    DESCRIPTOR_VERSION,
    PixelDescriptor,
    VisualClustering,
    cluster_descriptors,
    confirmed_near_duplicate,
    describe_preview_bytes,
    descriptor_cosine,
)
from .preview_store import (
    MAX_PREVIEW_BYTES,
    pin_available_preview_store,
    pin_preview_store,
    verify_committed_preview_store,
)
from .semantic_refinement import (
    PIXEL_MORPHOLOGY_VERSION,
    PixelMorphologyMetrics,
    describe_pixel_morphology,
    opaque_audit_id_set_sha256,
    prevalence_count_cap,
)

REQUIRED_FRESH_TRUTH_COHORT = "fresh-location-cert-v3"
PRODUCTION_INVENTORY_SHA256 = "c5620f8824f8e3dbad10c379f04b3b53a11ff2c9a782333158c005ce6a5deb93"
PRODUCTION_INVENTORY_COUNT = 84_607
DEFAULT_TARGET_SIZE = 20_000
DEFAULT_ACQUISITION_TARGET = 25_000
DEFAULT_MINIMUM_AVAILABLE = 22_000
MAX_PROCESS_MEMORY_BYTES = 8 * 1024**3
SYSTEM_MEMORY_CEILING_BYTES = 64 * 1024**3
_RUN_DOMAIN = b"triage-owner-training-cohort-v1\0"
_DESCRIPTOR_DOMAIN = b"triage-owner-training-descriptors-v1\0"
_REVIEW_DOMAIN = b"triage-owner-training-review-v1\0"
_MORPHOLOGY_DOMAIN = b"triage-owner-training-pixel-morphology-v1\0"
_CAPACITY_AUDIT_DOMAIN = b"triage-owner-training-capacity-audit-v1\0"
_CAPACITY_ALGORITHM = "deterministic-capacity-constrained-stratum-priority-v2"
_RAW_TV_FAILURE = "sqrt_quota_total_variation_above_gate"
_RAW_TV_WAIVER_REASON = "relaxed_capacity_lower_bound_proves_raw_gate_infeasible"
_CAPACITY_REPAIR_POOL_LIMIT = 128
_CAPACITY_REPAIR_PAIR_BUDGET = 65_536
_REUSE_STORE_NAMES = frozenset(
    {
        "candidate-preview-acquisition-v2",
        "candidate-previews-v2",
    }
)


@dataclass(frozen=True, slots=True)
class TrainingCohortConfig:
    """Frozen controls for one reproducible training-cohort run."""

    seed: str = "location-recovery-1b-training-v1"
    target_size: int = DEFAULT_TARGET_SIZE
    acquisition_target: int = DEFAULT_ACQUISITION_TARGET
    minimum_available: int = DEFAULT_MINIMUM_AVAILABLE
    min_distinct_days: int = 1_000
    max_per_day: int = 12
    max_per_moment: int = 3
    max_sqrt_quota_tv: float = 0.10
    max_sqrt_quota_excess_over_feasible: float = 0.01
    cluster_count: int = 128
    max_near_duplicate_checks: int = 256
    workers: int = 4
    memory_limit_bytes: int = MAX_PROCESS_MEMORY_BYTES
    review_sample_size: int = 64
    review_columns: int = 5
    review_rows: int = 4
    required_inventory_sha256: str = PRODUCTION_INVENTORY_SHA256
    required_inventory_count: int = PRODUCTION_INVENTORY_COUNT

    def __post_init__(self) -> None:
        if not self.seed.strip():
            raise ValueError("training cohort seed must not be empty")
        if self.target_size < 2:
            raise ValueError("training target must be at least two")
        minimum_acquisition = (self.target_size * 11 + 9) // 10
        if self.acquisition_target < minimum_acquisition:
            raise ValueError("metadata acquisition must be at least 110% of the final target")
        if not minimum_acquisition <= self.minimum_available <= self.acquisition_target:
            raise ValueError("minimum available previews must be at least 110% of target")
        if not 1 <= self.min_distinct_days <= self.target_size:
            raise ValueError("minimum distinct days must fit inside the final target")
        if not 1 <= self.max_per_day <= 12:
            raise ValueError("per-day cap must be between one and twelve")
        if not 1 <= self.max_per_moment <= 3:
            raise ValueError("per-moment cap must be between one and three")
        if not 0.0 <= self.max_sqrt_quota_tv <= 0.10:
            raise ValueError("sqrt year-quarter TV gate must be at most 0.10")
        if not 0.0 <= self.max_sqrt_quota_excess_over_feasible <= 0.01:
            raise ValueError("sqrt quota excess-over-feasible gate must be at most 0.01")
        if not 2 <= self.cluster_count <= self.minimum_available:
            raise ValueError("morphology cluster count must fit the available pool")
        if self.max_near_duplicate_checks < 1:
            raise ValueError("near-duplicate check limit must be positive")
        if not 1 <= self.workers <= 8:
            raise ValueError("preview workers must be between one and eight")
        if not 1 <= self.memory_limit_bytes <= MAX_PROCESS_MEMORY_BYTES:
            raise ValueError("training process memory limit must be at most 8 GiB")
        if self.review_sample_size < 2:
            raise ValueError("review sample must contain at least two images")
        if self.review_columns < 1 or self.review_rows < 1:
            raise ValueError("review sheet dimensions must be positive")
        if (
            len(self.required_inventory_sha256) != 64
            or any(
                character not in "0123456789abcdef" for character in self.required_inventory_sha256
            )
            or self.required_inventory_count < 1
        ):
            raise ValueError("required inventory binding is invalid")
        descriptor_bytes = self.acquisition_target * 112 * np.dtype(np.float32).itemsize
        cluster_distance_bytes = (
            self.acquisition_target * self.cluster_count * np.dtype(np.float32).itemsize
        )
        fetch_bytes = self.workers * 2 * MAX_PREVIEW_BYTES
        if 3 * descriptor_bytes + cluster_distance_bytes + fetch_bytes >= self.memory_limit_bytes:
            raise ValueError("configured acquisition cannot fit the process memory limit")

    def public_dict(self) -> dict[str, object]:
        return {
            "seed_sha256": hashlib.sha256(self.seed.encode()).hexdigest(),
            "target_size": self.target_size,
            "acquisition_target": self.acquisition_target,
            "minimum_available": self.minimum_available,
            "min_distinct_days": self.min_distinct_days,
            "max_per_day": self.max_per_day,
            "max_per_moment": self.max_per_moment,
            "max_sqrt_quota_tv": self.max_sqrt_quota_tv,
            "max_sqrt_quota_excess_over_feasible": (self.max_sqrt_quota_excess_over_feasible),
            "cluster_count": self.cluster_count,
            "screen_prevalence_multiplier": 1.0,
            "exclude_conservative_quality_failures": True,
            "pixel_morphology_version": PIXEL_MORPHOLOGY_VERSION,
            "max_near_duplicate_checks": self.max_near_duplicate_checks,
            "workers": self.workers,
            "process_memory_limit_bytes": self.memory_limit_bytes,
            "system_memory_ceiling_bytes": SYSTEM_MEMORY_CEILING_BYTES,
            "review_sample_size": self.review_sample_size,
            "required_inventory_sha256": self.required_inventory_sha256,
            "required_inventory_count": self.required_inventory_count,
        }


@dataclass(frozen=True, slots=True)
class TrainingCandidate:
    """One acquired preview plus its task-model-free pixel evidence."""

    row: InventoryAsset
    audit_id: str
    image_path: Path
    descriptor: PixelDescriptor
    raw_cluster_label: int
    raw_cluster_distance: float
    pixel_morphology: PixelMorphologyMetrics | None = None


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    retained: tuple[TrainingCandidate, ...]
    rejected_count: int
    comparison_count: int
    bucket_probe_count: int
    bucket_overflow_count: int


@dataclass(frozen=True, slots=True)
class TrainingQuotaFeasibilityAudit:
    """Training-only quota evidence when the raw sqrt target is infeasible."""

    target_size: int
    candidate_count: int
    eligible_inventory_count: int
    raw_sqrt_quota_total_variation: float
    relaxed_lower_bound_tv: float
    computed_feasible_reference_tv: float
    feasible_reference_gap_over_lower_bound: float
    signed_delta_from_feasible_reference: float
    excess_over_feasible: float
    max_excess_over_feasible: float
    raw_sqrt_quota_tv_gate: float
    feasible_reference_meets_raw_gate: bool
    raw_gate_failure_waived: bool
    raw_gate_failure_waiver_reason: str | None
    candidate_year_quarter_counts: tuple[tuple[str, int], ...]
    projected_capacity_upper_bounds: tuple[tuple[str, int], ...]
    projected_target_counts: tuple[tuple[str, int], ...]
    feasible_reference_counts: tuple[tuple[str, int], ...]
    selected_counts: tuple[tuple[str, int], ...]
    feasible_reference_selection_sha256: str
    screen_signature_count_cap: int | None
    max_per_day: int
    max_per_moment: int
    component_uniqueness_required: bool
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "triage-training-capacity-aware-quota-audit-v1",
            "algorithm": _CAPACITY_ALGORITHM,
            "target_size": self.target_size,
            "candidate_count": self.candidate_count,
            "eligible_inventory_count": self.eligible_inventory_count,
            "raw_sqrt_quota_total_variation": self.raw_sqrt_quota_total_variation,
            "raw_sqrt_quota_tv_gate": self.raw_sqrt_quota_tv_gate,
            "feasible_reference_meets_raw_gate": self.feasible_reference_meets_raw_gate,
            "raw_gate_failure_waived": self.raw_gate_failure_waived,
            "raw_gate_failure_waiver_reason": self.raw_gate_failure_waiver_reason,
            "relaxed_lower_bound_tv": self.relaxed_lower_bound_tv,
            "computed_feasible_reference_tv": self.computed_feasible_reference_tv,
            "feasible_reference_gap_over_lower_bound": (
                self.feasible_reference_gap_over_lower_bound
            ),
            "signed_delta_from_feasible_reference": (self.signed_delta_from_feasible_reference),
            "excess_over_feasible": self.excess_over_feasible,
            "max_excess_over_feasible": self.max_excess_over_feasible,
            "candidate_year_quarter_counts": dict(self.candidate_year_quarter_counts),
            "projected_capacity_upper_bounds": dict(self.projected_capacity_upper_bounds),
            "projected_target_counts": dict(self.projected_target_counts),
            "feasible_reference_counts": dict(self.feasible_reference_counts),
            "selected_counts": dict(self.selected_counts),
            "feasible_reference_selection_sha256": (self.feasible_reference_selection_sha256),
            "hard_constraints": {
                "max_per_day": self.max_per_day,
                "max_per_moment": self.max_per_moment,
                "component_uniqueness_required": self.component_uniqueness_required,
                "screen_signature_count_cap": self.screen_signature_count_cap,
            },
            "passed": self.passed,
            "failures": list(self.failures),
        }
        payload["capacity_evidence_sha256"] = _canonical_sha256(
            _CAPACITY_AUDIT_DOMAIN,
            payload,
        )
        return payload


@dataclass(frozen=True, slots=True)
class TrainingSelectionAudit:
    temporal: CohortAudit
    quota_feasibility: TrainingQuotaFeasibilityAudit
    source_available_count: int
    source_screen_signature_count: int
    screen_signature_count_cap: int
    selected_screen_signature_count: int
    source_conservative_quality_failure_count: int
    excluded_conservative_quality_failure_count: int
    selected_conservative_quality_failure_count: int
    pixel_morphology_sha256: str
    source_screen_signature_id_set_sha256: str
    source_quality_failure_id_set_sha256: str
    selected_screen_signature_id_set_sha256: str
    selected_quality_failure_id_set_sha256: str
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
            "quota_feasibility": self.quota_feasibility.public_dict(),
            "pixel_morphology_version": PIXEL_MORPHOLOGY_VERSION,
            "pixel_morphology_sha256": self.pixel_morphology_sha256,
            "source_available_count": self.source_available_count,
            "screen_signature": {
                "source_count": self.source_screen_signature_count,
                "selected_count": self.selected_screen_signature_count,
                "count_cap": self.screen_signature_count_cap,
                "prevalence_multiplier": 1.0,
            },
            "conservative_quality": {
                "source_failure_count": self.source_conservative_quality_failure_count,
                "excluded_failure_count": self.excluded_conservative_quality_failure_count,
                "selected_failure_count": self.selected_conservative_quality_failure_count,
            },
            "opaque_morphology_set_digests": {
                "source_screen_signature_sha256": (self.source_screen_signature_id_set_sha256),
                "source_quality_failure_sha256": self.source_quality_failure_id_set_sha256,
                "selected_screen_signature_sha256": (self.selected_screen_signature_id_set_sha256),
                "selected_quality_failure_sha256": (self.selected_quality_failure_id_set_sha256),
            },
            "truth_overlap": {
                "assets": self.truth_asset_overlap,
                "components": self.truth_component_overlap,
                "moments": self.truth_moment_overlap,
            },
            "passed": self.passed,
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class TrainingSelection:
    cohort: CohortSelection
    selected: tuple[TrainingCandidate, ...]
    audit: TrainingSelectionAudit


@dataclass(frozen=True, slots=True)
class TrainingAcquisitionSelection:
    cohort: CohortSelection
    quota_feasibility: TrainingQuotaFeasibilityAudit

    @property
    def name(self) -> str:
        return self.cohort.name

    @property
    def rows(self) -> tuple[InventoryAsset, ...]:
        return self.cohort.rows

    @property
    def selection_sha256(self) -> str:
        return self.cohort.selection_sha256

    @property
    def audit(self) -> CohortAudit:
        return self.cohort.audit


@dataclass(frozen=True, slots=True)
class TrainingCohortRun:
    acquisition: TrainingAcquisitionSelection
    selection: TrainingSelection
    public_manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FreshTruthBinding:
    ledger_sha256: str
    selection_sha256: str
    approval_authenticated: bool = False
    approval_public_sha256: str | None = None
    approval_private_sha256: str | None = None


class TrainingCohortQualityError(RuntimeError):
    def __init__(self, audit: TrainingSelectionAudit) -> None:
        super().__init__("training cohort quality gates failed: " + ", ".join(audit.failures))
        self.audit = audit


def _stable_rank(seed: str, scope: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{value}".encode()).hexdigest()


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


def _plain_canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _verify_plain_self_digest(
    payload: Mapping[str, object],
    field: str,
    *,
    what: str,
) -> str:
    claimed = str(payload.get(field, ""))
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if _plain_canonical_sha256(unsigned) != claimed:
        raise RuntimeError(f"{what} self-digest does not reproduce")
    return claimed


def _require_private_evidence_file(path: Path, *, what: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{what} must be a regular file")
    if path.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError(f"{what} must be mode 0600")


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


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _assert_memory_limit(limit_bytes: int) -> None:
    observed = _peak_rss_bytes()
    if observed >= limit_bytes:
        raise MemoryError(
            f"training-cohort process reached {observed / 1024**3:.2f} GiB; "
            f"limit is {limit_bytes / 1024**3:.2f} GiB"
        )


def _hash_partitions(value: str) -> tuple[int, ...] | None:
    if len(value) != 16:
        return None
    try:
        integer = int(value, 16)
    except ValueError:
        return None
    widths = (13, 13, 13, 13, 12)
    parts: list[int] = []
    shift = 64
    for width in widths:
        shift -= width
        parts.append((integer >> shift) & ((1 << width) - 1))
    return tuple(parts)


def _descriptor_bucket_keys(descriptor: PixelDescriptor) -> tuple[tuple[int, ...], ...]:
    average = _hash_partitions(descriptor.average_hash)
    difference = _hash_partitions(descriptor.difference_hash)
    if average is None or difference is None or descriptor.aspect_ratio <= 0:
        return ()
    aspect_bin = math.floor(math.log(descriptor.aspect_ratio) / 0.08)
    return tuple(
        (average_index, average_value, difference_index, difference_value, aspect_bin)
        for average_index, average_value in enumerate(average)
        for difference_index, difference_value in enumerate(difference)
    )


def reject_near_duplicates(
    candidates: Sequence[TrainingCandidate],
    *,
    seed: str,
    max_checks_per_candidate: int = 256,
) -> DeduplicationResult:
    """Reject raw-pixel near duplicates using bounded perceptual-hash buckets.

    Five-way bit partitions preserve a useful guarantee: hashes within four
    bits share at least one partition.  Pairing an average-hash partition with
    a difference-hash partition keeps buckets narrow, while the explicit check
    ceiling makes the run linear in cohort size.  A ceiling hit is recorded and
    is a fail-closed quality condition for the full builder.
    """
    if max_checks_per_candidate < 1:
        raise ValueError("near-duplicate check limit must be positive")
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            _stable_rank(seed, "raw-pixel-dedup", str(candidate.row.asset_id)),
            str(candidate.row.asset_id),
        ),
    )
    retained: list[TrainingCandidate] = []
    exact_previews: dict[str, int] = {}
    buckets: dict[tuple[int, ...], list[int]] = defaultdict(list)
    rejected = 0
    comparisons = 0
    bucket_probes = 0
    overflows = 0
    for candidate in ordered:
        descriptor = candidate.descriptor
        if descriptor.preview_sha256 in exact_previews:
            rejected += 1
            continue
        keys = _descriptor_bucket_keys(descriptor)
        candidate_indices: list[int] = []
        seen: set[int] = set()
        query_overflow = False
        aspect_bin = (
            math.floor(math.log(descriptor.aspect_ratio) / 0.08)
            if descriptor.aspect_ratio > 0
            else 0
        )
        for key in keys:
            for nearby_aspect in (aspect_bin - 1, aspect_bin, aspect_bin + 1):
                lookup = (*key[:-1], nearby_aspect)
                for retained_index in buckets.get(lookup, ()):
                    bucket_probes += 1
                    if retained_index in seen:
                        continue
                    seen.add(retained_index)
                    candidate_indices.append(retained_index)
                    if len(candidate_indices) > max_checks_per_candidate:
                        query_overflow = True
                        break
                if query_overflow:
                    break
            if query_overflow:
                break
        candidate_indices.sort()
        if query_overflow:
            overflows += 1
            candidate_indices = candidate_indices[:max_checks_per_candidate]
        duplicate = False
        for retained_index in candidate_indices:
            comparisons += 1
            if confirmed_near_duplicate(descriptor, retained[retained_index].descriptor):
                duplicate = True
                break
        if duplicate:
            rejected += 1
            continue
        retained_index = len(retained)
        retained.append(candidate)
        exact_previews[descriptor.preview_sha256] = retained_index
        insertion_overflow = False
        for key in keys:
            if len(buckets[key]) >= max_checks_per_candidate:
                insertion_overflow = True
            else:
                buckets[key].append(retained_index)
        if insertion_overflow and not query_overflow:
            overflows += 1
    return DeduplicationResult(
        tuple(retained),
        rejected,
        comparisons,
        bucket_probes,
        overflows,
    )


def _pixel_morphology_record(candidate: TrainingCandidate) -> dict[str, object]:
    metrics = candidate.pixel_morphology
    if metrics is None:
        raise ValueError("training candidate is missing frozen pixel morphology evidence")
    return {
        "audit_id": candidate.audit_id,
        "preview_sha256": candidate.descriptor.preview_sha256,
        "near_white_f64": np.float64(metrics.near_white_fraction).tobytes().hex(),
        "near_black_f64": np.float64(metrics.near_black_fraction).tobytes().hex(),
        "edge_f64": np.float64(metrics.edge_fraction).tobytes().hex(),
        "laplacian_variance_f64": np.float64(metrics.laplacian_variance).tobytes().hex(),
        "mean_saturation_f64": np.float64(metrics.mean_saturation).tobytes().hex(),
        "screen_signature": metrics.screen_signature,
        "conservative_quality_failure": metrics.conservative_quality_failure,
    }


def _pixel_morphology_sha256(candidates: Sequence[TrainingCandidate]) -> str:
    ordered = sorted(candidates, key=lambda candidate: candidate.audit_id)
    audit_ids = [candidate.audit_id for candidate in ordered]
    if len(audit_ids) != len(set(audit_ids)) or any(not value for value in audit_ids):
        raise ValueError("acquisition candidates require unique nonempty audit ids")
    return _canonical_sha256(
        _MORPHOLOGY_DOMAIN,
        {
            "schema": "triage-pixel-morphology-evidence-v1",
            "version": PIXEL_MORPHOLOGY_VERSION,
            "records": [_pixel_morphology_record(candidate) for candidate in ordered],
        },
    )


@dataclass(frozen=True, slots=True)
class _CapacitySelectionPlan:
    rows: tuple[InventoryAsset, ...]
    projected_capacity_upper_bounds: tuple[tuple[str, int], ...]
    projected_target_counts: tuple[tuple[str, int], ...]
    relaxed_lower_bound_tv: float


def _sqrt_quota_tv(
    selected_counts: Mapping[str, int],
    *,
    selected_total: int,
    eligible_counts: Mapping[str, int],
) -> float:
    weight_total = sum(math.sqrt(count) for count in eligible_counts.values())
    if not weight_total or not selected_total:
        return 1.0
    return 0.5 * sum(
        abs(selected_counts.get(stratum, 0) / selected_total - math.sqrt(count) / weight_total)
        for stratum, count in eligible_counts.items()
    )


def _projected_capacity_upper_bounds(
    rows: Sequence[InventoryAsset],
    *,
    max_per_day: int,
    max_per_moment: int,
) -> dict[str, int]:
    """Return a relaxation that cannot understate feasible stratum capacity.

    Component, moment, and day bounds are applied within each stratum. Shared
    groups across strata and the global screen cap are deliberately relaxed,
    so the resulting quota TV is a conservative lower bound, never an
    optimistic claim that a lower TV is actually attainable.
    """
    by_stratum: dict[str, list[InventoryAsset]] = defaultdict(list)
    for row in rows:
        by_stratum[row.stratum].append(row)
    capacities: dict[str, int] = {}
    for stratum, members in by_stratum.items():
        by_day_and_moment: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in members:
            by_day_and_moment[row.capture_day][row.moment_key].add(row.component_key)
        temporal_upper_bound = sum(
            min(
                max_per_day,
                sum(
                    min(max_per_moment, len(component_keys)) for component_keys in moments.values()
                ),
            )
            for moments in by_day_and_moment.values()
        )
        capacities[stratum] = min(
            len(members),
            len({row.component_key for row in members}),
            temporal_upper_bound,
        )
    return capacities


def _project_capacity_constrained_targets(
    *,
    target_size: int,
    eligible_counts: Mapping[str, int],
    capacity_upper_bounds: Mapping[str, int],
) -> dict[str, int]:
    """Solve the integer L1 quota projection under relaxed stratum caps."""
    projected = dict.fromkeys(eligible_counts, 0)
    weight_total = sum(math.sqrt(count) for count in eligible_counts.values())
    if not weight_total:
        return projected
    for _ in range(target_size):
        available: list[tuple[float, str]] = []
        for stratum, eligible_count in eligible_counts.items():
            selected_count = projected[stratum]
            if selected_count >= capacity_upper_bounds.get(stratum, 0):
                continue
            desired_share = math.sqrt(eligible_count) / weight_total
            marginal_cost = abs((selected_count + 1) / target_size - desired_share) - abs(
                selected_count / target_size - desired_share
            )
            available.append((marginal_cost, stratum))
        if not available:
            break
        _cost, chosen_stratum = min(available)
        projected[chosen_stratum] += 1
    return projected


def _day_round_robin_rows(
    rows: Sequence[InventoryAsset],
    *,
    seed: str,
    scope: str,
) -> tuple[InventoryAsset, ...]:
    by_day: dict[str, deque[InventoryAsset]] = {}
    grouped: dict[str, list[InventoryAsset]] = defaultdict(list)
    for row in rows:
        grouped[row.capture_day].append(row)
    for day, members in grouped.items():
        by_day[day] = deque(
            sorted(
                members,
                key=lambda row: (
                    _stable_rank(seed, scope, row.asset_id),
                    row.asset_id,
                ),
            )
        )
    days = sorted(
        by_day,
        key=lambda day: (_stable_rank(seed, scope, f"day:{day}"), day),
    )
    ordered: list[InventoryAsset] = []
    while True:
        added = False
        for day in days:
            if by_day[day]:
                ordered.append(by_day[day].popleft())
                added = True
        if not added:
            return tuple(ordered)


def _repair_one_for_two_capacity_dead_end(
    rows: Sequence[InventoryAsset],
    selected: Sequence[InventoryAsset],
    *,
    target_size: int,
    projected_targets: Mapping[str, int],
    max_per_day: int,
    max_per_moment: int,
    seed: str,
    screen_asset_ids: frozenset[str],
    screen_count_cap: int | None,
) -> tuple[InventoryAsset, ...]:
    """Repair one avoidable greedy dead end with a bounded one-for-two augment.

    The normal greedy path is intentionally untouched so an already successful
    acquisition remains byte-for-byte reproducible.  When it stops exactly one
    row short, this pass indexes which selected row would release each rejected
    candidate, then searches a fixed-size deterministic pair pool.  Work stays
    linear in the candidate count plus a constant pair-check budget.
    """
    if len(selected) + 1 != target_size:
        return tuple(selected)

    selected_by_id = {row.asset_id: row for row in selected}
    selected_by_component = {row.component_key: row.asset_id for row in selected}
    selected_by_moment: dict[str, list[str]] = defaultdict(list)
    selected_by_day: dict[str, list[str]] = defaultdict(list)
    for row in selected:
        selected_by_moment[row.moment_key].append(row.asset_id)
        selected_by_day[row.capture_day].append(row.asset_id)
    selected_screen_ids = {row.asset_id for row in selected if row.asset_id in screen_asset_ids}
    screen_count = len(selected_screen_ids)

    freed_by: dict[str, list[InventoryAsset]] = defaultdict(list)
    screen_only: list[InventoryAsset] = []

    def intersect(
        current: set[str] | None,
        required: set[str],
    ) -> set[str]:
        return set(required) if current is None else current & required

    for row in rows:
        if row.asset_id in selected_by_id:
            continue
        removals: set[str] | None = None
        component_blocker = selected_by_component.get(row.component_key)
        if component_blocker is not None:
            removals = {component_blocker}
        moment_members = selected_by_moment.get(row.moment_key, ())
        if len(moment_members) >= max_per_moment:
            removals = intersect(removals, set(moment_members))
        day_members = selected_by_day.get(row.capture_day, ())
        if len(day_members) >= max_per_day:
            removals = intersect(removals, set(day_members))
        screen_blocked = bool(
            row.asset_id in screen_asset_ids
            and screen_count_cap is not None
            and screen_count >= screen_count_cap
        )
        if screen_blocked:
            if removals is None:
                screen_only.append(row)
                continue
            removals &= selected_screen_ids
        if removals is None:
            # The monotone greedy pass cannot leave a currently valid row behind.
            continue
        for removed_id in removals:
            freed_by[removed_id].append(row)

    selected_counts = Counter(row.stratum for row in selected)

    def candidate_rank(row: InventoryAsset) -> tuple[object, ...]:
        projected_target = projected_targets.get(row.stratum, 0)
        selected_count = selected_counts[row.stratum]
        return (
            selected_count >= projected_target,
            abs((selected_count + 1) - projected_target),
            row.asset_id in screen_asset_ids,
            _stable_rank(seed, "capacity-repair-candidate", row.asset_id),
            row.asset_id,
        )

    screen_only_pool = sorted(screen_only, key=candidate_rank)[:_CAPACITY_REPAIR_POOL_LIMIT]
    best: tuple[tuple[object, ...], tuple[InventoryAsset, ...]] | None = None
    pair_checks = 0
    removal_ids = sorted(
        freed_by,
        key=lambda asset_id: (
            _stable_rank(seed, "capacity-repair-removal", asset_id),
            asset_id,
        ),
    )
    for removed_id in removal_ids:
        removed = selected_by_id[removed_id]
        pool_by_id = {row.asset_id: row for row in freed_by[removed_id]}
        if removed_id in selected_screen_ids:
            pool_by_id.update({row.asset_id: row for row in screen_only_pool})
        pool = sorted(pool_by_id.values(), key=candidate_rank)[:_CAPACITY_REPAIR_POOL_LIMIT]
        if len(pool) < 2:
            continue

        base_days = Counter(row.capture_day for row in selected if row.asset_id != removed_id)
        base_moments = Counter(row.moment_key for row in selected if row.asset_id != removed_id)
        base_components = {row.component_key for row in selected if row.asset_id != removed_id}
        base_screens = screen_count - int(removed_id in selected_screen_ids)
        base_strata = selected_counts.copy()
        base_strata[removed.stratum] -= 1

        for left_index, left in enumerate(pool):
            if (
                base_days[left.capture_day] >= max_per_day
                or base_moments[left.moment_key] >= max_per_moment
                or left.component_key in base_components
                or (
                    left.asset_id in screen_asset_ids
                    and screen_count_cap is not None
                    and base_screens >= screen_count_cap
                )
            ):
                continue
            for right in pool[left_index + 1 :]:
                pair_checks += 1
                if pair_checks > _CAPACITY_REPAIR_PAIR_BUDGET:
                    break
                if (
                    right.component_key in base_components
                    or right.component_key == left.component_key
                    or base_moments[right.moment_key] + int(right.moment_key == left.moment_key)
                    >= max_per_moment
                    or base_days[right.capture_day] + int(right.capture_day == left.capture_day)
                    >= max_per_day
                ):
                    continue
                added_screens = int(left.asset_id in screen_asset_ids) + int(
                    right.asset_id in screen_asset_ids
                )
                if screen_count_cap is not None and base_screens + added_screens > screen_count_cap:
                    continue
                result_counts = base_strata.copy()
                result_counts[left.stratum] += 1
                result_counts[right.stratum] += 1
                quota_distance = sum(
                    abs(result_counts[stratum] - projected_targets.get(stratum, 0))
                    for stratum in set(projected_targets) | set(result_counts)
                )
                pair_ids = tuple(sorted((left.asset_id, right.asset_id)))
                score: tuple[object, ...] = (
                    quota_distance,
                    base_screens + added_screens,
                    _stable_rank(seed, "capacity-repair-pair", "\0".join(pair_ids)),
                    removed_id,
                    pair_ids,
                )
                repaired = tuple(row for row in selected if row.asset_id != removed_id) + (
                    left,
                    right,
                )
                if best is None or score < best[0]:
                    best = (score, repaired)
            if pair_checks > _CAPACITY_REPAIR_PAIR_BUDGET:
                break
        if pair_checks > _CAPACITY_REPAIR_PAIR_BUDGET:
            break
    return tuple(selected) if best is None else best[1]


def _select_rows_for_capacity_plan(
    rows: Sequence[InventoryAsset],
    *,
    target_size: int,
    projected_targets: Mapping[str, int],
    max_per_day: int,
    max_per_moment: int,
    seed: str,
    screen_asset_ids: frozenset[str],
    screen_count_cap: int | None,
) -> tuple[InventoryAsset, ...]:
    grouped: dict[str, list[InventoryAsset]] = defaultdict(list)
    for row in rows:
        grouped[row.stratum].append(row)
    queues: dict[str, tuple[deque[InventoryAsset], deque[InventoryAsset]]] = {}
    for stratum, members in grouped.items():
        non_screens = [row for row in members if row.asset_id not in screen_asset_ids]
        screens = [row for row in members if row.asset_id in screen_asset_ids]
        queues[stratum] = (
            deque(
                _day_round_robin_rows(
                    non_screens,
                    seed=seed,
                    scope=f"capacity:{stratum}:non-screen",
                )
            ),
            deque(
                _day_round_robin_rows(
                    screens,
                    seed=seed,
                    scope=f"capacity:{stratum}:screen",
                )
            ),
        )

    selected: list[InventoryAsset] = []
    selected_by_stratum: Counter[str] = Counter()
    selected_days: Counter[str] = Counter()
    selected_moments: Counter[str] = Counter()
    selected_components: set[str] = set()
    selected_screen_count = 0

    def invalid(row: InventoryAsset) -> bool:
        return (
            selected_days[row.capture_day] >= max_per_day
            or selected_moments[row.moment_key] >= max_per_moment
            or row.component_key in selected_components
        )

    while len(selected) < target_size:
        options: list[tuple[tuple[object, ...], str, bool, InventoryAsset]] = []
        for stratum, (non_screen_queue, screen_queue) in queues.items():
            while non_screen_queue and invalid(non_screen_queue[0]):
                non_screen_queue.popleft()
            while screen_queue and invalid(screen_queue[0]):
                screen_queue.popleft()
            use_screen = False
            if non_screen_queue:
                candidate = non_screen_queue[0]
            elif screen_queue and (
                screen_count_cap is None or selected_screen_count < screen_count_cap
            ):
                candidate = screen_queue[0]
                use_screen = True
            else:
                continue
            projected_target = projected_targets.get(stratum, 0)
            selected_count = selected_by_stratum[stratum]
            if selected_count < projected_target:
                quota_priority: tuple[object, ...] = (
                    0,
                    (selected_count + 0.5) / max(projected_target, 1),
                )
            else:
                quota_priority = (1, selected_count - projected_target)
            priority = (
                *quota_priority,
                use_screen,
                _stable_rank(seed, "capacity-stratum", stratum),
                stratum,
            )
            options.append((priority, stratum, use_screen, candidate))
        if not options:
            break
        _priority, chosen_stratum, use_screen, chosen = min(options)
        chosen_queue = queues[chosen_stratum][1 if use_screen else 0]
        if chosen_queue.popleft() is not chosen:
            raise AssertionError("capacity queue changed during deterministic selection")
        selected.append(chosen)
        selected_by_stratum[chosen.stratum] += 1
        selected_days[chosen.capture_day] += 1
        selected_moments[chosen.moment_key] += 1
        selected_components.add(chosen.component_key)
        if use_screen:
            selected_screen_count += 1
    return _repair_one_for_two_capacity_dead_end(
        rows,
        selected,
        target_size=target_size,
        projected_targets=projected_targets,
        max_per_day=max_per_day,
        max_per_moment=max_per_moment,
        seed=seed,
        screen_asset_ids=screen_asset_ids,
        screen_count_cap=screen_count_cap,
    )


def _capacity_selection_plan(
    candidate_universe: Sequence[InventoryAsset],
    *,
    eligible_inventory: Sequence[InventoryAsset],
    target_size: int,
    max_per_day: int,
    max_per_moment: int,
    seed: str,
    screen_asset_ids: frozenset[str] = frozenset(),
    screen_count_cap: int | None = None,
) -> _CapacitySelectionPlan:
    candidates_by_id = {row.asset_id: row for row in candidate_universe}
    if len(candidates_by_id) != len(candidate_universe):
        raise ValueError("capacity candidate universe contains duplicate asset ids")
    eligible_by_id = {row.asset_id: row for row in eligible_inventory}
    if len(eligible_by_id) != len(eligible_inventory):
        raise ValueError("eligible inventory contains duplicate asset ids")
    if not set(candidates_by_id).issubset(eligible_by_id):
        raise ValueError("capacity candidate universe lies outside eligible inventory")
    ordered_candidates = tuple(candidates_by_id[asset_id] for asset_id in sorted(candidates_by_id))
    eligible_counts = Counter(row.stratum for row in eligible_inventory)
    capacity_upper_bounds = _projected_capacity_upper_bounds(
        ordered_candidates,
        max_per_day=max_per_day,
        max_per_moment=max_per_moment,
    )
    projected_targets = _project_capacity_constrained_targets(
        target_size=target_size,
        eligible_counts=eligible_counts,
        capacity_upper_bounds=capacity_upper_bounds,
    )
    relaxed_lower_bound_tv = _sqrt_quota_tv(
        projected_targets,
        selected_total=target_size,
        eligible_counts=eligible_counts,
    )
    selected = _select_rows_for_capacity_plan(
        ordered_candidates,
        target_size=target_size,
        projected_targets=projected_targets,
        max_per_day=max_per_day,
        max_per_moment=max_per_moment,
        seed=seed,
        screen_asset_ids=screen_asset_ids,
        screen_count_cap=screen_count_cap,
    )
    return _CapacitySelectionPlan(
        rows=selected,
        projected_capacity_upper_bounds=tuple(sorted(capacity_upper_bounds.items())),
        projected_target_counts=tuple(sorted(projected_targets.items())),
        relaxed_lower_bound_tv=relaxed_lower_bound_tv,
    )


def audit_training_quota_feasibility(
    selected: Sequence[InventoryAsset],
    *,
    candidate_universe: Sequence[InventoryAsset],
    eligible_inventory: Sequence[InventoryAsset],
    config: TrainingCohortConfig,
    target_size: int | None = None,
    min_distinct_days: int | None = None,
    screen_asset_ids: frozenset[str] = frozenset(),
    screen_count_cap: int | None = None,
    reference_plan: _CapacitySelectionPlan | None = None,
) -> TrainingQuotaFeasibilityAudit:
    """Audit raw quota skew against a hard-constraint-aware feasible witness."""
    target_size = target_size or config.target_size
    min_distinct_days = min_distinct_days or config.min_distinct_days
    spec = CohortSpec(
        target_size=target_size,
        minimum_size=target_size,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        min_distinct_days=min_distinct_days,
        min_year_quarter_coverage=0.90,
        max_sqrt_quota_tv=config.max_sqrt_quota_tv,
    )
    plan = reference_plan or _capacity_selection_plan(
        candidate_universe,
        eligible_inventory=eligible_inventory,
        target_size=target_size,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        seed=config.seed,
        screen_asset_ids=screen_asset_ids,
        screen_count_cap=screen_count_cap,
    )
    temporal = audit_cohort(selected, eligible_inventory, spec)
    reference_temporal = audit_cohort(plan.rows, eligible_inventory, spec)
    signed_delta = (
        temporal.sqrt_quota_total_variation - reference_temporal.sqrt_quota_total_variation
    )
    clipped_excess = max(0.0, signed_delta)
    reference_gap = max(
        0.0,
        reference_temporal.sqrt_quota_total_variation - plan.relaxed_lower_bound_tv,
    )
    raw_gate_failure = _RAW_TV_FAILURE in temporal.failures
    raw_gate_failure_waived = bool(
        raw_gate_failure and plan.relaxed_lower_bound_tv > config.max_sqrt_quota_tv + 1e-12
    )
    actual_hard_failures = [
        failure
        for failure in temporal.failures
        if failure != _RAW_TV_FAILURE or not raw_gate_failure_waived
    ]
    failures = list(actual_hard_failures)
    reference_hard_failures = [
        failure for failure in reference_temporal.failures if failure != _RAW_TV_FAILURE
    ]
    failures.extend(f"feasible_reference_{failure}" for failure in reference_hard_failures)
    selected_screen_count = sum(row.asset_id in screen_asset_ids for row in selected)
    reference_screen_count = sum(row.asset_id in screen_asset_ids for row in plan.rows)
    if screen_count_cap is not None and selected_screen_count > screen_count_cap:
        failures.append("screen_signature_cap_exceeded")
    if screen_count_cap is not None and reference_screen_count > screen_count_cap:
        failures.append("feasible_reference_screen_signature_cap_exceeded")
    if plan.relaxed_lower_bound_tv > reference_temporal.sqrt_quota_total_variation + 1e-12:
        failures.append("capacity_lower_bound_overstates_feasibility")
    if (
        not actual_hard_failures
        and plan.relaxed_lower_bound_tv > temporal.sqrt_quota_total_variation + 1e-12
    ):
        failures.append("capacity_lower_bound_overstates_observed_feasibility")
    if reference_gap > config.max_sqrt_quota_excess_over_feasible + 1e-12:
        failures.append("feasible_reference_gap_above_gate")
    if clipped_excess > config.max_sqrt_quota_excess_over_feasible + 1e-12:
        failures.append("sqrt_quota_excess_over_feasible")
    audit = TrainingQuotaFeasibilityAudit(
        target_size=target_size,
        candidate_count=len(candidate_universe),
        eligible_inventory_count=len(eligible_inventory),
        raw_sqrt_quota_total_variation=temporal.sqrt_quota_total_variation,
        relaxed_lower_bound_tv=plan.relaxed_lower_bound_tv,
        computed_feasible_reference_tv=(reference_temporal.sqrt_quota_total_variation),
        feasible_reference_gap_over_lower_bound=reference_gap,
        signed_delta_from_feasible_reference=signed_delta,
        excess_over_feasible=clipped_excess,
        max_excess_over_feasible=config.max_sqrt_quota_excess_over_feasible,
        raw_sqrt_quota_tv_gate=config.max_sqrt_quota_tv,
        feasible_reference_meets_raw_gate=(
            reference_temporal.sqrt_quota_total_variation <= config.max_sqrt_quota_tv + 1e-12
        ),
        raw_gate_failure_waived=raw_gate_failure_waived,
        raw_gate_failure_waiver_reason=(_RAW_TV_WAIVER_REASON if raw_gate_failure_waived else None),
        candidate_year_quarter_counts=tuple(
            sorted(Counter(row.stratum for row in candidate_universe).items())
        ),
        projected_capacity_upper_bounds=plan.projected_capacity_upper_bounds,
        projected_target_counts=plan.projected_target_counts,
        feasible_reference_counts=reference_temporal.year_quarter_counts,
        selected_counts=temporal.year_quarter_counts,
        feasible_reference_selection_sha256=cohort_selection_sha256(plan.rows),
        screen_signature_count_cap=screen_count_cap,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        component_uniqueness_required=True,
        failures=tuple(dict.fromkeys(failures)),
    )
    # Computing the public digest here catches non-finite evidence before a run
    # can reach any output manifest.
    audit.public_dict()
    return audit


def select_final_training_cohort(
    candidates: Sequence[TrainingCandidate],
    *,
    acquisition_candidates: Sequence[TrainingCandidate],
    eligible_inventory: Sequence[InventoryAsset],
    blocked: ReservationBlocklist | None,
    config: TrainingCohortConfig,
    require_quality: bool = True,
) -> TrainingSelection:
    """Select exactly the target under the frozen label-blind pixel policy."""
    blocked = blocked or ReservationBlocklist()
    acquisition_by_id = {
        str(candidate.row.asset_id): candidate for candidate in acquisition_candidates
    }
    if len(acquisition_by_id) != len(acquisition_candidates):
        raise ValueError("acquisition candidates contain duplicate asset ids")
    pixel_morphology_sha256 = _pixel_morphology_sha256(acquisition_candidates)
    retained_by_id = {str(candidate.row.asset_id): candidate for candidate in candidates}
    if len(retained_by_id) != len(candidates):
        raise ValueError("deduplicated candidates contain duplicate asset ids")
    if not set(retained_by_id).issubset(acquisition_by_id):
        raise ValueError("deduplicated candidates lie outside the acquisition")
    for asset_id, candidate in retained_by_id.items():
        source = acquisition_by_id[asset_id]
        if (
            candidate.audit_id != source.audit_id
            or candidate.descriptor.preview_sha256 != source.descriptor.preview_sha256
            or candidate.pixel_morphology != source.pixel_morphology
        ):
            raise ValueError("deduplicated candidate differs from its acquisition evidence")

    source_screen_ids = {
        candidate.audit_id
        for candidate in acquisition_candidates
        if candidate.pixel_morphology is not None and candidate.pixel_morphology.screen_signature
    }
    source_quality_ids = {
        candidate.audit_id
        for candidate in acquisition_candidates
        if candidate.pixel_morphology is not None
        and candidate.pixel_morphology.conservative_quality_failure
    }
    screen_cap = prevalence_count_cap(
        source_count=len(source_screen_ids),
        candidate_count=len(acquisition_candidates),
        final_target=config.target_size,
        multiplier=1.0,
    )
    quality_passed = tuple(
        candidate
        for candidate in candidates
        if candidate.pixel_morphology is not None
        and not candidate.pixel_morphology.conservative_quality_failure
    )
    screen_asset_ids = frozenset(
        candidate.row.asset_id
        for candidate in quality_passed
        if candidate.pixel_morphology.screen_signature
    )
    eligible = tuple(
        row
        for row in eligible_inventory
        if row.asset_id not in blocked.asset_ids
        and row.component_key not in blocked.component_keys
        and row.moment_key not in blocked.moment_keys
    )
    capacity_plan = _capacity_selection_plan(
        tuple(candidate.row for candidate in quality_passed),
        eligible_inventory=eligible,
        target_size=config.target_size,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        seed=f"{config.seed}:final",
        screen_asset_ids=screen_asset_ids,
        screen_count_cap=screen_cap,
    )
    quality_by_id = {candidate.row.asset_id: candidate for candidate in quality_passed}
    selected = [quality_by_id[row.asset_id] for row in capacity_plan.rows]
    selected.sort(
        key=lambda candidate: (
            _stable_rank(config.seed, "final-order", str(candidate.row.asset_id)),
            str(candidate.row.asset_id),
        )
    )
    selected_tuple = tuple(selected)
    selected_rows = tuple(candidate.row for candidate in selected_tuple)
    truth_overlap = (
        sum(row.asset_id in blocked.asset_ids for row in selected_rows),
        sum(row.component_key in blocked.component_keys for row in selected_rows),
        sum(row.moment_key in blocked.moment_keys for row in selected_rows),
    )
    temporal = audit_cohort(
        selected_rows,
        eligible,
        CohortSpec(
            target_size=config.target_size,
            minimum_size=config.target_size,
            max_per_day=config.max_per_day,
            max_per_moment=config.max_per_moment,
            min_distinct_days=config.min_distinct_days,
            min_year_quarter_coverage=0.90,
            max_sqrt_quota_tv=config.max_sqrt_quota_tv,
        ),
    )
    quota_feasibility = audit_training_quota_feasibility(
        selected_rows,
        candidate_universe=tuple(candidate.row for candidate in quality_passed),
        eligible_inventory=eligible,
        config=config,
        screen_asset_ids=screen_asset_ids,
        screen_count_cap=screen_cap,
        reference_plan=capacity_plan,
    )
    selected_screen_ids = {
        candidate.audit_id
        for candidate in selected_tuple
        if candidate.pixel_morphology is not None and candidate.pixel_morphology.screen_signature
    }
    selected_quality_ids = {
        candidate.audit_id
        for candidate in selected_tuple
        if candidate.pixel_morphology is not None
        and candidate.pixel_morphology.conservative_quality_failure
    }
    failures = list(quota_feasibility.failures)
    if len(selected_rows) != config.target_size:
        failures.append("target_not_reached")
    if len(selected_screen_ids) > screen_cap:
        failures.append("screen_signature_cap_exceeded")
    if selected_quality_ids:
        failures.append("conservative_quality_failure_selected")
    if any(truth_overlap):
        failures.append("immutable_truth_overlap")
    audit = TrainingSelectionAudit(
        temporal=temporal,
        quota_feasibility=quota_feasibility,
        source_available_count=len(acquisition_candidates),
        source_screen_signature_count=len(source_screen_ids),
        screen_signature_count_cap=screen_cap,
        selected_screen_signature_count=len(selected_screen_ids),
        source_conservative_quality_failure_count=len(source_quality_ids),
        excluded_conservative_quality_failure_count=len(source_quality_ids),
        selected_conservative_quality_failure_count=len(selected_quality_ids),
        pixel_morphology_sha256=pixel_morphology_sha256,
        source_screen_signature_id_set_sha256=opaque_audit_id_set_sha256(source_screen_ids),
        source_quality_failure_id_set_sha256=opaque_audit_id_set_sha256(source_quality_ids),
        selected_screen_signature_id_set_sha256=opaque_audit_id_set_sha256(selected_screen_ids),
        selected_quality_failure_id_set_sha256=opaque_audit_id_set_sha256(selected_quality_ids),
        truth_asset_overlap=truth_overlap[0],
        truth_component_overlap=truth_overlap[1],
        truth_moment_overlap=truth_overlap[2],
        failures=tuple(dict.fromkeys(failures)),
    )
    if require_quality and not audit.passed:
        raise TrainingCohortQualityError(audit)
    cohort = CohortSelection(
        name="location-training-v1",
        rows=selected_rows,
        selection_sha256=cohort_selection_sha256(selected_rows),
        audit=temporal,
    )
    return TrainingSelection(cohort, selected_tuple, audit)


def _authenticate_fresh_truth_approval(
    approval_dir: Path,
    *,
    snapshot: InventorySnapshot,
    reservation_selection_sha256: str,
) -> tuple[str, str]:
    approval_root = Path(approval_dir)
    if not approval_root.is_dir() or approval_root.is_symlink():
        raise RuntimeError("fresh truth approval must be a private directory")
    if approval_root.stat().st_mode & 0o777 != 0o700:
        raise RuntimeError("fresh truth approval directory must be mode 0700")
    allowed = {
        "certification-index.jsonl",
        "approval-private.json",
        "approval-public.json",
    }
    if {path.name for path in approval_root.iterdir()} != allowed:
        raise RuntimeError("fresh truth approval directory is incomplete or has extra files")
    index_path = approval_root / "certification-index.jsonl"
    private_path = approval_root / "approval-private.json"
    public_path = approval_root / "approval-public.json"
    for path, what in (
        (index_path, "fresh truth certification index"),
        (private_path, "fresh truth private approval"),
        (public_path, "fresh truth public approval"),
    ):
        _require_private_evidence_file(path, what=what)

    private = json.loads(private_path.read_text(encoding="utf-8"))
    public = json.loads(public_path.read_text(encoding="utf-8"))
    if not isinstance(private, Mapping) or not isinstance(public, Mapping):
        raise RuntimeError("fresh truth approval manifests are malformed")
    private_keys = {
        "schema",
        "status",
        "cohort_name",
        "inventory_sha256",
        "selection_sha256",
        "truth_ledger_sha256_at_selection",
        "truth_ledger_sha256_after_reservation",
        "semantic_output_dir",
        "semantic_run_sha256",
        "source_visual_lineage",
        "semantic_private_lineage",
        "final_preview_store",
        "review_decision_sha256",
        "certification_index_file",
        "certification_index_sha256",
        "selected_count",
        "approval_private_sha256",
    }
    public_keys = {
        "schema",
        "status",
        "cohort_name",
        "selected_count",
        "distinct_days",
        "distinct_components",
        "distinct_moments",
        "inventory_sha256",
        "selection_sha256",
        "truth_ledger",
        "review",
        "source_evidence",
        "final_preview_evidence",
        "quality_gates",
        "private_evidence",
        "approval_public_sha256",
    }
    if set(private) != private_keys or set(public) != public_keys:
        raise RuntimeError("fresh truth approval manifest violates the exact v3 schema")
    private_sha256 = _verify_plain_self_digest(
        private,
        "approval_private_sha256",
        what="fresh truth private approval",
    )
    public_sha256 = _verify_plain_self_digest(
        public,
        "approval_public_sha256",
        what="fresh truth public approval",
    )
    expected_header = (
        "truth-reserved",
        REQUIRED_FRESH_TRUTH_COHORT,
        snapshot.inventory_sha256,
        reservation_selection_sha256,
        400,
    )
    if (
        private.get("status"),
        private.get("cohort_name"),
        private.get("inventory_sha256"),
        private.get("selection_sha256"),
        private.get("selected_count"),
    ) != expected_header or (
        public.get("status"),
        public.get("cohort_name"),
        public.get("inventory_sha256"),
        public.get("selection_sha256"),
        public.get("selected_count"),
    ) != expected_header:
        raise RuntimeError("fresh truth approval differs from the reserved selection")
    if (
        private.get("schema") != "triage-fresh-certification-approval-private-v3"
        or public.get("schema") != "triage-fresh-certification-approval-public-v3"
    ):
        raise RuntimeError("fresh truth approval uses an unsupported schema")
    if (
        public.get("distinct_days"),
        public.get("distinct_components"),
        public.get("distinct_moments"),
    ) != (400, 400, 400):
        raise RuntimeError("fresh truth approval lacks 400-way diversity")
    private_evidence = public.get("private_evidence")
    truth_ledger = public.get("truth_ledger")
    private_final = private.get("final_preview_store")
    public_final = public.get("final_preview_evidence")
    if not all(
        isinstance(value, Mapping)
        for value in (private_evidence, truth_ledger, private_final, public_final)
    ):
        raise RuntimeError("fresh truth approval lineage is malformed")
    if private_evidence.get("approval_private_sha256") != private_sha256:
        raise RuntimeError("fresh truth public approval does not bind its private approval")
    if truth_ledger.get("sha256_at_selection") != private.get(
        "truth_ledger_sha256_at_selection"
    ) or truth_ledger.get("sha256_after_reservation") != private.get(
        "truth_ledger_sha256_after_reservation"
    ):
        raise RuntimeError("fresh truth approval disagrees about ledger lineage")
    for field in (
        "manifest_sha256",
        "private_index_sha256",
        "selection_lock_sha256",
        "image_set_sha256",
    ):
        if private_final.get(field) != public_final.get(field):
            raise RuntimeError("fresh truth approval disagrees about final preview evidence")

    index_bytes = index_path.read_bytes()
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    if (
        private.get("certification_index_file") != "certification-index.jsonl"
        or private.get("certification_index_sha256") != index_sha256
        or private_evidence.get("certification_index_sha256") != index_sha256
    ):
        raise RuntimeError("fresh truth certification index digest does not reproduce")
    index_keys = {
        "asset_id",
        "source_audit_id",
        "final_audit_id",
        "source_updated",
        "captured_at",
        "capture_day",
        "component_key",
        "moment_key",
        "stratum",
        "image_relpath",
        "preview_sha256",
        "preview_width",
        "preview_height",
    }
    raw_rows = [json.loads(line) for line in index_bytes.splitlines()]
    if len(raw_rows) != 400 or any(
        not isinstance(row, Mapping) or set(row) != index_keys for row in raw_rows
    ):
        raise RuntimeError("fresh truth certification index violates its exact schema")
    inventory_by_id = {row.asset_id: row for row in snapshot.rows}
    index_inventory: list[InventoryAsset] = []
    seen_assets: set[str] = set()
    seen_source_audits: set[str] = set()
    seen_final_audits: set[str] = set()
    seen_paths: set[str] = set()
    image_digest_rows: list[dict[str, object]] = []
    for raw in raw_rows:
        asset_id = str(raw["asset_id"])
        inventory = inventory_by_id.get(asset_id)
        if inventory is None:
            raise RuntimeError("fresh truth certification index lies outside the inventory")
        if (
            raw["source_updated"],
            raw["captured_at"],
            raw["capture_day"],
            raw["component_key"],
            raw["moment_key"],
            raw["stratum"],
        ) != (
            inventory.updated_at,
            inventory.captured_at,
            inventory.capture_day,
            inventory.component_key,
            inventory.moment_key,
            inventory.stratum,
        ):
            raise RuntimeError("fresh truth certification metadata differs from the inventory")
        source_audit = str(raw["source_audit_id"])
        final_audit = str(raw["final_audit_id"])
        image_relpath = str(raw["image_relpath"])
        if (
            not source_audit
            or not final_audit
            or not image_relpath
            or Path(image_relpath).is_absolute()
            or ".." in Path(image_relpath).parts
            or asset_id in seen_assets
            or source_audit in seen_source_audits
            or final_audit in seen_final_audits
            or image_relpath in seen_paths
        ):
            raise RuntimeError("fresh truth certification identities are invalid or duplicated")
        preview_sha256 = str(raw["preview_sha256"])
        if (
            len(preview_sha256) != 64
            or any(character not in "0123456789abcdef" for character in preview_sha256)
            or int(raw["preview_width"]) < 1
            or int(raw["preview_height"]) < 1
        ):
            raise RuntimeError("fresh truth certification preview evidence is invalid")
        seen_assets.add(asset_id)
        seen_source_audits.add(source_audit)
        seen_final_audits.add(final_audit)
        seen_paths.add(image_relpath)
        index_inventory.append(inventory)
        image_digest_rows.append(
            {
                "final_audit_id": final_audit,
                "image_relpath": image_relpath,
                "preview_sha256": preview_sha256,
            }
        )
    if cohort_selection_sha256(index_inventory) != reservation_selection_sha256:
        raise RuntimeError("fresh truth approval selection digest does not reproduce")
    image_set_sha256 = _plain_canonical_sha256(
        {"schema": "triage-fresh-cert-image-set-v3", "rows": image_digest_rows}
    )
    if image_set_sha256 != private_final.get("image_set_sha256"):
        raise RuntimeError("fresh truth approval image-set digest does not reproduce")
    return public_sha256, private_sha256


def _require_fresh_truth(
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    *,
    approval_dir: Path | None = None,
) -> FreshTruthBinding:
    reservation = ledger.cohort_reservation(REQUIRED_FRESH_TRUTH_COHORT)
    if reservation is None:
        raise RuntimeError(f"required truth cohort {REQUIRED_FRESH_TRUTH_COHORT!r} is not reserved")
    if reservation.inventory_sha256 != snapshot.inventory_sha256:
        raise RuntimeError("fresh truth cohort belongs to a different inventory snapshot")
    if (
        reservation.selected_count != 400
        or len(reservation.rows) != 400
        or any(row.reservation_kind != "mapped" for row in reservation.rows)
    ):
        raise RuntimeError("fresh truth cohort must contain exactly 400 mapped reservations")
    inventory_by_id = {row.asset_id: row for row in snapshot.rows}
    mapped: list[InventoryAsset] = []
    for reserved in reservation.rows:
        inventory = inventory_by_id.get(reserved.asset_id)
        if inventory is None:
            raise RuntimeError("fresh truth reservation contains an asset outside the inventory")
        if (
            reserved.component_key,
            reserved.moment_key,
            reserved.capture_day,
            reserved.stratum,
            reserved.source_universe_sha256,
            reserved.absent_asset_type,
            reserved.reason,
        ) != (
            inventory.component_key,
            inventory.moment_key,
            inventory.capture_day,
            inventory.stratum,
            snapshot.inventory_sha256,
            None,
            None,
        ):
            raise RuntimeError("fresh truth reservation metadata differs from the inventory")
        mapped.append(inventory)
    if (
        len({row.asset_id for row in mapped}) != 400
        or len({row.capture_day for row in mapped}) != 400
        or len({row.component_key for row in mapped}) != 400
        or len({row.moment_key for row in mapped}) != 400
    ):
        raise RuntimeError(
            "fresh truth cohort requires 400 distinct assets, days, components, and moments"
        )
    if cohort_selection_sha256(mapped) != reservation.selection_sha256:
        raise RuntimeError("fresh truth selection digest does not reproduce")
    public_approval_sha256: str | None = None
    private_approval_sha256: str | None = None
    if approval_dir is not None and Path(approval_dir).exists():
        public_approval_sha256, private_approval_sha256 = _authenticate_fresh_truth_approval(
            approval_dir,
            snapshot=snapshot,
            reservation_selection_sha256=reservation.selection_sha256,
        )
    return FreshTruthBinding(
        ledger_sha256=ledger.ledger_sha256(),
        selection_sha256=reservation.selection_sha256,
        approval_authenticated=public_approval_sha256 is not None,
        approval_public_sha256=public_approval_sha256,
        approval_private_sha256=private_approval_sha256,
    )


def _require_inventory_binding(
    snapshot: InventorySnapshot,
    config: TrainingCohortConfig,
) -> None:
    if (
        snapshot.inventory_sha256 != config.required_inventory_sha256
        or len(snapshot.rows) != config.required_inventory_count
    ):
        raise RuntimeError("training requires the exact frozen full inventory snapshot")


def require_fresh_truth_ready(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    config: TrainingCohortConfig = TrainingCohortConfig(),
    approval_dir: Path | None = None,
) -> str:
    """Expose the no-network training prerequisite for CLI preflight checks."""
    binding = _require_fresh_truth(snapshot, ledger, approval_dir=approval_dir)
    _require_inventory_binding(snapshot, config)
    return binding.ledger_sha256


def select_training_acquisition(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    config: TrainingCohortConfig,
) -> TrainingAcquisitionSelection:
    """Select the metadata-only surplus after blocking all immutable truth."""
    _require_inventory_binding(snapshot, config)
    ledger_sha256 = _require_fresh_truth(snapshot, ledger).ledger_sha256
    blocked = ledger.blocklist()
    eligible = tuple(
        row
        for row in snapshot.rows
        if row.asset_id not in blocked.asset_ids
        and row.component_key not in blocked.component_keys
        and row.moment_key not in blocked.moment_keys
    )
    capacity_plan = _capacity_selection_plan(
        eligible,
        eligible_inventory=eligible,
        target_size=config.acquisition_target,
        max_per_day=config.max_per_day,
        max_per_moment=config.max_per_moment,
        seed=f"{config.seed}:acquisition",
    )
    temporal = audit_cohort(
        capacity_plan.rows,
        eligible,
        CohortSpec(
            target_size=config.acquisition_target,
            minimum_size=config.acquisition_target,
            max_per_day=config.max_per_day,
            max_per_moment=config.max_per_moment,
            min_distinct_days=config.min_distinct_days,
            min_year_quarter_coverage=0.90,
            max_sqrt_quota_tv=config.max_sqrt_quota_tv,
        ),
    )
    quota_feasibility = audit_training_quota_feasibility(
        capacity_plan.rows,
        candidate_universe=eligible,
        eligible_inventory=eligible,
        config=config,
        target_size=config.acquisition_target,
        min_distinct_days=config.min_distinct_days,
        reference_plan=capacity_plan,
    )
    if not quota_feasibility.passed:
        raise RuntimeError(
            "training acquisition capacity-aware quota gates failed: "
            + ", ".join(quota_feasibility.failures)
        )
    cohort = CohortSelection(
        name="location-training-preview-acquisition-v1",
        rows=capacity_plan.rows,
        selection_sha256=cohort_selection_sha256(capacity_plan.rows),
        audit=temporal,
    )
    selection = TrainingAcquisitionSelection(cohort, quota_feasibility)
    overlaps = (
        sum(row.asset_id in blocked.asset_ids for row in selection.rows),
        sum(row.component_key in blocked.component_keys for row in selection.rows),
        sum(row.moment_key in blocked.moment_keys for row in selection.rows),
    )
    if any(overlaps):
        raise RuntimeError("metadata acquisition overlaps immutable truth")
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed during metadata acquisition")
    return selection


def authenticate_v2_reuse_stores(
    stores: Sequence[Path],
    *,
    snapshot: InventorySnapshot,
) -> tuple[Path, ...]:
    """Authenticate only immutable, non-truth v2 acquisition/candidate stores."""
    inventory_by_id = {row.asset_id: row for row in snapshot.rows}
    authenticated: list[Path] = []
    for raw_store in stores:
        store = Path(raw_store)
        if store.name not in _REUSE_STORE_NAMES:
            raise ValueError("reuse store is not a recognized v2 acquisition/candidate store")
        manifest, _private, lock = verify_committed_preview_store(store)
        cohort_name = str(lock.get("cohort_name", ""))
        if not cohort_name.endswith("-v2") or not any(
            token in cohort_name for token in ("candidate", "acquisition")
        ):
            raise ValueError("reuse store is not a non-truth v2 candidate acquisition")
        if any(token in cohort_name for token in ("truth", "certification", "final")):
            raise ValueError("truth/final preview stores cannot be reused for training")
        if lock.get("inventory_sha256") != snapshot.inventory_sha256:
            raise ValueError("reuse store belongs to a different inventory snapshot")
        rows = lock.get("rows")
        if not isinstance(rows, list):
            raise ValueError("reuse selection lock has no private row list")
        for item in rows:
            if not isinstance(item, Mapping):
                raise ValueError("reuse selection lock contains an invalid row")
            inventory_row = inventory_by_id.get(str(item.get("asset_id", "")))
            if inventory_row is None:
                raise ValueError("reuse selection contains an asset outside the inventory")
            expected = (
                inventory_row.updated_at,
                inventory_row.component_key,
                inventory_row.moment_key,
                inventory_row.capture_day,
            )
            actual = (
                str(item.get("source_updated", "")),
                str(item.get("component_key", "")),
                str(item.get("moment_key", "")),
                str(item.get("capture_day", "")),
            )
            if actual != expected:
                raise ValueError("reuse selection metadata differs from the sealed inventory")
        if manifest.get("inventory_sha256") != snapshot.inventory_sha256:
            raise ValueError("reuse manifest belongs to a different inventory snapshot")
        authenticated.append(store)
    return tuple(dict.fromkeys(authenticated))


def discover_v2_reuse_stores(roots: Sequence[Path]) -> tuple[Path, ...]:
    """Find only the two known v2 acquisition-store directory names."""
    discovered: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists():
            continue
        if root.is_dir() and root.name in _REUSE_STORE_NAMES:
            discovered.add(root)
        if root.is_dir():
            for name in _REUSE_STORE_NAMES:
                discovered.update(path.parent for path in root.rglob(f"{name}/selection-lock.json"))
    return tuple(sorted(discovered, key=lambda path: str(path.resolve())))


def _safe_store_image(store: Path, image_relpath: str) -> Path:
    root = store.resolve()
    path = (store / image_relpath).resolve()
    if not path.is_relative_to(root):
        raise ValueError("preview index contains a path outside its private store")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _load_preview_rows(
    store: Path,
    *,
    selection_sha256: str,
    available: bool,
) -> dict[str, dict[str, object]]:
    manifest, private, _lock = verify_committed_preview_store(store)
    if manifest.get("selection_sha256") != selection_sha256:
        raise ValueError("preview manifest belongs to a different selection")
    if private.get("selection_sha256") != selection_sha256:
        raise ValueError("private preview index belongs to a different selection")
    key = "available_rows" if available else "rows"
    raw_rows = private.get(key)
    if not isinstance(raw_rows, list):
        raise ValueError("private preview index has no expected row list")
    rows = {str(row["asset_id"]): dict(row) for row in raw_rows}
    if len(rows) != len(raw_rows):
        raise ValueError("private preview index contains duplicate asset ids")
    return rows


def _describe_acquisition(
    *,
    store: Path,
    acquisition: TrainingAcquisitionSelection,
    config: TrainingCohortConfig,
) -> tuple[tuple[TrainingCandidate, ...], VisualClustering, str, bytes]:
    private_rows = _load_preview_rows(
        store,
        selection_sha256=acquisition.selection_sha256,
        available=True,
    )
    acquisition_by_id = {row.asset_id: row for row in acquisition.rows}
    ordered_ids = sorted(private_rows)
    if not set(ordered_ids).issubset(acquisition_by_id):
        raise ValueError("available preview store lies outside metadata acquisition")
    descriptors: list[PixelDescriptor] = []
    pixel_metrics: list[PixelMorphologyMetrics] = []
    paths: list[Path] = []
    audit_ids: list[str] = []
    for index, asset_id in enumerate(ordered_ids, start=1):
        private_row = private_rows[asset_id]
        path = _safe_store_image(store, str(private_row["image_relpath"]))
        payload = path.read_bytes()
        descriptor = describe_preview_bytes(payload)
        if descriptor.preview_sha256 != private_row["preview_sha256"]:
            raise ValueError("available preview bytes differ from their pinned digest")
        descriptors.append(descriptor)
        pixel_metrics.append(describe_pixel_morphology(payload))
        paths.append(path)
        audit_ids.append(str(private_row["audit_id"]))
        if index % 128 == 0:
            _assert_memory_limit(config.memory_limit_bytes)
    if len(descriptors) < config.minimum_available:
        raise RuntimeError("available preview count fell below the configured success floor")
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
    private_records = [
        {
            **_pixel_morphology_record(candidate),
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
        "schema": "triage-private-owner-training-descriptors-v1",
        "descriptor_version": DESCRIPTOR_VERSION,
        "pixel_morphology_version": PIXEL_MORPHOLOGY_VERSION,
        "pixel_morphology_sha256": _pixel_morphology_sha256(candidates),
        "acquisition_selection_sha256": acquisition.selection_sha256,
        "rows": private_records,
    }
    descriptor_bytes = _canonical_bytes(descriptor_payload)
    descriptor_sha256 = _canonical_sha256(_DESCRIPTOR_DOMAIN, descriptor_payload)
    _assert_memory_limit(config.memory_limit_bytes)
    return candidates, clustering, descriptor_sha256, descriptor_bytes


def _render_pages(
    entries: Sequence[tuple[Path, str]],
    *,
    output_dir: Path,
    stem: str,
    columns: int,
    rows: int,
) -> tuple[dict[str, object], ...]:
    page_size = columns * rows
    tile_width, image_height, caption_height = 256, 192, 30
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
            draw.text((x + 5, y + image_height + 5), caption, fill="black")
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG", optimize=True)
        payload = buffer.getvalue()
        destination = output_dir / f"{stem}-{page_number:02d}.png"
        _write_create_only_idempotent(destination, payload)
        pages.append(
            {
                "page": page_number,
                "tile_count": len(page_entries),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return tuple(pages)


def _nearest_review_candidates(
    selected: Sequence[TrainingCandidate],
    *,
    pair_count: int,
) -> tuple[tuple[TrainingCandidate, TrainingCandidate, float], ...]:
    by_cluster: dict[int, list[TrainingCandidate]] = defaultdict(list)
    for candidate in selected:
        by_cluster[candidate.raw_cluster_label].append(candidate)
    pairs: list[tuple[float, str, TrainingCandidate, TrainingCandidate]] = []
    for label, members in by_cluster.items():
        probes = sorted(
            members,
            key=lambda candidate: (
                candidate.raw_cluster_distance,
                candidate.audit_id,
            ),
        )[:8]
        best: tuple[float, str, TrainingCandidate, TrainingCandidate] | None = None
        for left_index, left in enumerate(probes):
            for right in probes[left_index + 1 :]:
                similarity = descriptor_cosine(left.descriptor, right.descriptor)
                tie = f"{label:04d}:{left.audit_id}:{right.audit_id}"
                entry = (similarity, tie, left, right)
                if best is None or entry[:2] > best[:2]:
                    best = entry
        if best is not None:
            pairs.append(best)
    pairs.sort(key=lambda item: (-item[0], item[1]))
    return tuple((left, right, similarity) for similarity, _tie, left, right in pairs[:pair_count])


def _render_review_bundle(
    *,
    output_dir: Path,
    final_store: Path,
    selection: TrainingSelection,
    config: TrainingCohortConfig,
) -> dict[str, object]:
    _private_directory(output_dir)
    private_rows = _load_preview_rows(
        final_store,
        selection_sha256=selection.cohort.selection_sha256,
        available=False,
    )

    def entry(candidate: TrainingCandidate, caption: str) -> tuple[Path, str]:
        private = private_rows[str(candidate.row.asset_id)]
        return (
            _safe_store_image(final_store, str(private["image_relpath"])),
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
            _stable_rank(config.seed, "blind-random-review", str(candidate.row.asset_id)),
            candidate.audit_id,
        ),
    )[: config.review_sample_size]
    random_entries = [entry(candidate, "random") for candidate in random_candidates]
    nearest = _nearest_review_candidates(
        selection.selected,
        pair_count=max(1, config.review_sample_size // 2),
    )
    nearest_entries: list[tuple[Path, str]] = []
    for pair_index, (left, right, similarity) in enumerate(nearest, start=1):
        nearest_entries.append(entry(left, f"near {pair_index:02d}A {similarity:.3f}"))
        nearest_entries.append(entry(right, f"near {pair_index:02d}B {similarity:.3f}"))
    groups = {
        "medoids": _render_pages(
            medoid_entries,
            output_dir=output_dir,
            stem="medoids",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "random": _render_pages(
            random_entries,
            output_dir=output_dir,
            stem="random",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
        "nearest": _render_pages(
            nearest_entries,
            output_dir=output_dir,
            stem="nearest",
            columns=config.review_columns,
            rows=config.review_rows,
        ),
    }
    manifest: dict[str, object] = {
        "schema": "triage-private-owner-training-review-v1",
        "blindness": "opaque audit ids and raw pixels only; no labels or head output",
        "final_selection_sha256": selection.cohort.selection_sha256,
        "nearest_method": "closest pair among eight centroid-nearest members per raw cluster",
        "complexity": "O(n + clusters * 8^2)",
        "groups": groups,
    }
    manifest["review_manifest_sha256"] = _canonical_sha256(_REVIEW_DOMAIN, manifest)
    _write_create_only_idempotent(
        output_dir / "review-manifest.json",
        _canonical_bytes(manifest),
    )
    return manifest


def build_training_cohort(
    *,
    snapshot: InventorySnapshot,
    ledger: TruthReservationLedger,
    output_dir: Path,
    fetch_preview: Callable[[str], bytes],
    config: TrainingCohortConfig = TrainingCohortConfig(),
    reuse_stores: tuple[Path, ...] = (),
    approval_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> TrainingCohortRun:
    """Build and seal the exact training cohort after truth is immutable."""
    _assert_memory_limit(config.memory_limit_bytes)
    _require_inventory_binding(snapshot, config)
    truth_binding = _require_fresh_truth(snapshot, ledger, approval_dir=approval_dir)
    ledger_sha256 = truth_binding.ledger_sha256
    acquisition = select_training_acquisition(
        snapshot=snapshot,
        ledger=ledger,
        config=config,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed before preview acquisition")
    blocked = ledger.blocklist()
    overlaps = (
        sum(row.asset_id in blocked.asset_ids for row in acquisition.rows),
        sum(row.component_key in blocked.component_keys for row in acquisition.rows),
        sum(row.moment_key in blocked.moment_keys for row in acquisition.rows),
    )
    if any(overlaps):
        raise RuntimeError("metadata acquisition overlaps immutable truth")
    authenticated_reuse = authenticate_v2_reuse_stores(
        reuse_stores,
        snapshot=snapshot,
    )

    output_dir = Path(output_dir)
    _private_directory(output_dir)
    acquisition_store = output_dir / "training-preview-acquisition-v1"
    availability_manifest = pin_available_preview_store(
        acquisition_store,
        acquisition.rows,
        cohort_name="location-training-preview-acquisition-v1",
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=acquisition.selection_sha256,
        fetch_preview=fetch_preview,
        audit_prefix="A",
        minimum_successes=config.minimum_available,
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
        progress=progress,
        reuse_stores=authenticated_reuse,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed while previews were acquired")

    candidates, clustering, descriptor_sha256, descriptor_bytes = _describe_acquisition(
        store=acquisition_store,
        acquisition=acquisition,
        config=config,
    )
    _write_create_only_idempotent(output_dir / "descriptor-audit.json", descriptor_bytes)
    quality_passed = tuple(
        candidate
        for candidate in candidates
        if candidate.pixel_morphology is not None
        and not candidate.pixel_morphology.conservative_quality_failure
    )
    deduplicated = reject_near_duplicates(
        quality_passed,
        seed=config.seed,
        max_checks_per_candidate=config.max_near_duplicate_checks,
    )
    if deduplicated.bucket_overflow_count:
        raise RuntimeError("raw-pixel duplicate index overflowed its bounded comparison budget")
    if len(deduplicated.retained) < config.target_size:
        raise RuntimeError("raw-pixel duplicate removal left fewer than the final target")

    selection = select_final_training_cohort(
        deduplicated.retained,
        acquisition_candidates=candidates,
        eligible_inventory=snapshot.rows,
        blocked=blocked,
        config=config,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed before final cohort sealing")

    selected_by_id = {str(candidate.row.asset_id): candidate for candidate in selection.selected}

    def descriptor_bound_preview(asset_id: str) -> bytes:
        candidate = selected_by_id[asset_id]
        payload = candidate.image_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != candidate.descriptor.preview_sha256:
            raise RuntimeError("selected preview changed after descriptor audit")
        return payload

    final_store = output_dir / "training-previews-v1"
    final_manifest = pin_preview_store(
        final_store,
        selection.cohort.rows,
        cohort_name="location-training-v1",
        inventory_sha256=snapshot.inventory_sha256,
        selection_sha256=selection.cohort.selection_sha256,
        fetch_preview=descriptor_bound_preview,
        audit_prefix="T",
        workers=config.workers,
        memory_limit_bytes=config.memory_limit_bytes,
        progress=progress,
    )
    if ledger.ledger_sha256() != ledger_sha256:
        raise RuntimeError("truth ledger changed while the final cohort was pinned")

    final_rows = _load_preview_rows(
        final_store,
        selection_sha256=selection.cohort.selection_sha256,
        available=False,
    )
    preview_descriptor_bindings = []
    for candidate in sorted(selection.selected, key=lambda item: str(item.row.asset_id)):
        final_row = final_rows[str(candidate.row.asset_id)]
        final_preview_sha256 = str(final_row["preview_sha256"])
        if final_preview_sha256 != candidate.descriptor.preview_sha256:
            raise RuntimeError("final preview digest differs from its descriptor evidence")
        preview_descriptor_bindings.append(
            {
                "asset_id": candidate.row.asset_id,
                "acquisition_audit_id": candidate.audit_id,
                "final_audit_id": final_row["audit_id"],
                "preview_sha256": final_preview_sha256,
                "descriptor_vector_sha256": hashlib.sha256(
                    candidate.descriptor.vector.tobytes()
                ).hexdigest(),
            }
        )
    preview_descriptor_binding_sha256 = _canonical_sha256(
        _DESCRIPTOR_DOMAIN,
        {
            "schema": "triage-training-preview-descriptor-binding-v1",
            "descriptor_sha256": descriptor_sha256,
            "rows": preview_descriptor_bindings,
        },
    )
    private_selection = {
        "schema": "triage-private-owner-training-selection-v1",
        "inventory_sha256": snapshot.inventory_sha256,
        "truth_ledger_sha256": ledger_sha256,
        "acquisition_selection_sha256": acquisition.selection_sha256,
        "acquisition_quota_feasibility": acquisition.quota_feasibility.public_dict(),
        "descriptor_sha256": descriptor_sha256,
        "preview_descriptor_binding_sha256": preview_descriptor_binding_sha256,
        "final_selection_sha256": selection.cohort.selection_sha256,
        "final_quota_feasibility": selection.audit.quota_feasibility.public_dict(),
        "rows": [
            {
                "asset_id": candidate.row.asset_id,
                "component_key": candidate.row.component_key,
                "moment_key": candidate.row.moment_key,
                "capture_day": candidate.row.capture_day,
                "stratum": candidate.row.stratum,
                "acquisition_audit_id": candidate.audit_id,
                "final_audit_id": final_rows[str(candidate.row.asset_id)]["audit_id"],
                "raw_cluster_label": candidate.raw_cluster_label,
                "preview_sha256": candidate.descriptor.preview_sha256,
                "descriptor_vector_sha256": hashlib.sha256(
                    candidate.descriptor.vector.tobytes()
                ).hexdigest(),
            }
            for candidate in sorted(
                selection.selected,
                key=lambda candidate: str(candidate.row.asset_id),
            )
        ],
    }
    private_selection["private_selection_sha256"] = _canonical_sha256(
        _RUN_DOMAIN,
        private_selection,
    )
    _write_create_only_idempotent(
        output_dir / "private-selection.json",
        _canonical_bytes(private_selection),
    )
    review_manifest = _render_review_bundle(
        output_dir=output_dir / "blind-review",
        final_store=final_store,
        selection=selection,
        config=config,
    )

    cluster_counts = Counter(int(label) for label in clustering.labels)
    public_manifest: dict[str, object] = {
        "schema": "triage-owner-training-cohort-public-v1",
        "status": "sealed_exact_training_cohort",
        "privacy": "aggregate only; asset mapping is restricted to private indices",
        "inventory_sha256": snapshot.inventory_sha256,
        "inventory_asset_count": len(snapshot.rows),
        "truth_ledger_sha256": ledger_sha256,
        "required_truth_cohort": REQUIRED_FRESH_TRUTH_COHORT,
        "fresh_truth_selection_sha256": truth_binding.selection_sha256,
        "fresh_truth_approval": {
            "authenticated": truth_binding.approval_authenticated,
            "public_sha256": truth_binding.approval_public_sha256,
            "private_sha256": truth_binding.approval_private_sha256,
        },
        "semantic_inputs": "disabled",
        "config": config.public_dict(),
        "acquisition": {
            "selection_sha256": acquisition.selection_sha256,
            "audit": acquisition.audit.public_dict(),
            "quota_feasibility": acquisition.quota_feasibility.public_dict(),
            "attempted_count": availability_manifest["attempted_count"],
            "available_count": availability_manifest["available_count"],
            "unavailable_count": availability_manifest["unavailable_count"],
            "error_counts": availability_manifest["error_counts"],
            "authenticated_reuse_store_count": len(authenticated_reuse),
        },
        "raw_pixel_audit": {
            "descriptor_version": DESCRIPTOR_VERSION,
            "descriptor_sha256": descriptor_sha256,
            "cluster_count": config.cluster_count,
            "represented_clusters": len(cluster_counts),
            "normalized_cluster_entropy": clustering.normalized_entropy,
            "largest_cluster_share": clustering.largest_cluster_share,
            "cluster_counts": {
                str(label): cluster_counts[label] for label in sorted(cluster_counts)
            },
            "near_duplicate_rejections": deduplicated.rejected_count,
            "near_duplicate_comparisons": deduplicated.comparison_count,
            "near_duplicate_bucket_probes": deduplicated.bucket_probe_count,
            "bucket_overflow_count": deduplicated.bucket_overflow_count,
            "complexity": (
                f"O(n * {config.max_near_duplicate_checks}) bounded perceptual-hash index"
            ),
        },
        "final_selection": {
            "name": selection.cohort.name,
            "selection_sha256": selection.cohort.selection_sha256,
            "audit": selection.cohort.audit.public_dict(),
            "quota_feasibility": selection.audit.quota_feasibility.public_dict(),
            "morphology_audit": selection.audit.public_dict(),
        },
        "private_evidence": {
            "final_preview_count": final_manifest["row_count"],
            "final_preview_manifest_sha256": final_manifest["manifest_sha256"],
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
    public_manifest["run_sha256"] = _canonical_sha256(_RUN_DOMAIN, public_manifest)
    public_bytes = _canonical_bytes(public_manifest)

    def commit_public_manifest() -> None:
        _write_create_only_idempotent(
            output_dir / "training-cohort-public.json",
            public_bytes,
        )
        _fsync_directory(output_dir)

    ledger.commit_if_unchanged(
        expected_ledger_sha256=ledger_sha256,
        commit=commit_public_manifest,
    )
    _assert_memory_limit(config.memory_limit_bytes)
    return TrainingCohortRun(acquisition, selection, public_manifest)
