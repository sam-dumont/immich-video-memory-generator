#!/usr/bin/env python3
"""Fit PCA plus linear and shallow-MLP candidates for one active-v1 triage head."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import stat
import string
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

if __package__:
    from .cohorts import TruthReservationLedger
    from .data import CohortPreviewIndex, IndexedPreview, load_cohort_preview_index
    from .generate_labels import (
        LABEL_PROMPT,
        TEACHER_API_MODEL,
        TEACHER_MODEL,
        LabelAsset,
        TeacherEndpoint,
        _active_head_provenance,
        _multihead_inventory_sha256,
        _valid_active_labels,
        _wal_rows,
        validate_multihead_completion,
    )
    from .head_specs import ACTIVE_HEAD_NAMES, LOCATION, HeadSpec, resolve_head_specs
    from .memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        acquire_pipeline_lock,
        ensure_offline_process_memory,
        ensure_private_directory,
        validate_offline_working_set_gib,
    )
    from .preview_store import verify_committed_preview_store
else:  # pragma: no cover - direct script invocation
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.triage_heads.cohorts import TruthReservationLedger
    from scripts.triage_heads.data import (
        CohortPreviewIndex,
        IndexedPreview,
        load_cohort_preview_index,
    )
    from scripts.triage_heads.generate_labels import (
        LABEL_PROMPT,
        TEACHER_API_MODEL,
        TEACHER_MODEL,
        LabelAsset,
        TeacherEndpoint,
        _active_head_provenance,
        _multihead_inventory_sha256,
        _valid_active_labels,
        _wal_rows,
        validate_multihead_completion,
    )
    from scripts.triage_heads.head_specs import (
        ACTIVE_HEAD_NAMES,
        LOCATION,
        HeadSpec,
        resolve_head_specs,
    )
    from scripts.triage_heads.memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        acquire_pipeline_lock,
        ensure_offline_process_memory,
        ensure_private_directory,
        validate_offline_working_set_gib,
    )
    from scripts.triage_heads.preview_store import verify_committed_preview_store

DEFAULT_ARTIFACT_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads"
DEFAULT_TRUTH = (
    Path.home() / ".immich-memories-matrix" / "description-truth-2026-08-31" / "library_truth.jsonl"
)
# One door for the teacher identity: the labeler's constants. The 27B literal that
# lived here outlasted the teacher swap by a week and admitted retired labels.
PINNED_TEACHER_MODEL = TEACHER_MODEL
PINNED_TEACHER_API_MODEL = TEACHER_API_MODEL
PINNED_PROMPT_SHA256 = "dd997465bccf0cbd3521ae82af1ec4566b3e69db1ae834ab457ecf4997a0a69b"
PINNED_SCHEMA_SHA256 = "cc2d2803bbcb3a0cdad5439c02057038ea910dc0e954717c67e0ec1a65a7f1ee"
LABEL_PROVENANCE_FIELDS = (
    "teacher_model",
    "teacher_api_model",
    "temperature",
    "prompt_sha256",
    "schema_sha256",
)
TRAINING_COMPONENTS = (
    "training_manifest",
    "pca_artifact",
    "linear_candidate",
    "mlp_candidate",
)


def training_component_filenames(head: HeadSpec) -> dict[str, str]:
    """One bundle layout per head; the PCA and manifest names are shared."""
    return {
        "training_manifest": "training-manifest.jsonl",
        "pca_artifact": "pca-v1.npz",
        "linear_candidate": f"{head.name}-linear-candidate.npz",
        "mlp_candidate": f"{head.name}-mlp-candidate.npz",
    }


TRAINING_COMPONENT_FILENAMES = training_component_filenames(LOCATION)
TRAINING_RUNS_DIRECTORY = "training-runs"
TRAINING_CURRENT_POINTER = "training-current.json"
TRAINING_REPORT_FILENAME = "train-metrics.json"

SPLIT_FRACTIONS = {"train": 0.70, "cal": 0.10, "test": 0.20}
C_GRID = (0.01, 0.1, 1.0, 10.0)
ACTIVE_V1_OWNER_COUNT = 20_000
ACTIVE_V1_COHORT_NAME = "location-training-v2"
ACTIVE_V1_TRUTH_COHORT = "fresh-location-cert-v3"
ACTIVE_V1_TRUTH_COUNT = 400
_OWNER_COHORT_DOMAIN = b"triage-owner-training-cohort-v1\0"
_OWNER_DESCRIPTOR_DOMAIN = b"triage-owner-training-descriptors-v1\0"
_OWNER_CAPACITY_DOMAIN = b"triage-owner-training-capacity-audit-v1\0"
_OWNER_CAPACITY_ALGORITHM = "deterministic-capacity-constrained-stratum-priority-v2"
_OWNER_CAPACITY_WAIVER_REASON = "relaxed_capacity_lower_bound_proves_raw_gate_infeasible"
_OWNER_V2_COHORT_DOMAIN = b"triage-owner-training-cohort-v2\0"
_OWNER_V2_DESCRIPTOR_DOMAIN = b"triage-owner-training-descriptors-v2\0"
_OWNER_V2_BINDING_DOMAIN = b"triage-owner-training-preview-descriptor-binding-v2\0"
_OWNER_V2_NEAREST_DOMAIN = b"triage-owner-training-global-nearest-audit-v2\0"
_OWNER_V2_PUBLIC_KEYS = {
    "schema",
    "status",
    "privacy",
    "inventory_sha256",
    "inventory_asset_count",
    "truth_ledger_sha256",
    "required_truth_cohort",
    "fresh_truth_selection_sha256",
    "fresh_truth_approval",
    "semantic_inputs",
    "config",
    "source_v1",
    "acquisition",
    "raw_pixel_audit",
    "final_selection",
    "private_evidence",
    "memory_provenance",
    "run_sha256",
}
_OWNER_V2_PRIVATE_KEYS = {
    "schema",
    "inventory_sha256",
    "truth_ledger_sha256",
    "source_v1",
    "acquisition_selection_sha256",
    "descriptor_sha256",
    "preview_descriptor_binding_sha256",
    "final_selection_sha256",
    "final_quota_feasibility",
    "cosine_duplicate_filter",
    "global_nearest_similarity",
    "global_nearest_pair_audit_ids",
    "rows",
    "private_selection_sha256",
}
_OWNER_V2_DESCRIPTOR_KEYS = {
    "schema",
    "descriptor_version",
    "pixel_morphology_version",
    "pixel_morphology_sha256",
    "source_v1_descriptor_sha256",
    "acquisition_selection_sha256",
    "rows",
}
_OWNER_V2_CONFIG_KEYS = {
    "seed_sha256",
    "target_size",
    "acquisition_target",
    "minimum_available",
    "min_distinct_days",
    "max_per_day",
    "max_per_moment",
    "max_sqrt_quota_tv",
    "max_sqrt_quota_excess_over_feasible",
    "cluster_count",
    "screen_prevalence_multiplier",
    "exclude_conservative_quality_failures",
    "pixel_morphology_version",
    "max_near_duplicate_checks",
    "workers",
    "process_memory_limit_bytes",
    "system_memory_ceiling_bytes",
    "review_sample_size",
    "required_inventory_sha256",
    "required_inventory_count",
}
_OWNER_V2_ACQUISITION_KEYS = {
    "selection_sha256",
    "attempted_count",
    "available_count",
    "unavailable_count",
    "error_counts",
    "reuse_mode",
}
_OWNER_V2_RAW_PIXEL_KEYS = {
    "descriptor_version",
    "descriptor_sha256",
    "cluster_count",
    "represented_clusters",
    "normalized_cluster_entropy",
    "largest_cluster_share",
    "cluster_counts",
    "conservative_quality_failure_count",
    "quality_pass_count",
    "legacy_near_duplicate_rejections",
    "legacy_near_duplicate_comparisons",
    "legacy_near_duplicate_bucket_probes",
    "legacy_bucket_overflow_count",
    "cosine_duplicate_filter",
    "global_nearest_similarity",
    "complexity",
}
_OWNER_V2_FINAL_SELECTION_KEYS = {
    "name",
    "selection_sha256",
    "audit",
    "quota_feasibility",
    "morphology_audit",
    "global_nearest_similarity",
}
_OWNER_V2_PRIVATE_EVIDENCE_KEYS = {
    "final_preview_count",
    "final_preview_manifest_sha256",
    "descriptor_sha256",
    "private_selection_sha256",
    "preview_descriptor_binding_sha256",
    "review_manifest_sha256",
    "review_sheet_counts",
}
_OWNER_V2_MEMORY_KEYS = {
    "process_cap_bytes",
    "global_ceiling_bytes",
    "process_cap_gib",
    "global_ceiling_gib",
}
_OWNER_V2_PRIVATE_ROW_KEYS = {
    "asset_id",
    "component_key",
    "moment_key",
    "capture_day",
    "stratum",
    "acquisition_audit_id",
    "final_audit_id",
    "raw_cluster_label",
    "preview_sha256",
    "descriptor_vector_sha256",
}
_OWNER_V2_COSINE_KEYS = {
    "algorithm",
    "threshold",
    "comparison",
    "input_count",
    "rejected_count",
    "retained_count",
    "radius_query_count",
    "radius_neighbor_edge_count",
    "max_batch_neighbor_count",
}
_OWNER_V2_NEAREST_KEYS = {
    "schema",
    "algorithm",
    "selected_count",
    "threshold",
    "comparison",
    "max_nearest_similarity",
    "nearest_pair_sha256",
    "descriptor_set_sha256",
    "passed",
    "failures",
    "nearest_audit_sha256",
}
_OWNER_MAX_PROCESS_MEMORY_BYTES = 8 * 1024**3
_OWNER_SYSTEM_MEMORY_CEILING_BYTES = 64 * 1024**3


@dataclass(frozen=True)
class TrainingExample:
    asset_id: str
    label: str
    group_key: str
    preview_sha256: str = ""
    source_updated: str = ""


@dataclass(frozen=True)
class ActiveV1TrainingEvidence:
    """Fully authenticated owner labels plus their immutable input lineage."""

    examples: tuple[TrainingExample, ...]
    cohort_index: CohortPreviewIndex
    training_input: dict[str, Any]
    truth_ledger_sha256: str
    truth_ledger_path: Path
    source_files: tuple[tuple[Path, str], ...]


@dataclass(frozen=True)
class TrainingBundleStaging:
    root: Path
    component_paths: dict[str, Path]
    head: HeadSpec = LOCATION

    @property
    def report_path(self) -> Path:
        return self.root / TRAINING_REPORT_FILENAME


@dataclass(frozen=True)
class TrainingBundleSnapshot:
    report_path: Path
    component_paths: dict[str, Path]
    report: dict[str, Any]
    report_bytes: bytes
    component_bytes: dict[str, bytes]
    training_run_sha256: str
    pointer_sha256: str | None


@dataclass(frozen=True)
class _TrainingBundleReference:
    report_path: Path
    component_paths: dict[str, Path]
    pointer: dict[str, Any] | None


@dataclass(frozen=True)
class PCAArtifact:
    mean: np.ndarray
    components: np.ndarray
    explained_variance: np.ndarray

    def transform(self, packs: np.ndarray) -> np.ndarray:
        array = np.asarray(packs, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.mean.shape[0]:
            raise ValueError(f"PCA expected [n, {self.mean.shape[0]}] packs, got {array.shape}")
        return ((array - self.mean) @ self.components.T).astype(np.float32, copy=False)


def project_deployment_features(pca: PCAArtifact, packs: np.ndarray) -> np.ndarray:
    """Match the cache/deployment boundary: PCA FP32 -> stored FP16 -> compute FP32."""
    return pca.transform(packs).astype(np.float16).astype(np.float32)


@dataclass(frozen=True)
class LinearArtifact:
    classes: tuple[str, ...]
    coef: np.ndarray
    intercept: np.ndarray
    c: float
    class_weights: np.ndarray

    def logits(self, features: np.ndarray) -> np.ndarray:
        array = np.asarray(features, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.coef.shape[1]:
            raise ValueError(f"linear head expected [n, {self.coef.shape[1]}], got {array.shape}")
        return (array @ self.coef.T + self.intercept).astype(np.float32, copy=False)

    def predict(self, features: np.ndarray) -> np.ndarray:
        indices = np.argmax(self.logits(features), axis=1)
        return np.asarray(self.classes)[indices]


@dataclass(frozen=True)
class MLPArtifact:
    classes: tuple[str, ...]
    layer_norm_weight: np.ndarray
    layer_norm_bias: np.ndarray
    linear1_weight: np.ndarray
    linear1_bias: np.ndarray
    linear2_weight: np.ndarray
    linear2_bias: np.ndarray
    class_weights: np.ndarray
    layer_norm_eps: float = 1e-5

    def logits(self, features: np.ndarray) -> np.ndarray:
        array = np.asarray(features, dtype=np.float32)
        input_dim = self.layer_norm_weight.shape[0]
        if array.ndim != 2 or array.shape[1] != input_dim:
            raise ValueError(f"MLP head expected [n, {input_dim}], got {array.shape}")
        mean = array.mean(axis=1, keepdims=True)
        variance = array.var(axis=1, keepdims=True)
        hidden = (array - mean) / np.sqrt(variance + self.layer_norm_eps)
        hidden = hidden * self.layer_norm_weight + self.layer_norm_bias
        hidden = hidden @ self.linear1_weight.T + self.linear1_bias
        # Match torch.nn.GELU(approximate="tanh") used during fitting.
        hidden = (
            0.5 * hidden * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (hidden + 0.044715 * hidden**3)))
        )
        return (hidden @ self.linear2_weight.T + self.linear2_bias).astype(np.float32, copy=False)

    def predict(self, features: np.ndarray) -> np.ndarray:
        indices = np.argmax(self.logits(features), axis=1)
        return np.asarray(self.classes)[indices]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _temporary_path(path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    return Path(name)


def _acquire_artifact_lock(
    directory: Path,
    *,
    cache_db: Path | None = None,
    shared_root: Path | None = None,
):
    return acquire_pipeline_lock(
        directory,
        cache_db=cache_db,
        shared_root=shared_root,
    )


def _atomic_savez(path: Path, **arrays: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def label_provenance_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and preserve every hard-rule field used to admit WAL rows."""
    missing = [field for field in LABEL_PROVENANCE_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"missing label provenance fields: {', '.join(missing)}")
    if manifest["teacher_model"] != PINNED_TEACHER_MODEL:
        raise ValueError("label manifest does not name the pinned teacher model")
    if manifest["teacher_api_model"] != PINNED_TEACHER_API_MODEL:
        raise ValueError("label manifest does not name the pinned teacher API alias")
    raw_temperature = manifest["temperature"]
    if (
        isinstance(raw_temperature, bool)
        or not isinstance(raw_temperature, (int, float))
        or not math.isfinite(float(raw_temperature))
        or float(raw_temperature) != 0.0
    ):
        raise ValueError("label manifest temperature must be exactly 0.0")
    for field in ("prompt_sha256", "schema_sha256"):
        value = manifest[field]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in string.hexdigits for character in value)
        ):
            raise ValueError(f"label manifest {field} must be a SHA-256 hex digest")
    if manifest["prompt_sha256"] != PINNED_PROMPT_SHA256:
        raise ValueError("label manifest prompt digest is not the validated location policy")
    if manifest["schema_sha256"] != PINNED_SCHEMA_SHA256:
        raise ValueError("label manifest schema digest is not the validated costless escape")
    return {
        "teacher_model": PINNED_TEACHER_MODEL,
        "teacher_api_model": PINNED_TEACHER_API_MODEL,
        "temperature": 0.0,
        "prompt_sha256": manifest["prompt_sha256"],
        "schema_sha256": manifest["schema_sha256"],
    }


def seal_training_report(
    report: Mapping[str, Any],
    *,
    component_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Bind the complete metrics payload to its exact manifest and candidates."""
    if "training_run_sha256" in report or "component_sha256" in report:
        raise ValueError("training report is already sealed")
    if set(component_paths) != set(TRAINING_COMPONENTS):
        raise ValueError("training component paths do not match the required bundle")
    sealed = dict(report)
    sealed["component_sha256"] = {
        name: _file_sha256(component_paths[name]) for name in TRAINING_COMPONENTS
    }
    sealed["training_run_sha256"] = _canonical_json_sha256(sealed)
    return sealed


def validate_training_report(
    report: Mapping[str, Any],
    *,
    component_paths: Mapping[str, Path],
) -> str:
    """Reject edited metrics or any stale/swapped file in a training bundle."""
    if set(component_paths) != set(TRAINING_COMPONENTS):
        raise ValueError("training component paths do not match the required bundle")
    component_digests = {name: _file_sha256(component_paths[name]) for name in TRAINING_COMPONENTS}
    return _validate_training_report_digests(report, component_digests=component_digests)


def _validate_training_report_digests(
    report: Mapping[str, Any],
    *,
    component_digests: Mapping[str, str],
) -> str:
    if set(component_digests) != set(TRAINING_COMPONENTS):
        raise ValueError("training component digest set does not match the required bundle")
    stored_run = report.get("training_run_sha256")
    if not isinstance(stored_run, str) or len(stored_run) != 64:
        raise ValueError("training report has no valid training run digest")
    unsigned = dict(report)
    unsigned.pop("training_run_sha256", None)
    if _canonical_json_sha256(unsigned) != stored_run:
        raise ValueError("training report digest does not match its metrics")
    component_sha256 = report.get("component_sha256")
    if not isinstance(component_sha256, dict) or set(component_sha256) != set(TRAINING_COMPONENTS):
        raise ValueError("training report component digest set is incomplete")
    for name in TRAINING_COMPONENTS:
        if component_sha256[name] != component_digests[name]:
            raise ValueError(f"training component digest mismatch: {name}")
    return stored_run


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_training_runs_directory(
    artifact_dir: Path,
    *,
    create: bool,
) -> Path:
    artifact_directory = Path(artifact_dir)
    if artifact_directory.is_symlink():
        raise ValueError("training artifact directory must be a local directory")
    if create:
        artifact_directory.mkdir(parents=True, exist_ok=True)
    if not artifact_directory.is_dir():
        raise ValueError("training artifact directory must be a local directory")
    runs_directory = artifact_directory / TRAINING_RUNS_DIRECTORY
    if runs_directory.is_symlink():
        raise ValueError("training-runs must be a private local directory")
    if create:
        runs_directory.mkdir(mode=0o700, exist_ok=True)
    if not runs_directory.is_dir():
        raise ValueError("training-runs must be a private local directory")
    runs_stat = runs_directory.lstat()
    if not stat.S_ISDIR(runs_stat.st_mode) or runs_stat.st_mode & 0o077:
        raise PermissionError("training-runs must be a private local directory")
    return runs_directory


def _require_single_link_regular_file(
    path: Path,
    *,
    label: str,
    required_mode: int | None = None,
) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} must be a single-link regular file")
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} must be a single-link regular file") from error
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ValueError(f"{label} must be a single-link regular file")
    mode = stat.S_IMODE(status.st_mode)
    if required_mode is None:
        if mode & 0o077:
            raise PermissionError(f"{label} must not be group- or world-accessible")
    elif mode != required_mode:
        raise PermissionError(f"{label} must have mode {required_mode:04o}")


def _validate_staging_bundle_layout(
    staging: TrainingBundleStaging,
    *,
    runs_directory: Path,
    include_report: bool,
) -> None:
    expected_root = _lexical_absolute(runs_directory)
    staging_root = _lexical_absolute(staging.root)
    if (
        staging.root.is_symlink()
        or not staging.root.is_dir()
        or staging_root.parent != expected_root
        or not staging.root.name.startswith(".staging-")
        or staging.root.lstat().st_mode & 0o077
    ):
        raise ValueError("training bundle staging path is not owned by this artifact directory")
    filenames = training_component_filenames(staging.head)
    expected_paths = {name: staging.root / filename for name, filename in filenames.items()}
    if set(staging.component_paths) != set(expected_paths) or any(
        _lexical_absolute(staging.component_paths[name]) != _lexical_absolute(expected_paths[name])
        for name in expected_paths
    ):
        raise ValueError("training staging component layout is not canonical")
    expected_names = set(filenames.values())
    if include_report:
        expected_names.add(TRAINING_REPORT_FILENAME)
    children = tuple(staging.root.iterdir())
    if {child.name for child in children} != expected_names:
        raise ValueError("training staging component layout is incomplete or contains extras")
    for child in children:
        _require_single_link_regular_file(child, label="training staging component")


def _validate_committed_bundle_layout(bundle_root: Path, *, head: HeadSpec) -> None:
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ValueError("training current pointer does not name an immutable bundle")
    if stat.S_IMODE(bundle_root.lstat().st_mode) != 0o500:
        raise PermissionError("immutable training bundle directory must have mode 0500")
    expected_names = {*training_component_filenames(head).values(), TRAINING_REPORT_FILENAME}
    children = tuple(bundle_root.iterdir())
    if {child.name for child in children} != expected_names:
        raise ValueError("content-addressed training bundle has an unexpected file set")
    for child in children:
        _require_single_link_regular_file(
            child,
            label="immutable training bundle component",
            required_mode=0o400,
        )


def new_training_bundle_staging(
    artifact_dir: Path, *, head: HeadSpec = LOCATION
) -> TrainingBundleStaging:
    """Create one private staging directory that cannot disturb a committed run."""
    runs_directory = _require_training_runs_directory(artifact_dir, create=True)
    root = Path(tempfile.mkdtemp(prefix=".staging-", dir=runs_directory))
    return TrainingBundleStaging(
        root=root,
        component_paths={
            name: root / filename for name, filename in training_component_filenames(head).items()
        },
        head=head,
    )


def _training_pointer(report: Mapping[str, Any], report_path: Path) -> dict[str, Any]:
    run_sha256 = report.get("training_run_sha256")
    if not _is_sha256(run_sha256):
        raise ValueError("training report has no content-addressable run digest")
    pointer: dict[str, Any] = {
        "schema_version": "triage-training-current-v1",
        "training_run_sha256": run_sha256,
        "report_sha256": _file_sha256(report_path),
    }
    pointer["pointer_sha256"] = _canonical_json_sha256(pointer)
    return pointer


def _remove_staging_directory(staging: TrainingBundleStaging) -> None:
    expected = {*training_component_filenames(staging.head).values(), TRAINING_REPORT_FILENAME}
    children = tuple(staging.root.iterdir())
    if {child.name for child in children} != expected or any(
        child.is_symlink() or not child.is_file() for child in children
    ):
        raise RuntimeError("training staging directory contains unexpected files")
    for child in children:
        child.unlink()
    staging.root.rmdir()


def _publish_training_directory(
    runs_directory: Path,
    staging: TrainingBundleStaging,
    run_sha256: str,
) -> Path:
    destination = runs_directory / run_sha256
    expected_names = {
        *training_component_filenames(staging.head).values(),
        TRAINING_REPORT_FILENAME,
    }
    if destination.exists():
        _validate_committed_bundle_layout(destination, head=staging.head)
        destination_files = {path.name: path for path in destination.iterdir()}
        staging_files = {path.name: path for path in staging.root.iterdir()}
        if set(destination_files) != expected_names or set(staging_files) != expected_names:
            raise RuntimeError("content-addressed training bundle has an unexpected file set")
        if any(
            destination_files[name].is_symlink()
            or not destination_files[name].is_file()
            or destination_files[name].read_bytes() != staging_files[name].read_bytes()
            for name in expected_names
        ):
            raise RuntimeError("training run digest already names different immutable content")
        _remove_staging_directory(staging)
    else:
        for path in staging.root.iterdir():
            path.chmod(0o400)
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        staging.root.chmod(0o500)
        _fsync_directory(staging.root)
        staging.root.replace(destination)
        _fsync_directory(runs_directory)
    _validate_committed_bundle_layout(destination, head=staging.head)
    _fsync_directory(runs_directory)
    return destination


def commit_training_bundle(
    artifact_dir: Path,
    staging: TrainingBundleStaging,
    report: Mapping[str, Any],
    *,
    pre_publish: Callable[[], None] | None = None,
    commit_guard: Callable[[Callable[[], None]], Any] | None = None,
) -> dict[str, Any]:
    """Publish a complete immutable bundle through one atomic current pointer."""
    artifact_directory = Path(artifact_dir)
    runs_directory = _require_training_runs_directory(artifact_directory, create=False)
    _validate_staging_bundle_layout(
        staging,
        runs_directory=runs_directory,
        include_report=False,
    )
    run_sha256 = validate_training_report(report, component_paths=staging.component_paths)
    _write_json(staging.report_path, dict(report))
    _validate_staging_bundle_layout(
        staging,
        runs_directory=runs_directory,
        include_report=True,
    )
    pointer = _training_pointer(report, staging.report_path)
    published = False

    def publish() -> None:
        nonlocal published
        _validate_staging_bundle_layout(
            staging,
            runs_directory=runs_directory,
            include_report=True,
        )
        if validate_training_report(report, component_paths=staging.component_paths) != run_sha256:
            raise RuntimeError("staged training bundle changed before publication")
        if _file_sha256(staging.report_path) != pointer["report_sha256"]:
            raise RuntimeError("staged training report changed before publication")
        _publish_training_directory(runs_directory, staging, run_sha256)
        if pre_publish is not None:
            pre_publish()
        _write_json(artifact_directory / TRAINING_CURRENT_POINTER, pointer)
        (artifact_directory / TRAINING_CURRENT_POINTER).chmod(0o600)
        published = True

    if commit_guard is None:
        publish()
    else:
        commit_guard(publish)
    if not published:
        raise RuntimeError("training commit guard returned without publishing the bundle")
    return pointer


def _resolve_training_bundle_reference(
    artifact_dir: Path,
    *,
    allow_legacy: bool = False,
    head: HeadSpec = LOCATION,
) -> _TrainingBundleReference:
    artifact_directory = Path(artifact_dir)
    if artifact_directory.is_symlink():
        raise ValueError("training artifact directory must be a local directory")
    pointer_path = artifact_directory / TRAINING_CURRENT_POINTER
    if pointer_path.is_symlink():
        raise ValueError("training current pointer must be a regular local file")
    if not pointer_path.exists():
        if not allow_legacy:
            raise ValueError(
                "training current pointer is missing; legacy fallback was not authorized"
            )
        return _TrainingBundleReference(
            report_path=artifact_directory / TRAINING_REPORT_FILENAME,
            component_paths={
                name: artifact_directory / filename
                for name, filename in TRAINING_COMPONENT_FILENAMES.items()
            },
            pointer=None,
        )
    if not pointer_path.is_file():
        raise ValueError("training current pointer must be a regular local file")
    _require_single_link_regular_file(
        pointer_path,
        label="training current pointer",
        required_mode=0o600,
    )
    try:
        pointer = json.loads(pointer_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("training current pointer is not valid JSON") from error
    expected_keys = {
        "schema_version",
        "training_run_sha256",
        "report_sha256",
        "pointer_sha256",
    }
    if not isinstance(pointer, dict) or set(pointer) != expected_keys:
        raise ValueError("training current pointer does not match its schema")
    pointer_sha256 = pointer["pointer_sha256"]
    unsigned = dict(pointer)
    unsigned.pop("pointer_sha256")
    if (
        pointer["schema_version"] != "triage-training-current-v1"
        or not _is_sha256(pointer["training_run_sha256"])
        or not _is_sha256(pointer["report_sha256"])
        or not _is_sha256(pointer_sha256)
        or _canonical_json_sha256(unsigned) != pointer_sha256
    ):
        raise ValueError("training current pointer digest does not reproduce")
    runs_directory = _require_training_runs_directory(artifact_directory, create=False)
    bundle_root = runs_directory / pointer["training_run_sha256"]
    _validate_committed_bundle_layout(bundle_root, head=head)
    report_path = bundle_root / TRAINING_REPORT_FILENAME
    component_paths = {
        name: bundle_root / filename
        for name, filename in training_component_filenames(head).items()
    }
    return _TrainingBundleReference(
        report_path=report_path,
        component_paths=component_paths,
        pointer=pointer,
    )


def _snapshot_training_bundle_reference(
    reference: _TrainingBundleReference,
) -> TrainingBundleSnapshot:
    paths = (reference.report_path, *reference.component_paths.values())
    if reference.pointer is None:
        for path in paths:
            _require_single_link_regular_file(path, label="legacy training bundle component")
    report_bytes = reference.report_path.read_bytes()
    pointer = reference.pointer
    if pointer is not None and hashlib.sha256(report_bytes).hexdigest() != pointer["report_sha256"]:
        raise ValueError("training report differs from the current pointer")
    try:
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("current training report is not valid JSON") from error
    if not isinstance(report, dict):
        raise ValueError("current training report must contain one JSON object")
    component_bytes = {name: path.read_bytes() for name, path in reference.component_paths.items()}
    training_run_sha256 = _validate_training_report_digests(
        report,
        component_digests={
            name: hashlib.sha256(payload).hexdigest() for name, payload in component_bytes.items()
        },
    )
    if pointer is not None and training_run_sha256 != pointer["training_run_sha256"]:
        raise ValueError("training report does not match the current pointer")
    return TrainingBundleSnapshot(
        report_path=reference.report_path,
        component_paths=dict(reference.component_paths),
        report=report,
        report_bytes=report_bytes,
        component_bytes=component_bytes,
        training_run_sha256=training_run_sha256,
        pointer_sha256=None if pointer is None else str(pointer["pointer_sha256"]),
    )


def snapshot_training_bundle(
    artifact_dir: Path,
    *,
    allow_legacy: bool = False,
    head: HeadSpec = LOCATION,
) -> TrainingBundleSnapshot:
    """Read and authenticate the exact report/component bytes used by certification."""
    reference = _resolve_training_bundle_reference(
        artifact_dir,
        allow_legacy=allow_legacy,
        head=head,
    )
    return _snapshot_training_bundle_reference(reference)


def resolve_training_bundle(
    artifact_dir: Path,
    *,
    allow_legacy: bool = False,
    head: HeadSpec = LOCATION,
) -> tuple[Path, dict[str, Path]]:
    """Resolve an immutable bundle, with opt-in legacy-path support."""
    reference = _resolve_training_bundle_reference(
        artifact_dir,
        allow_legacy=allow_legacy,
        head=head,
    )
    if reference.pointer is not None:
        _snapshot_training_bundle_reference(reference)
    return reference.report_path, dict(reference.component_paths)


def fit_pca(
    packs: np.ndarray,
    *,
    components: int = 256,
    memory_guard: Callable[[], int] | None = None,
) -> PCAArtifact:
    """Fit randomized PCA; callers control which rows cross this boundary."""
    from sklearn.decomposition import PCA

    array = np.asarray(packs, dtype=np.float32)
    if array.ndim != 2:
        raise ValueError("PCA input must be a two-dimensional matrix")
    if components < 1 or components > min(array.shape):
        raise ValueError(f"cannot fit {components} PCA components to shape {array.shape}")
    if memory_guard is not None:
        memory_guard()
    model = PCA(n_components=components, svd_solver="randomized", random_state=42)
    model.fit(array)
    if memory_guard is not None:
        memory_guard()
    return PCAArtifact(
        mean=np.asarray(model.mean_, dtype=np.float32),
        components=np.asarray(model.components_, dtype=np.float32),
        explained_variance=np.asarray(model.explained_variance_, dtype=np.float32),
    )


def pca_sha256(artifact: PCAArtifact) -> str:
    """Hash PCA semantics, independent of NPZ container timestamps/compression."""
    digest = hashlib.sha256(b"triage-pca-v1\0")
    for name, value in (
        ("mean", artifact.mean),
        ("components", artifact.components),
        ("explained_variance", artifact.explained_variance),
    ):
        array = np.ascontiguousarray(value, dtype="<f4")
        digest.update(name.encode("ascii") + b"\0")
        digest.update(json.dumps(array.shape, separators=(",", ":")).encode("ascii") + b"\0")
        digest.update(array.tobytes())
    return digest.hexdigest()


def save_pca_artifact(path: Path, artifact: PCAArtifact) -> None:
    _atomic_savez(
        path,
        artifact_version=np.array("triage-pca-v1"),
        mean=artifact.mean,
        components=artifact.components,
        explained_variance=artifact.explained_variance,
    )


def load_pca_artifact(path: Path) -> PCAArtifact:
    with np.load(path, allow_pickle=False) as payload:
        if str(payload["artifact_version"]) != "triage-pca-v1":
            raise ValueError("unsupported PCA artifact version")
        return PCAArtifact(
            mean=np.asarray(payload["mean"], dtype=np.float32),
            components=np.asarray(payload["components"], dtype=np.float32),
            explained_variance=np.asarray(payload["explained_variance"], dtype=np.float32),
        )


def _class_weights(labels: np.ndarray, classes: np.ndarray) -> dict[str, float]:
    counts = Counter(str(label) for label in labels)
    weights = {
        str(label): min(10.0, len(labels) / (len(classes) * counts[str(label)]))
        for label in classes
    }
    return weights


def fit_linear_candidate(
    features: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    *,
    memory_guard: Callable[[], int] | None = None,
) -> tuple[LinearArtifact, dict[float, float]]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_score

    x = np.asarray(features, dtype=np.float32)
    y = np.asarray(labels, dtype=str)
    group_array = np.asarray(groups, dtype=str)
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(group_array):
        raise ValueError("features, labels, and groups must have matching rows")
    classes = np.unique(y)
    if len(classes) < 2:
        raise ValueError("linear head needs at least two classes")
    weights = _class_weights(y, classes)
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=42)
    scores: dict[float, float] = {}
    for c in C_GRID:
        if memory_guard is not None:
            memory_guard()
        model = LogisticRegression(
            C=c,
            class_weight=weights,
            max_iter=5000,
            random_state=42,
        )
        fold_scores = cross_val_score(
            model,
            x,
            y,
            groups=group_array,
            cv=cv,
            scoring="accuracy",
            n_jobs=1,
        )
        scores[c] = float(np.mean(fold_scores))
        if memory_guard is not None:
            memory_guard()
    best_c = max(C_GRID, key=lambda value: (scores[value], -value))
    model = LogisticRegression(
        C=best_c,
        class_weight=weights,
        max_iter=5000,
        random_state=42,
    )
    model.fit(x, y)
    if memory_guard is not None:
        memory_guard()
    ordered_classes = tuple(str(label) for label in model.classes_)
    coef = np.asarray(model.coef_, dtype=np.float32)
    intercept = np.asarray(model.intercept_, dtype=np.float32)
    if len(ordered_classes) == 2:
        # sklearn stores a binary logit as one row for classes_[1]; the artifact
        # contract is one row per class (softmax over rows), so class 0 gets the
        # zero row — softmax([0, z]) == sigmoid(z), the same decision.
        coef = np.vstack([np.zeros_like(coef), coef])
        intercept = np.concatenate([np.zeros_like(intercept), intercept])
    return (
        LinearArtifact(
            classes=ordered_classes,
            coef=coef,
            intercept=intercept,
            c=float(best_c),
            class_weights=np.asarray(
                [weights[label] for label in ordered_classes], dtype=np.float32
            ),
        ),
        scores,
    )


def save_linear_artifact(path: Path, artifact: LinearArtifact) -> None:
    _atomic_savez(
        path,
        artifact_version=np.array("triage-linear-v1"),
        classes=np.asarray(artifact.classes),
        coef=artifact.coef,
        intercept=artifact.intercept,
        c=np.array(artifact.c, dtype=np.float64),
        class_weights=artifact.class_weights,
    )


def load_linear_artifact(path: Path) -> LinearArtifact:
    with np.load(path, allow_pickle=False) as payload:
        if str(payload["artifact_version"]) != "triage-linear-v1":
            raise ValueError("unsupported linear-head artifact version")
        return LinearArtifact(
            classes=tuple(str(label) for label in payload["classes"].tolist()),
            coef=np.asarray(payload["coef"], dtype=np.float32),
            intercept=np.asarray(payload["intercept"], dtype=np.float32),
            c=float(payload["c"]),
            class_weights=np.asarray(payload["class_weights"], dtype=np.float32),
        )


def fit_mlp_candidate(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    hidden_dim: int = 256,
    epochs: int = 100,
    batch_size: int = 256,
    memory_guard: Callable[[], int] | None = None,
) -> MLPArtifact:
    import torch
    from torch import nn

    x = np.asarray(features, dtype=np.float32)
    y = np.asarray(labels, dtype=str)
    if x.ndim != 2 or len(x) != len(y):
        raise ValueError("features and labels must have matching rows")
    if hidden_dim < 1 or epochs < 1 or batch_size < 1:
        raise ValueError("hidden_dim, epochs, and batch_size must be positive")
    classes = np.unique(y)
    if len(classes) < 2:
        raise ValueError("MLP head needs at least two classes")
    weights = _class_weights(y, classes)
    class_to_index = {label: index for index, label in enumerate(classes)}
    targets = np.asarray([class_to_index[label] for label in y], dtype=np.int64)

    torch.manual_seed(42)
    torch.set_num_threads(1)

    class Model(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layer_norm = nn.LayerNorm(x.shape[1])
            self.linear1 = nn.Linear(x.shape[1], hidden_dim)
            self.gelu = nn.GELU(approximate="tanh")
            self.dropout = nn.Dropout(0.2)
            self.linear2 = nn.Linear(hidden_dim, len(classes))

        def forward(self, batch):
            batch = self.layer_norm(batch)
            batch = self.linear1(batch)
            batch = self.gelu(batch)
            batch = self.dropout(batch)
            return self.linear2(batch)

    model = Model()
    feature_tensor = torch.from_numpy(x)
    target_tensor = torch.from_numpy(targets)
    loss_fn = nn.CrossEntropyLoss(
        weight=torch.tensor([weights[str(label)] for label in classes], dtype=torch.float32)
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    generator = torch.Generator().manual_seed(42)
    model.train()
    for _epoch in range(epochs):
        if memory_guard is not None:
            memory_guard()
        order = torch.randperm(len(feature_tensor), generator=generator)
        for start in range(0, len(order), batch_size):
            indices = order[start : start + batch_size]
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(feature_tensor[indices]), target_tensor[indices])
            loss.backward()
            optimizer.step()

    model.eval()
    if memory_guard is not None:
        memory_guard()
    return MLPArtifact(
        classes=tuple(str(label) for label in classes),
        layer_norm_weight=model.layer_norm.weight.detach().numpy().astype(np.float32),
        layer_norm_bias=model.layer_norm.bias.detach().numpy().astype(np.float32),
        linear1_weight=model.linear1.weight.detach().numpy().astype(np.float32),
        linear1_bias=model.linear1.bias.detach().numpy().astype(np.float32),
        linear2_weight=model.linear2.weight.detach().numpy().astype(np.float32),
        linear2_bias=model.linear2.bias.detach().numpy().astype(np.float32),
        class_weights=np.asarray([weights[label] for label in classes], dtype=np.float32),
    )


def save_mlp_artifact(path: Path, artifact: MLPArtifact) -> None:
    _atomic_savez(
        path,
        artifact_version=np.array("triage-mlp-v1"),
        classes=np.asarray(artifact.classes),
        layer_norm_weight=artifact.layer_norm_weight,
        layer_norm_bias=artifact.layer_norm_bias,
        linear1_weight=artifact.linear1_weight,
        linear1_bias=artifact.linear1_bias,
        linear2_weight=artifact.linear2_weight,
        linear2_bias=artifact.linear2_bias,
        class_weights=artifact.class_weights,
        layer_norm_eps=np.array(artifact.layer_norm_eps, dtype=np.float64),
    )


def load_mlp_artifact(path: Path) -> MLPArtifact:
    with np.load(path, allow_pickle=False) as payload:
        if str(payload["artifact_version"]) != "triage-mlp-v1":
            raise ValueError("unsupported MLP-head artifact version")
        return MLPArtifact(
            classes=tuple(str(label) for label in payload["classes"].tolist()),
            layer_norm_weight=np.asarray(payload["layer_norm_weight"], dtype=np.float32),
            layer_norm_bias=np.asarray(payload["layer_norm_bias"], dtype=np.float32),
            linear1_weight=np.asarray(payload["linear1_weight"], dtype=np.float32),
            linear1_bias=np.asarray(payload["linear1_bias"], dtype=np.float32),
            linear2_weight=np.asarray(payload["linear2_weight"], dtype=np.float32),
            linear2_bias=np.asarray(payload["linear2_bias"], dtype=np.float32),
            class_weights=np.asarray(payload["class_weights"], dtype=np.float32),
            layer_norm_eps=float(payload["layer_norm_eps"]),
        )


def validate_never_train(examples: list[TrainingExample], never_train_ids: set[str]) -> None:
    overlap_count = sum(example.asset_id in never_train_ids for example in examples)
    if overlap_count:
        raise ValueError(f"{overlap_count} verified-truth assets leaked into the training labels")


def validate_label_embedding_lineage(
    examples: list[TrainingExample],
    staging_provenance: Mapping[str, tuple[str, str]],
) -> None:
    expected_ids = {example.asset_id for example in examples}
    if set(staging_provenance) != expected_ids:
        raise ValueError(
            f"embedding lineage covers {len(staging_provenance)} of {len(expected_ids)} labels"
        )
    mismatches = sum(
        staging_provenance[example.asset_id] != (example.preview_sha256, example.source_updated)
        for example in examples
    )
    if mismatches:
        raise ValueError(f"{mismatches} labels do not match their embedded pixels/source")


def training_pack_snapshot_sha256(
    examples: list[TrainingExample],
    split_by_asset: Mapping[str, str],
    packs: Mapping[str, np.ndarray],
    *,
    head: HeadSpec = LOCATION,
) -> str:
    """Bind the exact staging features consumed by one training run."""
    expected_ids = {example.asset_id for example in examples}
    if set(packs) != expected_ids or set(split_by_asset) != expected_ids:
        raise ValueError("training pack snapshot does not cover the exact label inventory")
    rows = []
    for example in sorted(examples, key=lambda item: item.asset_id):
        pack = np.ascontiguousarray(packs[example.asset_id], dtype=np.float32)
        rows.append(
            {
                "asset_id": example.asset_id,
                "group_key": example.group_key,
                head.name: example.label,
                "pack_sha256": hashlib.sha256(pack.tobytes()).hexdigest(),
                "preview_sha256": example.preview_sha256,
                "source_updated": example.source_updated,
                "split": split_by_asset[example.asset_id],
            }
        )
    return _canonical_json_sha256({"schema": "triage-training-pack-snapshot-v1", "rows": rows})


def connected_split_groups(rows: Iterable[IndexedPreview]) -> dict[str, str]:
    """Map each capture-day group to the union of days its moments and components touch.

    A moment that runs past midnight sits in two capture days; splitting by day
    alone would put its two halves on different sides of train/cal/test.
    """
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    days: set[str] = set()
    for row in rows:
        day = f"capture-day:{row.capture_day}"
        days.add(day)
        union(day, f"moment:{row.moment_key}")
        union(day, f"component:{row.component_key}")
    return {day: find(day) for day in sorted(days)}


def assign_group_splits(
    examples: list[TrainingExample],
    *,
    seed: int = 42,
    merged_groups: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Greedily fill 70/10/20 partitions without splitting a capture group.

    ``merged_groups`` (from :func:`connected_split_groups`) widens the unit so
    capture days joined by a shared moment travel together.
    """
    if not examples:
        raise ValueError("cannot split an empty training set")
    ids = [example.asset_id for example in examples]
    if len(set(ids)) != len(ids):
        raise ValueError("training asset ids must be unique")
    merged = merged_groups or {}
    unit_of = {
        example.group_key: merged.get(example.group_key, example.group_key) for example in examples
    }
    group_sizes = Counter(unit_of[example.group_key] for example in examples)
    groups = list(group_sizes.items())
    rng = random.Random(seed)
    rng.shuffle(groups)
    groups.sort(key=lambda item: item[1], reverse=True)

    total = len(examples)
    targets = {name: fraction * total for name, fraction in SPLIT_FRACTIONS.items()}
    filled = dict.fromkeys(SPLIT_FRACTIONS, 0)
    group_split: dict[str, str] = {}
    for group_key, size in groups:
        split = min(SPLIT_FRACTIONS, key=lambda name: filled[name] / targets[name])
        group_split[group_key] = split
        filled[split] += size
    return {example.asset_id: group_split[unit_of[example.group_key]] for example in examples}


def verify_no_group_leakage(
    examples: list[TrainingExample], split_by_asset: dict[str, str]
) -> dict[str, int]:
    groups_by_split: dict[str, set[str]] = defaultdict(set)
    for example in examples:
        try:
            split = split_by_asset[example.asset_id]
        except KeyError as error:
            raise AssertionError(f"asset missing from split: {example.asset_id}") from error
        if split not in SPLIT_FRACTIONS:
            raise AssertionError(f"unknown split {split!r}")
        groups_by_split[split].add(example.group_key)
    names = list(groups_by_split)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            overlap = groups_by_split[left] & groups_by_split[right]
            if overlap:
                raise AssertionError(
                    f"{len(overlap)} capture groups leak between {left} and {right}"
                )
    return {
        split: sum(split_by_asset[example.asset_id] == split for example in examples)
        for split in SPLIT_FRACTIONS
    }


def verify_split_class_coverage(
    examples: list[TrainingExample],
    split_by_asset: dict[str, str],
    *,
    head: HeadSpec = LOCATION,
) -> None:
    """Every outer partition must retain the head's complete class contract."""
    expected = set(head.classes)
    for split in SPLIT_FRACTIONS:
        observed = {
            example.label for example in examples if split_by_asset.get(example.asset_id) == split
        }
        missing = expected - observed
        if missing:
            raise ValueError(f"{split} split omits {', '.join(sorted(missing))}")


def load_training_examples(
    path: Path,
    *,
    expected_provenance: Mapping[str, object] | None = None,
) -> list[TrainingExample]:
    """Use the latest successful WAL row for each asset; error rows remain retryable."""
    successful: dict[str, TrainingExample] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "ok":
                continue
            if expected_provenance and any(
                row.get(key) != value for key, value in expected_provenance.items()
            ):
                continue
            location = str(row["location"])
            if location not in LOCATION.classes:
                raise ValueError(f"invalid training location class: {location!r}")
            example = TrainingExample(
                asset_id=str(row["asset_id"]),
                label=location,
                group_key=str(row["group_key"]),
                preview_sha256=str(row.get("preview_sha256", "")),
                source_updated=str(row.get("source_updated", "")),
            )
            if (
                len(example.preview_sha256) != 64
                or any(character not in string.hexdigits for character in example.preview_sha256)
                or not example.source_updated
            ):
                raise ValueError("training label has invalid preview/source provenance")
            # The WAL is append-only. A changed preview/source is relabeled and
            # the latest exact-provenance record supersedes its stale predecessor.
            successful[example.asset_id] = example
    return [successful[asset_id] for asset_id in sorted(successful)]


def validate_label_completion(
    manifest: dict,
    *,
    successful_count: int,
    inventory_sha256: str | None = None,
) -> None:
    if manifest.get("status") != "complete":
        raise ValueError("label run is not the full inventory and complete")
    full = int(manifest.get("full_inventory_count", -1))
    selected = int(manifest.get("selected_count", -1))
    recorded = int(manifest.get("successful_count", -1))
    if full < 1 or selected != full or recorded != full or successful_count != full:
        raise ValueError(
            "label run is not the full inventory: "
            f"full={full} selected={selected} recorded={recorded} loaded={successful_count}"
        )
    if (
        inventory_sha256 is not None
        and manifest.get("selected_inventory_sha256") != inventory_sha256
    ):
        raise ValueError("label run inventory digest does not match the successful WAL view")


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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_private_json_object(
    path: Path,
    *,
    label: str,
    require_canonical: bool,
) -> tuple[dict[str, Any], bytes]:
    resolved = Path(path)
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} must be a regular local file")
    if resolved.stat().st_mode & 0o077:
        raise PermissionError(f"{label} must not be group- or world-accessible")
    raw = resolved.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    if require_canonical and raw != _canonical_bytes(payload):
        raise ValueError(f"{label} bytes are not in the immutable canonical encoding")
    return payload, raw


def _require_private_directory(path: Path, *, label: str) -> None:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"{label} must be a private local directory")
    try:
        status = candidate.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} must be a private local directory") from error
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"{label} must be a private local directory")
    if stat.S_IMODE(status.st_mode) != 0o700:
        raise PermissionError(f"{label} must have mode 0700")


def _validate_v2_preview_store_files(store: Path) -> tuple[Path, ...]:
    """Authenticate the committed store and require unaliased private files."""
    _require_private_directory(store, label="owner v2 preview store")
    images = store / "images"
    _require_private_directory(images, label="owner v2 preview image directory")
    try:
        verify_committed_preview_store(store)
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        raise ValueError(f"owner v2 preview store is not committed: {error}") from error
    files = (
        store / "private-index.json",
        store / "manifest.json",
        store / "selection-lock.json",
        *sorted(images.iterdir(), key=lambda path: path.name),
    )
    for path in files:
        _require_single_link_regular_file(
            path,
            label="owner v2 preview evidence",
            required_mode=0o600,
        )
    return files


def _active_v1_label_assets(index: CohortPreviewIndex) -> list[LabelAsset]:
    return [
        LabelAsset(
            asset_id=row.asset_id,
            image_path=row.image_path,
            group_key=f"capture-day:{row.capture_day}",
            source_updated=row.source_updated,
            preview_sha256=row.preview_sha256,
        )
        for row in index.rows
    ]


def _validate_exact_active_run(
    run: Mapping[str, Any],
    *,
    expected_count: int,
    inventory_sha256: str,
    expected_provenance: Mapping[str, Any],
) -> None:
    if run.get("schema_version") != "triage-active-head-label-run-v1":
        raise ValueError("active-v1 run has the wrong schema")
    if run.get("status") != "complete":
        raise ValueError("active-v1 run is not complete")
    for field in ("full_inventory_count", "selected_count", "successful_count"):
        if type(run.get(field)) is not int or run[field] != expected_count:
            raise ValueError(f"active-v1 run {field} must be exactly {expected_count}")
    if type(run.get("error_count")) is not int or run["error_count"] != 0:
        raise ValueError("active-v1 run must have exactly zero errors")
    if run.get("selected_inventory_sha256") != inventory_sha256:
        raise ValueError("active-v1 run inventory does not match the authenticated pixels")
    if any(run.get(field) != value for field, value in expected_provenance.items()):
        raise ValueError("active-v1 run does not match the pinned multihead experiment")
    if run.get("source") != "private owner previews; local loopback omlx only":
        raise ValueError("active-v1 run does not attest the private loopback source")
    if run.get("cost_usd") != 0.0:
        raise ValueError("active-v1 run must attest zero external labeling cost")


def _validate_owner_quota_feasibility(
    value: Any,
    *,
    label: str,
    expected_count: int,
) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"owner {label} capacity evidence is malformed")
    expected_keys = {
        "schema",
        "algorithm",
        "target_size",
        "candidate_count",
        "eligible_inventory_count",
        "raw_sqrt_quota_total_variation",
        "raw_sqrt_quota_tv_gate",
        "feasible_reference_meets_raw_gate",
        "raw_gate_failure_waived",
        "raw_gate_failure_waiver_reason",
        "relaxed_lower_bound_tv",
        "computed_feasible_reference_tv",
        "feasible_reference_gap_over_lower_bound",
        "signed_delta_from_feasible_reference",
        "excess_over_feasible",
        "max_excess_over_feasible",
        "candidate_year_quarter_counts",
        "projected_capacity_upper_bounds",
        "projected_target_counts",
        "feasible_reference_counts",
        "selected_counts",
        "feasible_reference_selection_sha256",
        "hard_constraints",
        "passed",
        "failures",
        "capacity_evidence_sha256",
    }
    if set(value) != expected_keys:
        raise ValueError(f"owner {label} capacity evidence does not match the v2 schema")
    if value.get("schema") != "triage-training-capacity-aware-quota-audit-v1":
        raise ValueError(f"owner {label} capacity evidence has the wrong schema")
    if value.get("algorithm") != _OWNER_CAPACITY_ALGORITHM:
        raise ValueError(f"owner {label} capacity evidence has the wrong algorithm")
    evidence_digest = value.get("capacity_evidence_sha256")
    unsigned = dict(value)
    unsigned.pop("capacity_evidence_sha256", None)
    if (
        not _is_sha256(evidence_digest)
        or hashlib.sha256(_OWNER_CAPACITY_DOMAIN + _canonical_bytes(unsigned)).hexdigest()
        != evidence_digest
    ):
        raise ValueError(f"owner {label} capacity digest does not reproduce")
    if type(value.get("target_size")) is not int or value["target_size"] != expected_count:
        raise ValueError(f"owner {label} capacity target is not the exact cohort count")
    for field in ("candidate_count", "eligible_inventory_count"):
        if type(value.get(field)) is not int or value[field] < expected_count:
            raise ValueError(f"owner {label} capacity counts are malformed")
    numeric_fields = (
        "raw_sqrt_quota_total_variation",
        "raw_sqrt_quota_tv_gate",
        "relaxed_lower_bound_tv",
        "computed_feasible_reference_tv",
        "feasible_reference_gap_over_lower_bound",
        "signed_delta_from_feasible_reference",
        "excess_over_feasible",
        "max_excess_over_feasible",
    )
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), (int, float))
        or not math.isfinite(float(value[field]))
        for field in numeric_fields
    ):
        raise ValueError(f"owner {label} capacity metrics are malformed")
    if any(
        not isinstance(value.get(field), dict)
        or any(type(count) is not int or count < 0 for count in value[field].values())
        for field in (
            "candidate_year_quarter_counts",
            "projected_capacity_upper_bounds",
            "projected_target_counts",
            "feasible_reference_counts",
            "selected_counts",
        )
    ):
        raise ValueError(f"owner {label} capacity count maps are malformed")
    if (
        sum(value["projected_target_counts"].values()) != expected_count
        or sum(value["feasible_reference_counts"].values()) != expected_count
        or sum(value["selected_counts"].values()) != expected_count
        or not _is_sha256(value.get("feasible_reference_selection_sha256"))
    ):
        raise ValueError(f"owner {label} capacity witness is malformed")
    hard_constraints = value.get("hard_constraints")
    if not isinstance(hard_constraints, dict) or set(hard_constraints) != {
        "max_per_day",
        "max_per_moment",
        "component_uniqueness_required",
        "screen_signature_count_cap",
    }:
        raise ValueError(f"owner {label} capacity hard constraints are malformed")
    if (
        type(hard_constraints.get("max_per_day")) is not int
        or hard_constraints["max_per_day"] < 1
        or type(hard_constraints.get("max_per_moment")) is not int
        or hard_constraints["max_per_moment"] < 1
        or hard_constraints.get("component_uniqueness_required") is not True
        or (
            hard_constraints.get("screen_signature_count_cap") is not None
            and type(hard_constraints["screen_signature_count_cap"]) is not int
        )
    ):
        raise ValueError(f"owner {label} capacity hard constraints are malformed")
    feasible_meets_raw = value.get("feasible_reference_meets_raw_gate")
    raw_failure_waived = value.get("raw_gate_failure_waived")
    waiver_reason = value.get("raw_gate_failure_waiver_reason")
    if type(feasible_meets_raw) is not bool or type(raw_failure_waived) is not bool:
        raise ValueError(f"owner {label} capacity waiver evidence is malformed")
    raw_tv = float(value["raw_sqrt_quota_total_variation"])
    raw_gate = float(value["raw_sqrt_quota_tv_gate"])
    lower_bound = float(value["relaxed_lower_bound_tv"])
    reference_tv = float(value["computed_feasible_reference_tv"])
    expected_raw_failure = raw_tv > raw_gate + 1e-12
    expected_waiver = expected_raw_failure and lower_bound > raw_gate + 1e-12
    if feasible_meets_raw != (reference_tv <= raw_gate + 1e-12):
        raise ValueError(f"owner {label} capacity waiver evidence is malformed")
    if raw_failure_waived != expected_waiver:
        raise ValueError(f"owner {label} capacity waiver evidence is malformed")
    if raw_failure_waived:
        if waiver_reason != _OWNER_CAPACITY_WAIVER_REASON:
            raise ValueError(f"owner {label} capacity waiver evidence is malformed")
    elif waiver_reason is not None:
        raise ValueError(f"owner {label} capacity waiver evidence is malformed")
    if (
        abs(float(value["signed_delta_from_feasible_reference"]) - (raw_tv - reference_tv)) > 1e-12
        or abs(float(value["excess_over_feasible"]) - max(0.0, raw_tv - reference_tv)) > 1e-12
        or abs(
            float(value["feasible_reference_gap_over_lower_bound"])
            - max(0.0, reference_tv - lower_bound)
        )
        > 1e-12
        or float(value["max_excess_over_feasible"]) < 0
        or float(value["excess_over_feasible"]) > float(value["max_excess_over_feasible"]) + 1e-12
        or float(value["feasible_reference_gap_over_lower_bound"])
        > float(value["max_excess_over_feasible"]) + 1e-12
    ):
        raise ValueError(f"owner {label} capacity witness is internally inconsistent")
    if value.get("passed") is not True or value.get("failures") != []:
        raise ValueError(f"owner {label} capacity audit did not pass")
    return evidence_digest


def validate_owner_v2_nearest_pair_binding(private: Mapping[str, Any]) -> None:
    """Require the v2 nearest pair to name selected acquisition audit ids."""
    nearest = private.get("global_nearest_similarity")
    nearest_pair_ids = private.get("global_nearest_pair_audit_ids")
    private_rows = private.get("rows")
    if not isinstance(nearest, Mapping) or not isinstance(private_rows, list):
        raise ValueError("owner v2 global-nearest pair evidence is malformed")
    if (
        not isinstance(nearest_pair_ids, list)
        or len(nearest_pair_ids) != 2
        or any(not isinstance(value, str) or not value for value in nearest_pair_ids)
        or nearest_pair_ids != sorted(set(nearest_pair_ids))
        or hashlib.sha256(
            _OWNER_V2_NEAREST_DOMAIN
            + _canonical_bytes(
                {
                    "schema": "triage-owner-training-nearest-pair-v2",
                    "audit_ids": nearest_pair_ids,
                }
            )
        ).hexdigest()
        != nearest.get("nearest_pair_sha256")
    ):
        raise ValueError("owner v2 global-nearest pair evidence does not reproduce")
    selected_acquisition_ids = {
        str(row.get("acquisition_audit_id", "")) for row in private_rows if isinstance(row, Mapping)
    }
    if not set(nearest_pair_ids).issubset(selected_acquisition_ids):
        raise ValueError("owner v2 global-nearest pair lies outside the selected acquisition")


def _validate_owner_cohort_v1_seals(
    *,
    index: CohortPreviewIndex,
    cohort_index_path: Path,
    cohort_manifest_path: Path,
    truth_ledger_path: Path,
    expected_count: int,
) -> tuple[dict[str, Any], tuple[tuple[Path, str], ...], str]:
    cohort_root = cohort_manifest_path.parent
    if cohort_index_path.parent.parent.resolve() != cohort_root.resolve():
        raise ValueError("owner cohort index and outer manifest are not from the same store")
    public, public_bytes = _load_private_json_object(
        cohort_manifest_path,
        label="owner cohort manifest",
        require_canonical=True,
    )
    private_selection_path = cohort_root / "private-selection.json"
    private, private_bytes = _load_private_json_object(
        private_selection_path,
        label="owner private selection",
        require_canonical=True,
    )
    descriptor_audit_path = cohort_root / "descriptor-audit.json"
    descriptor_audit, descriptor_bytes = _load_private_json_object(
        descriptor_audit_path,
        label="owner descriptor audit",
        require_canonical=True,
    )

    public_digest = public.get("run_sha256")
    unsigned_public = dict(public)
    unsigned_public.pop("run_sha256", None)
    if (
        not _is_sha256(public_digest)
        or hashlib.sha256(_OWNER_COHORT_DOMAIN + _canonical_bytes(unsigned_public)).hexdigest()
        != public_digest
    ):
        raise ValueError("owner cohort outer seal does not reproduce")
    private_digest = private.get("private_selection_sha256")
    unsigned_private = dict(private)
    unsigned_private.pop("private_selection_sha256", None)
    if (
        not _is_sha256(private_digest)
        or hashlib.sha256(_OWNER_COHORT_DOMAIN + _canonical_bytes(unsigned_private)).hexdigest()
        != private_digest
    ):
        raise ValueError("owner private-selection seal does not reproduce")

    if public.get("schema") != "triage-owner-training-cohort-public-v1":
        raise ValueError("owner cohort manifest has the wrong schema")
    if public.get("status") != "sealed_exact_training_cohort":
        raise ValueError("owner cohort is not sealed as an exact training cohort")
    if public.get("required_truth_cohort") != ACTIVE_V1_TRUTH_COHORT:
        raise ValueError("owner cohort names the wrong reserved truth cohort")
    if public.get("inventory_sha256") != index.inventory_sha256:
        raise ValueError("owner cohort inventory differs from the authenticated preview store")
    config = public.get("config")
    acquisition = public.get("acquisition")
    final_selection = public.get("final_selection")
    private_evidence = public.get("private_evidence")
    if not all(
        isinstance(value, dict)
        for value in (config, acquisition, final_selection, private_evidence)
    ):
        raise ValueError("owner cohort manifest sections are malformed")
    if type(config.get("target_size")) is not int or config["target_size"] != expected_count:
        raise ValueError("owner cohort target size is not the exact active-v1 count")
    acquisition_target = config.get("acquisition_target")
    minimum_acquisition = (expected_count * 11 + 9) // 10
    if type(acquisition_target) is not int or acquisition_target < minimum_acquisition:
        raise ValueError("owner cohort acquisition target is below the sealed surplus floor")
    if final_selection.get("name") != ACTIVE_V1_COHORT_NAME:
        raise ValueError("owner cohort final selection has the wrong name")
    if final_selection.get("selection_sha256") != index.selection_sha256:
        raise ValueError("owner cohort selection differs from the authenticated preview store")
    if (
        type(private_evidence.get("final_preview_count")) is not int
        or private_evidence["final_preview_count"] != expected_count
        or private_evidence.get("final_preview_manifest_sha256") != index.manifest_sha256
        or private_evidence.get("private_selection_sha256") != private_digest
    ):
        raise ValueError("owner cohort private evidence does not bind the exact preview store")

    if private.get("schema") != "triage-private-owner-training-selection-v1":
        raise ValueError("owner private selection has the wrong schema")
    if acquisition.get("selection_sha256") != private.get("acquisition_selection_sha256"):
        raise ValueError("owner acquisition selection differs across its sealed manifests")
    if acquisition.get("quota_feasibility") != private.get("acquisition_quota_feasibility"):
        raise ValueError("owner acquisition quota evidence differs across its sealed manifests")
    if final_selection.get("quota_feasibility") != private.get("final_quota_feasibility"):
        raise ValueError("owner final quota evidence differs across its sealed manifests")
    acquisition_capacity_sha256 = _validate_owner_quota_feasibility(
        acquisition.get("quota_feasibility"),
        label="acquisition",
        expected_count=acquisition_target,
    )
    final_capacity_sha256 = _validate_owner_quota_feasibility(
        final_selection.get("quota_feasibility"),
        label="final",
        expected_count=expected_count,
    )
    for field, expected in (
        ("inventory_sha256", index.inventory_sha256),
        ("final_selection_sha256", index.selection_sha256),
        ("truth_ledger_sha256", public.get("truth_ledger_sha256")),
    ):
        if private.get(field) != expected:
            raise ValueError("owner private selection lineage differs from its outer seal")
    raw_pixel_audit = public.get("raw_pixel_audit")
    if not isinstance(raw_pixel_audit, dict) or (
        raw_pixel_audit.get("descriptor_sha256") != private.get("descriptor_sha256")
    ):
        raise ValueError("owner descriptor audit differs across its sealed manifests")
    if (
        descriptor_audit.get("schema") != "triage-private-owner-training-descriptors-v1"
        or descriptor_audit.get("acquisition_selection_sha256")
        != private.get("acquisition_selection_sha256")
        or hashlib.sha256(_OWNER_DESCRIPTOR_DOMAIN + _canonical_bytes(descriptor_audit)).hexdigest()
        != private.get("descriptor_sha256")
    ):
        raise ValueError("owner descriptor-audit seal does not reproduce")
    descriptor_binding_sha256 = private.get("preview_descriptor_binding_sha256")
    if (
        not _is_sha256(descriptor_binding_sha256)
        or private_evidence.get("preview_descriptor_binding_sha256") != descriptor_binding_sha256
    ):
        raise ValueError("owner preview/descriptor binding differs across its seals")
    private_rows = private.get("rows")
    if not isinstance(private_rows, list) or len(private_rows) != expected_count:
        raise ValueError("owner private selection does not contain the exact cohort")
    expected_row_keys = {
        "asset_id",
        "component_key",
        "moment_key",
        "capture_day",
        "stratum",
        "acquisition_audit_id",
        "final_audit_id",
        "raw_cluster_label",
        "preview_sha256",
        "descriptor_vector_sha256",
    }
    indexed_by_id = {row.asset_id: row for row in index.rows}
    selected_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_row in private_rows:
        if not isinstance(raw_row, dict) or set(raw_row) != expected_row_keys:
            raise ValueError("owner private selection row does not match its schema")
        asset_id = raw_row.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in selected_by_id:
            raise ValueError("owner private selection contains duplicate or invalid identities")
        selected_by_id[asset_id] = raw_row
    if set(selected_by_id) != set(indexed_by_id):
        raise ValueError("owner private selection and preview index cover different assets")
    for asset_id, indexed in indexed_by_id.items():
        selected = selected_by_id[asset_id]
        if (
            selected.get("component_key"),
            selected.get("moment_key"),
            selected.get("capture_day"),
            selected.get("final_audit_id"),
            selected.get("preview_sha256"),
        ) != (
            indexed.component_key,
            indexed.moment_key,
            indexed.capture_day,
            indexed.audit_id,
            indexed.preview_sha256,
        ):
            raise ValueError("owner private selection differs from authenticated preview rows")
        if not _is_sha256(selected.get("descriptor_vector_sha256")):
            raise ValueError("owner private selection has an invalid descriptor-vector digest")
    descriptor_binding_payload = {
        "schema": "triage-training-preview-descriptor-binding-v1",
        "descriptor_sha256": private.get("descriptor_sha256"),
        "rows": [
            {
                "asset_id": selected["asset_id"],
                "acquisition_audit_id": selected["acquisition_audit_id"],
                "final_audit_id": selected["final_audit_id"],
                "preview_sha256": selected["preview_sha256"],
                "descriptor_vector_sha256": selected["descriptor_vector_sha256"],
            }
            for selected in sorted(private_rows, key=lambda row: str(row["asset_id"]))
        ],
    }
    if (
        hashlib.sha256(
            _OWNER_DESCRIPTOR_DOMAIN + _canonical_bytes(descriptor_binding_payload)
        ).hexdigest()
        != descriptor_binding_sha256
    ):
        raise ValueError("owner preview/descriptor binding digest does not reproduce")
    descriptor_rows = descriptor_audit.get("rows")
    if not isinstance(descriptor_rows, list):
        raise ValueError("owner descriptor audit rows are malformed")
    descriptors_by_id: dict[str, Mapping[str, Any]] = {}
    for descriptor_row in descriptor_rows:
        if not isinstance(descriptor_row, dict):
            raise ValueError("owner descriptor audit row is malformed")
        descriptor_asset_id = descriptor_row.get("asset_id")
        if (
            not isinstance(descriptor_asset_id, str)
            or not descriptor_asset_id
            or descriptor_asset_id in descriptors_by_id
        ):
            raise ValueError("owner descriptor audit contains duplicate or invalid identities")
        descriptors_by_id[descriptor_asset_id] = descriptor_row
    if not set(selected_by_id).issubset(descriptors_by_id):
        raise ValueError("owner descriptor audit omits selected training assets")
    for asset_id, selected in selected_by_id.items():
        descriptor = descriptors_by_id[asset_id]
        if (
            descriptor.get("audit_id"),
            descriptor.get("preview_sha256"),
            descriptor.get("vector_sha256"),
        ) != (
            selected.get("acquisition_audit_id"),
            selected.get("preview_sha256"),
            selected.get("descriptor_vector_sha256"),
        ):
            raise ValueError("owner selected rows differ from their descriptor audit")

    ledger_path = Path(truth_ledger_path)
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ValueError("truth ledger must already exist as a regular local file")
    if ledger_path.stat().st_mode & 0o077:
        raise PermissionError("truth ledger must not be group- or world-accessible")
    ledger = TruthReservationLedger(ledger_path)
    ledger_sha256 = ledger.ledger_sha256()
    if not _is_sha256(public.get("truth_ledger_sha256")) or (
        public["truth_ledger_sha256"] != ledger_sha256
    ):
        raise ValueError("current truth ledger differs from the owner cohort seal")
    reservation = ledger.cohort_reservation(ACTIVE_V1_TRUTH_COHORT)
    if (
        reservation is None
        or reservation.inventory_sha256 != index.inventory_sha256
        or reservation.selected_count != ACTIVE_V1_TRUTH_COUNT
        or len(reservation.rows) != ACTIVE_V1_TRUTH_COUNT
        or any(row.reservation_kind != "mapped" for row in reservation.rows)
    ):
        raise ValueError("fresh-location-cert-v3 is not exactly 400 mapped truth rows")
    if public.get("fresh_truth_selection_sha256") != reservation.selection_sha256:
        raise ValueError("fresh truth selection differs from the owner cohort seal")
    blocked = ledger.blocklist()
    asset_overlap = set(indexed_by_id) & set(blocked.asset_ids)
    component_overlap = {row.component_key for row in index.rows} & set(blocked.component_keys)
    moment_overlap = {row.moment_key for row in index.rows} & set(blocked.moment_keys)
    if asset_overlap or component_overlap or moment_overlap:
        raise ValueError(
            "owner training cohort overlaps reserved truth by asset, component, or moment"
        )

    lineage = {
        "mode": "active-v1-owner",
        "expected_owner_count": expected_count,
        "active_run_sha256": "",
        "active_completion_sha256": "",
        "wal_sha256": "",
        "owner_cohort_index_sha256": index.private_index_sha256,
        "owner_preview_manifest_sha256": index.manifest_sha256,
        "owner_cohort_manifest_file_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "owner_cohort_run_sha256": public_digest,
        "owner_private_selection_file_sha256": hashlib.sha256(private_bytes).hexdigest(),
        "owner_private_selection_sha256": private_digest,
        "owner_preview_descriptor_binding_sha256": descriptor_binding_sha256,
        "owner_descriptor_sha256": private.get("descriptor_sha256"),
        "owner_descriptor_audit_file_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "owner_inventory_sha256": index.inventory_sha256,
        "owner_selection_sha256": index.selection_sha256,
        "owner_acquisition_selection_sha256": private.get("acquisition_selection_sha256"),
        "owner_acquisition_capacity_evidence_sha256": (acquisition_capacity_sha256),
        "owner_final_capacity_evidence_sha256": final_capacity_sha256,
        "truth_ledger_sha256": ledger_sha256,
        "required_truth_cohort": ACTIVE_V1_TRUTH_COHORT,
        "required_truth_count": ACTIVE_V1_TRUTH_COUNT,
        "fresh_truth_selection_sha256": reservation.selection_sha256,
    }
    source_files = (
        (cohort_index_path, index.private_index_sha256),
        (
            cohort_index_path.parent / "manifest.json",
            _file_sha256(cohort_index_path.parent / "manifest.json"),
        ),
        (cohort_manifest_path, hashlib.sha256(public_bytes).hexdigest()),
        (private_selection_path, hashlib.sha256(private_bytes).hexdigest()),
        (descriptor_audit_path, hashlib.sha256(descriptor_bytes).hexdigest()),
    )
    return lineage, source_files, ledger_sha256


def _validate_owner_cohort_seals(
    *,
    index: CohortPreviewIndex,
    cohort_index_path: Path,
    cohort_manifest_path: Path,
    truth_ledger_path: Path,
    expected_count: int,
) -> tuple[dict[str, Any], tuple[tuple[Path, str], ...], str]:
    """Authenticate the approved owner v2 cohort; v1 is never trainable."""
    supplied_index = Path(cohort_index_path)
    preview_store = supplied_index.parent
    cohort_root = preview_store.parent
    expected_index = preview_store / "private-index.json"
    expected_manifest = cohort_root / "training-cohort-public.json"
    if supplied_index.absolute() != expected_index.absolute():
        raise ValueError("owner cohort index is not the exact v2 private-index path")
    if (
        preview_store.name != "training-previews-v2"
        or Path(cohort_manifest_path).absolute() != expected_manifest.absolute()
    ):
        raise ValueError("owner cohort manifest is not the exact v2 manifest path")
    _require_private_directory(cohort_root, label="owner v2 cohort root")
    preview_source_files = _validate_v2_preview_store_files(preview_store)
    _require_single_link_regular_file(
        expected_manifest,
        label="owner v2 cohort manifest",
        required_mode=0o600,
    )
    public, public_bytes = _load_private_json_object(
        expected_manifest,
        label="owner v2 cohort manifest",
        require_canonical=True,
    )
    private_path = cohort_root / "private-selection.json"
    descriptor_path = cohort_root / "descriptor-audit.json"
    _require_single_link_regular_file(
        private_path,
        label="owner v2 private selection",
        required_mode=0o600,
    )
    _require_single_link_regular_file(
        descriptor_path,
        label="owner v2 descriptor audit",
        required_mode=0o600,
    )
    private, private_bytes = _load_private_json_object(
        private_path,
        label="owner v2 private selection",
        require_canonical=True,
    )
    descriptor, descriptor_bytes = _load_private_json_object(
        descriptor_path,
        label="owner v2 descriptor audit",
        require_canonical=True,
    )
    public_digest = public.get("run_sha256")
    unsigned_public = dict(public)
    unsigned_public.pop("run_sha256", None)
    private_digest = private.get("private_selection_sha256")
    unsigned_private = dict(private)
    unsigned_private.pop("private_selection_sha256", None)
    if (
        not _is_sha256(public_digest)
        or hashlib.sha256(_OWNER_V2_COHORT_DOMAIN + _canonical_bytes(unsigned_public)).hexdigest()
        != public_digest
        or not _is_sha256(private_digest)
        or hashlib.sha256(_OWNER_V2_COHORT_DOMAIN + _canonical_bytes(unsigned_private)).hexdigest()
        != private_digest
    ):
        raise ValueError("owner v2 outer/private seal does not reproduce")
    if (
        set(public) != _OWNER_V2_PUBLIC_KEYS
        or set(private) != _OWNER_V2_PRIVATE_KEYS
        or set(descriptor) != _OWNER_V2_DESCRIPTOR_KEYS
    ):
        raise ValueError("owner cohort evidence does not match the exact v2 schema")
    if public.get("schema") != "triage-owner-training-cohort-public-v2":
        raise ValueError("active training requires owner cohort public v2")
    if public.get("status") != "awaiting_visual_review":
        raise ValueError("owner v2 cohort is not at its visual-review boundary")
    if private.get("schema") != "triage-private-owner-training-selection-v2":
        raise ValueError("owner v2 private selection has the wrong schema")
    if descriptor.get("schema") != "triage-private-owner-training-descriptors-v2":
        raise ValueError("owner v2 descriptor audit has the wrong schema")
    config = public.get("config")
    acquisition = public.get("acquisition")
    final_selection = public.get("final_selection")
    private_evidence = public.get("private_evidence")
    raw_pixel_audit = public.get("raw_pixel_audit")
    source_v1 = public.get("source_v1")
    memory_provenance = public.get("memory_provenance")
    if not all(
        isinstance(section, dict)
        for section in (
            config,
            acquisition,
            final_selection,
            private_evidence,
            raw_pixel_audit,
            source_v1,
            memory_provenance,
        )
    ):
        raise ValueError("owner v2 cohort sections are malformed")
    if (
        set(config) != _OWNER_V2_CONFIG_KEYS
        or set(acquisition) != _OWNER_V2_ACQUISITION_KEYS
        or set(final_selection) != _OWNER_V2_FINAL_SELECTION_KEYS
        or set(private_evidence) != _OWNER_V2_PRIVATE_EVIDENCE_KEYS
        or set(memory_provenance) != _OWNER_V2_MEMORY_KEYS
    ):
        raise ValueError("owner cohort sections do not match the exact v2 schema")
    if (
        public.get("required_truth_cohort") != ACTIVE_V1_TRUTH_COHORT
        or public.get("semantic_inputs") != "disabled"
    ):
        raise ValueError("owner v2 manifest enables an unsupported evidence source")
    if type(config.get("target_size")) is not int or config["target_size"] != expected_count:
        raise ValueError("owner v2 target is not the exact active count")
    acquisition_target = config.get("acquisition_target")
    if type(acquisition_target) is not int or acquisition_target < (expected_count * 11 + 9) // 10:
        raise ValueError("owner v2 acquisition target is below the surplus floor")
    minimum_available = config.get("minimum_available")
    if (
        type(minimum_available) is not int
        or minimum_available < (expected_count * 11 + 9) // 10
        or minimum_available > acquisition_target
        or config.get("required_inventory_sha256") != index.inventory_sha256
        or type(config.get("required_inventory_count")) is not int
        or config["required_inventory_count"] != public.get("inventory_asset_count")
        or config.get("pixel_morphology_version") != descriptor.get("pixel_morphology_version")
    ):
        raise ValueError("owner v2 config lineage does not reproduce")
    process_cap = config.get("process_memory_limit_bytes")
    global_ceiling = config.get("system_memory_ceiling_bytes")
    if (
        type(process_cap) is not int
        or not 1 <= process_cap <= _OWNER_MAX_PROCESS_MEMORY_BYTES
        or global_ceiling != _OWNER_SYSTEM_MEMORY_CEILING_BYTES
        or memory_provenance.get("process_cap_bytes") != process_cap
        or memory_provenance.get("global_ceiling_bytes") != global_ceiling
        or memory_provenance.get("process_cap_gib") != process_cap / 1024**3
        or memory_provenance.get("global_ceiling_gib") != global_ceiling / 1024**3
    ):
        raise ValueError("owner v2 memory provenance exceeds or differs from its hard caps")
    if (
        final_selection.get("name") != ACTIVE_V1_COHORT_NAME
        or final_selection.get("selection_sha256") != index.selection_sha256
        or public.get("inventory_sha256") != index.inventory_sha256
        or private.get("inventory_sha256") != index.inventory_sha256
        or private.get("final_selection_sha256") != index.selection_sha256
        or private_evidence.get("final_preview_count") != expected_count
        or private_evidence.get("final_preview_manifest_sha256") != index.manifest_sha256
        or private_evidence.get("private_selection_sha256") != private_digest
    ):
        raise ValueError("owner v2 final preview lineage does not reproduce")
    source_keys = {
        "cohort_run_sha256",
        "review_manifest_sha256",
        "rejection_decision_sha256",
        "private_selection_sha256",
        "descriptor_sha256",
        "acquisition_selection_sha256",
        "acquisition_preview_manifest_sha256",
        "acquisition_private_index_sha256",
        "acquisition_selection_lock_sha256",
    }
    if (
        set(source_v1) != source_keys
        or any(not _is_sha256(value) for value in source_v1.values())
        or private.get("source_v1") != source_v1
    ):
        raise ValueError("owner v2 outer/inner v1 source lineage differs")
    acquisition_selection_sha256 = source_v1["acquisition_selection_sha256"]
    if (
        acquisition.get("selection_sha256") != acquisition_selection_sha256
        or private.get("acquisition_selection_sha256") != acquisition_selection_sha256
        or descriptor.get("acquisition_selection_sha256") != acquisition_selection_sha256
        or descriptor.get("source_v1_descriptor_sha256") != source_v1["descriptor_sha256"]
        or acquisition.get("attempted_count") != acquisition_target
        or acquisition.get("reuse_mode") != "authenticated-sealed-v1-acquisition-no-refetch"
    ):
        raise ValueError("owner v2 acquisition lineage does not reproduce")
    for field in ("available_count", "unavailable_count"):
        if type(acquisition.get(field)) is not int or acquisition[field] < 0:
            raise ValueError("owner v2 acquisition counts are malformed")
    if (
        acquisition["available_count"] + acquisition["unavailable_count"] != acquisition_target
        or acquisition["available_count"] < minimum_available
    ):
        raise ValueError("owner v2 acquisition counts do not sum to its target")
    error_counts = acquisition.get("error_counts")
    if (
        not isinstance(error_counts, dict)
        or any(type(value) is not int or value < 0 for value in error_counts.values())
        or sum(error_counts.values()) != acquisition["unavailable_count"]
    ):
        raise ValueError("owner v2 acquisition errors are malformed")
    final_capacity_sha256 = _validate_owner_quota_feasibility(
        final_selection.get("quota_feasibility"),
        label="final",
        expected_count=expected_count,
    )
    if private.get("final_quota_feasibility") != final_selection.get("quota_feasibility"):
        raise ValueError("owner v2 final capacity differs across its seals")
    cosine_filter = private.get("cosine_duplicate_filter")
    nearest = private.get("global_nearest_similarity")
    cosine_count_fields = (
        "input_count",
        "rejected_count",
        "retained_count",
        "radius_query_count",
        "radius_neighbor_edge_count",
        "max_batch_neighbor_count",
    )
    if (
        not isinstance(cosine_filter, dict)
        or set(cosine_filter) != _OWNER_V2_COSINE_KEYS
        or raw_pixel_audit.get("cosine_duplicate_filter") != cosine_filter
        or cosine_filter.get("algorithm") != "exact-normalized-ball-tree-independent-set-v1"
        or cosine_filter.get("threshold") != 0.99
        or cosine_filter.get("comparison") != "strictly_below"
        or any(
            type(cosine_filter.get(field)) is not int or cosine_filter[field] < 0
            for field in cosine_count_fields
        )
        or cosine_filter["rejected_count"] + cosine_filter["retained_count"]
        != cosine_filter["input_count"]
        or cosine_filter["radius_query_count"] != cosine_filter["input_count"]
        or cosine_filter["input_count"] > acquisition["available_count"]
        or cosine_filter["retained_count"] < expected_count
    ):
        raise ValueError("owner v2 cosine-duplicate evidence differs or did not pass")
    if (
        not isinstance(nearest, dict)
        or set(nearest) != _OWNER_V2_NEAREST_KEYS
        or raw_pixel_audit.get("global_nearest_similarity") != nearest
        or final_selection.get("global_nearest_similarity") != nearest
    ):
        raise ValueError("owner v2 global-nearest evidence differs across its seals")
    descriptor_sha256 = private.get("descriptor_sha256")
    if (
        set(raw_pixel_audit) != _OWNER_V2_RAW_PIXEL_KEYS
        or descriptor.get("descriptor_version") != "triage-raw-pixel-v1"
        or raw_pixel_audit.get("descriptor_version") != "triage-raw-pixel-v1"
        or raw_pixel_audit.get("cluster_count") != config.get("cluster_count")
        or not _is_sha256(descriptor_sha256)
        or hashlib.sha256(_OWNER_V2_DESCRIPTOR_DOMAIN + _canonical_bytes(descriptor)).hexdigest()
        != descriptor_sha256
        or raw_pixel_audit.get("descriptor_sha256") != descriptor_sha256
        or private_evidence.get("descriptor_sha256") != descriptor_sha256
    ):
        raise ValueError("owner v2 descriptor seal does not reproduce")
    private_rows = private.get("rows")
    descriptor_rows = descriptor.get("rows")
    if not isinstance(private_rows, list) or len(private_rows) != expected_count:
        raise ValueError("owner v2 private selection is not the exact active count")
    if (
        not isinstance(descriptor_rows, list)
        or len(descriptor_rows) != acquisition["available_count"]
    ):
        raise ValueError("owner v2 descriptor count differs from available pixels")
    if any(
        not isinstance(row, dict) or set(row) != _OWNER_V2_PRIVATE_ROW_KEYS for row in private_rows
    ):
        raise ValueError("owner v2 private selection rows do not match their schema")
    validate_owner_v2_nearest_pair_binding(private)
    if any(not isinstance(row, dict) for row in descriptor_rows):
        raise ValueError("owner v2 descriptor rows are malformed")
    selected_by_id = {
        str(row.get("asset_id", "")): row for row in private_rows if isinstance(row, dict)
    }
    descriptors_by_id = {
        str(row.get("asset_id", "")): row for row in descriptor_rows if isinstance(row, dict)
    }
    indexed_by_id = {row.asset_id: row for row in index.rows}
    if (
        len(selected_by_id) != expected_count
        or len(descriptors_by_id) != len(descriptor_rows)
        or any(
            not asset_id
            or not isinstance(row.get("audit_id"), str)
            or not row["audit_id"]
            or not _is_sha256(row.get("preview_sha256"))
            or not _is_sha256(row.get("vector_sha256"))
            for asset_id, row in descriptors_by_id.items()
        )
        or set(selected_by_id) != set(indexed_by_id)
        or not set(selected_by_id).issubset(descriptors_by_id)
    ):
        raise ValueError("owner v2 private/index/descriptor inventories differ")
    binding_rows: list[dict[str, object]] = []
    for asset_id in sorted(selected_by_id):
        selected = selected_by_id[asset_id]
        indexed = indexed_by_id[asset_id]
        descriptor_row = descriptors_by_id[asset_id]
        if (
            selected.get("component_key"),
            selected.get("moment_key"),
            selected.get("capture_day"),
            selected.get("final_audit_id"),
            selected.get("preview_sha256"),
        ) != (
            indexed.component_key,
            indexed.moment_key,
            indexed.capture_day,
            indexed.audit_id,
            indexed.preview_sha256,
        ):
            raise ValueError("owner v2 private rows differ from final preview index")
        if (
            descriptor_row.get("audit_id"),
            descriptor_row.get("preview_sha256"),
            descriptor_row.get("vector_sha256"),
        ) != (
            selected.get("acquisition_audit_id"),
            selected.get("preview_sha256"),
            selected.get("descriptor_vector_sha256"),
        ):
            raise ValueError("owner v2 selected rows differ from descriptor audit")
        binding_rows.append(
            {
                "asset_id": asset_id,
                "acquisition_audit_id": selected["acquisition_audit_id"],
                "final_audit_id": selected["final_audit_id"],
                "preview_sha256": selected["preview_sha256"],
                "descriptor_vector_sha256": selected["descriptor_vector_sha256"],
            }
        )
    binding_sha256 = hashlib.sha256(
        _OWNER_V2_BINDING_DOMAIN
        + _canonical_bytes(
            {
                "schema": "triage-training-preview-descriptor-binding-v2",
                "descriptor_sha256": descriptor_sha256,
                "rows": binding_rows,
            }
        )
    ).hexdigest()
    if (
        private.get("preview_descriptor_binding_sha256") != binding_sha256
        or private_evidence.get("preview_descriptor_binding_sha256") != binding_sha256
    ):
        raise ValueError("owner v2 preview/descriptor binding does not reproduce")
    fresh_approval = public.get("fresh_truth_approval")
    if (
        not isinstance(fresh_approval, dict)
        or set(fresh_approval) != {"authenticated", "public_sha256", "private_sha256"}
        or fresh_approval.get("authenticated") is not True
        or not _is_sha256(fresh_approval.get("public_sha256"))
        or not _is_sha256(fresh_approval.get("private_sha256"))
    ):
        raise ValueError("owner v2 fresh-truth approval is not authenticated")
    ledger_path = Path(truth_ledger_path)
    if ledger_path.is_symlink() or not ledger_path.is_file() or ledger_path.stat().st_mode & 0o077:
        raise PermissionError("truth ledger must be a private regular local file")
    ledger = TruthReservationLedger(ledger_path)
    ledger_sha256 = ledger.ledger_sha256()
    reservation = ledger.cohort_reservation(ACTIVE_V1_TRUTH_COHORT)
    if (
        public.get("truth_ledger_sha256") != ledger_sha256
        or private.get("truth_ledger_sha256") != ledger_sha256
        or reservation is None
        or reservation.inventory_sha256 != index.inventory_sha256
        or reservation.selected_count != ACTIVE_V1_TRUTH_COUNT
        or len(reservation.rows) != ACTIVE_V1_TRUTH_COUNT
        or any(row.reservation_kind != "mapped" for row in reservation.rows)
        or public.get("fresh_truth_selection_sha256") != reservation.selection_sha256
    ):
        raise ValueError("owner v2 fresh-truth selection/ledger lineage differs")
    blocked = ledger.blocklist()
    if (
        set(indexed_by_id) & set(blocked.asset_ids)
        or {row.component_key for row in index.rows} & set(blocked.component_keys)
        or {row.moment_key for row in index.rows} & set(blocked.moment_keys)
    ):
        raise ValueError("owner v2 cohort overlaps truth by asset, component, or moment")
    try:
        if __package__:
            from .training_cohort_dino_audit import default_dino_audit_root
            from .training_cohort_v2 import load_visual_review_approval
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.training_cohort_dino_audit import (
                default_dino_audit_root,
            )
            from scripts.triage_heads.training_cohort_v2 import (
                load_visual_review_approval,
            )

        visual_approval = load_visual_review_approval(cohort_root)
        dino_audit_root = default_dino_audit_root(cohort_root)
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        raise ValueError(f"owner v2 visual review is not approved: {error}") from error
    if (
        visual_approval.get("cohort_run_sha256") != public_digest
        or visual_approval.get("final_selection_sha256") != index.selection_sha256
        or visual_approval.get("final_preview_manifest_sha256") != index.manifest_sha256
        or visual_approval.get("dino_private_index_sha256") != index.private_index_sha256
    ):
        raise ValueError("owner v2 visual approval differs from training inputs")
    lineage = {
        "mode": "active-v1-owner",
        "owner_cohort_version": "v2",
        "expected_owner_count": expected_count,
        "active_run_sha256": "",
        "active_completion_sha256": "",
        "wal_sha256": "",
        "owner_cohort_index_sha256": index.private_index_sha256,
        "owner_preview_manifest_sha256": index.manifest_sha256,
        "owner_cohort_manifest_file_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "owner_cohort_run_sha256": public_digest,
        "owner_private_selection_file_sha256": hashlib.sha256(private_bytes).hexdigest(),
        "owner_private_selection_sha256": private_digest,
        "owner_descriptor_sha256": descriptor_sha256,
        "owner_descriptor_audit_file_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "owner_preview_descriptor_binding_sha256": binding_sha256,
        "owner_inventory_sha256": index.inventory_sha256,
        "owner_selection_sha256": index.selection_sha256,
        "owner_source_v1": dict(source_v1),
        "owner_acquisition_selection_sha256": acquisition_selection_sha256,
        "owner_final_capacity_evidence_sha256": final_capacity_sha256,
        "owner_visual_approval_sha256": visual_approval["approval_sha256"],
        "owner_visual_review_manifest_sha256": visual_approval["review_manifest_sha256"],
        "owner_global_nearest_audit_sha256": visual_approval["nearest_audit_sha256"],
        "owner_dino_audit_manifest_sha256": visual_approval["dino_audit_manifest_sha256"],
        "owner_dino_nearest_pairs_sha256": visual_approval["dino_nearest_pairs_sha256"],
        "owner_dino_pixel_set_sha256": visual_approval["dino_pixel_set_sha256"],
        "owner_dino_encoder_sha256": visual_approval["dino_encoder_sha256"],
        "owner_dino_vector_set_sha256": visual_approval["dino_vector_set_sha256"],
        "truth_ledger_sha256": ledger_sha256,
        "required_truth_cohort": ACTIVE_V1_TRUTH_COHORT,
        "required_truth_count": ACTIVE_V1_TRUTH_COUNT,
        "fresh_truth_selection_sha256": reservation.selection_sha256,
        "fresh_truth_approval_public_sha256": fresh_approval["public_sha256"],
        "fresh_truth_approval_private_sha256": fresh_approval["private_sha256"],
    }
    source_paths = [
        *preview_source_files,
        expected_manifest,
        private_path,
        descriptor_path,
        cohort_root / "visual-review-approval.json",
        *sorted((cohort_root / "blind-review").iterdir()),
        *sorted(path for path in dino_audit_root.iterdir() if path.is_file()),
    ]
    unique_source_paths = tuple(dict.fromkeys(source_paths))
    source_files = tuple((path, _file_sha256(path)) for path in unique_source_paths)
    return lineage, source_files, ledger_sha256


def load_active_v1_training_evidence(
    *,
    active_run_manifest_path: Path,
    active_completion_path: Path,
    wal_path: Path,
    cohort_index_path: Path,
    cohort_manifest_path: Path,
    truth_ledger_path: Path,
    expected_count: int = ACTIVE_V1_OWNER_COUNT,
    head: HeadSpec = LOCATION,
) -> ActiveV1TrainingEvidence:
    """Authenticate one sealed owner active-v1 source and extract one head's labels."""
    if type(expected_count) is not int or expected_count < 1:
        raise ValueError("active-v1 expected count must be a positive integer")
    index = load_cohort_preview_index(cohort_index_path)
    if index.cohort_name != ACTIVE_V1_COHORT_NAME or len(index.rows) != expected_count:
        raise ValueError("owner preview index is not the exact location-training-v2 cohort")
    lineage, cohort_sources, ledger_sha256 = _validate_owner_cohort_seals(
        index=index,
        cohort_index_path=cohort_index_path,
        cohort_manifest_path=cohort_manifest_path,
        truth_ledger_path=truth_ledger_path,
        expected_count=expected_count,
    )
    run, run_bytes = _load_private_json_object(
        active_run_manifest_path,
        label="active-v1 run manifest",
        require_canonical=False,
    )
    _load_private_json_object(
        active_completion_path,
        label="active-v1 completion seal",
        require_canonical=False,
    )
    wal = Path(wal_path)
    if wal.is_symlink() or not wal.is_file():
        raise ValueError("active-v1 WAL must be a regular local file")
    if wal.stat().st_mode & 0o077:
        raise PermissionError("active-v1 WAL must not be group- or world-accessible")

    endpoint = TeacherEndpoint("http://127.0.0.1", "read-only-completion-verifier")
    expected_provenance = _active_head_provenance(endpoint, prompt=LABEL_PROMPT)
    assets = _active_v1_label_assets(index)
    inventory_sha256 = _multihead_inventory_sha256(assets)
    _validate_exact_active_run(
        run,
        expected_count=expected_count,
        inventory_sha256=inventory_sha256,
        expected_provenance=expected_provenance,
    )
    completion = validate_multihead_completion(
        active_completion_path,
        wal_path=wal,
        assets=assets,
        endpoint=endpoint,
        prompt=LABEL_PROMPT,
    )
    if completion["selected_count"] != expected_count:
        raise ValueError("active-v1 completion count is not the exact owner cohort")

    expected_by_id = {
        asset.asset_id: {
            "group_key": asset.group_key,
            "source_updated": asset.source_updated,
            "preview_sha256": asset.preview_sha256,
        }
        for asset in assets
    }
    successful: dict[str, TrainingExample] = {}
    for row in _wal_rows(wal):
        asset_id = str(row.get("asset_id", ""))
        per_asset = expected_by_id.get(asset_id)
        if row.get("status") != "ok" or per_asset is None or not _valid_active_labels(row):
            continue
        if any(row.get(field) != value for field, value in expected_provenance.items()):
            continue
        if any(row.get(field) != value for field, value in per_asset.items()):
            continue
        successful[asset_id] = TrainingExample(
            asset_id=asset_id,
            label=str(row[head.name]),
            group_key=str(row["group_key"]),
            preview_sha256=str(row["preview_sha256"]),
            source_updated=str(row["source_updated"]),
        )
    if set(successful) != set(expected_by_id):
        raise ValueError("active-v1 WAL extraction does not cover the exact owner cohort")
    wal_sha256 = _file_sha256(wal)
    if wal_sha256 != completion["wal_sha256"]:
        raise ValueError("active-v1 WAL changed while its completion was verified")

    completion_file_sha256 = _file_sha256(active_completion_path)
    run_file_sha256 = hashlib.sha256(run_bytes).hexdigest()
    lineage.update(
        {
            "active_run_sha256": run_file_sha256,
            "active_completion_sha256": completion_file_sha256,
            "wal_sha256": wal_sha256,
            "head_set_version": expected_provenance["head_set_version"],
            "active_heads": list(ACTIVE_HEAD_NAMES),
            "head": head.name,
            "teacher_model": expected_provenance["teacher_model"],
            "teacher_api_model": expected_provenance["teacher_api_model"],
            "temperature": 0.0,
            "prompt_sha256": expected_provenance["prompt_sha256"],
            "schema_sha256": expected_provenance["schema_sha256"],
            "multihead_inventory_sha256": inventory_sha256,
        }
    )
    return ActiveV1TrainingEvidence(
        examples=tuple(successful[asset_id] for asset_id in sorted(successful)),
        cohort_index=index,
        training_input=lineage,
        truth_ledger_sha256=ledger_sha256,
        truth_ledger_path=Path(truth_ledger_path),
        source_files=(
            *cohort_sources,
            (active_run_manifest_path, run_file_sha256),
            (active_completion_path, completion_file_sha256),
            (wal, wal_sha256),
        ),
    )


def validate_active_split_isolation(
    index: CohortPreviewIndex,
    split_by_asset: Mapping[str, str],
) -> None:
    """Fail if a connected owner component or moment crosses an outer split."""
    expected_ids = {row.asset_id for row in index.rows}
    if set(split_by_asset) != expected_ids:
        raise ValueError("active-v1 split does not cover the exact owner cohort")
    for label, key in (
        ("component", lambda row: row.component_key),
        ("moment", lambda row: row.moment_key),
    ):
        splits_by_key: dict[str, set[str]] = defaultdict(set)
        for row in index.rows:
            splits_by_key[key(row)].add(split_by_asset[row.asset_id])
        if any(len(splits) != 1 for splits in splits_by_key.values()):
            raise ValueError(f"active-v1 {label} identity leaks across train/cal/test")


def assert_active_v1_evidence_unchanged(evidence: ActiveV1TrainingEvidence) -> None:
    """Recheck mutable source paths and the append-only ledger before freezing candidates."""
    assert_active_v1_source_files_unchanged(evidence)
    ledger = TruthReservationLedger(evidence.truth_ledger_path)
    if ledger.ledger_sha256() != evidence.truth_ledger_sha256:
        raise RuntimeError("truth ledger changed during active-v1 training")


def assert_active_v1_source_files_unchanged(evidence: ActiveV1TrainingEvidence) -> None:
    """Rehash every file whose digest will be bound into the training report."""
    for path, expected_sha256 in evidence.source_files:
        _require_single_link_regular_file(
            path,
            label="active-v1 source evidence",
            required_mode=0o600,
        )
        if _file_sha256(path) != expected_sha256:
            raise RuntimeError("active-v1 source evidence changed during training")


def validate_active_embed_metadata(
    metadata: Mapping[str, Any],
    *,
    expected_training_count: int,
    expected_encoder_id: str,
    expected_weights_sha256: str,
    expected_preprocess_version: str,
    expected_layout_version: str,
    expected_fresh_certification: Mapping[str, str],
) -> None:
    """Require a complete pinned owner20k + approved-fresh400 embedding run."""
    if metadata.get("schema_version") != "triage-embed-run-v1":
        raise ValueError("embedding metadata has the wrong schema")
    expected = {
        "encoder_id": expected_encoder_id,
        "weights_sha256": expected_weights_sha256,
        "preprocess_version": expected_preprocess_version,
        "layout_version": expected_layout_version,
    }
    if any(metadata.get(field) != value for field, value in expected.items()):
        raise ValueError("embedding metadata does not name the pinned encoder experiment")
    inventory = metadata.get("inventory")
    run = metadata.get("run")
    if not isinstance(inventory, dict) or not isinstance(run, dict):
        raise ValueError("embedding metadata inventory/run sections are malformed")
    if (
        type(inventory.get("training_assets")) is not int
        or inventory["training_assets"] != expected_training_count
    ):
        raise ValueError("embedding run does not bind the exact owner training count")
    if (
        inventory.get("usable_truth_assets") != ACTIVE_V1_TRUTH_COUNT
        or inventory.get("missing_truth_images") != 0
        or inventory.get("excluded_truth_previews") != 0
    ):
        raise ValueError("embedding run does not contain the exact approved fresh400 truth set")
    for field in ("requested", "computed", "cached"):
        if type(run.get(field)) is not int or run[field] < 0:
            raise ValueError("embedding run counts are malformed")
    if run["requested"] != expected_training_count + ACTIVE_V1_TRUTH_COUNT or (
        run["computed"] + run["cached"] != run["requested"]
    ):
        raise ValueError("embedding run did not complete its requested inventory")
    expected_lineage_keys = {
        "inventory_sha256",
        "owner_cohort_run_sha256",
        "fresh_inventory_sha256",
        "fresh_truth_selection_sha256",
        "fresh_truth_approval_private_sha256",
        "fresh_truth_approval_public_sha256",
    }
    if set(expected_fresh_certification) != expected_lineage_keys or any(
        not _is_sha256(value) for value in expected_fresh_certification.values()
    ):
        raise ValueError("expected fresh400 lineage is malformed")
    fresh = metadata.get("fresh_certification")
    expected_fresh_keys = expected_lineage_keys | {
        "certification_index_sha256",
        "image_set_sha256",
        "pixel_snapshot_sha256",
        "selected_count",
    }
    if not isinstance(fresh, dict) or set(fresh) != expected_fresh_keys:
        raise ValueError("embedding metadata fresh400 lineage is malformed")
    if fresh.get("selected_count") != ACTIVE_V1_TRUTH_COUNT or any(
        fresh.get(field) != expected for field, expected in expected_fresh_certification.items()
    ):
        raise ValueError("embedding metadata fresh400 lineage differs from owner training")
    if any(not _is_sha256(fresh.get(field)) for field in expected_fresh_keys - {"selected_count"}):
        raise ValueError("embedding metadata fresh400 lineage contains an invalid digest")
    try:
        limit_gib = validate_offline_working_set_gib(float(metadata["max_process_working_set_gib"]))
        peak_rss = int(metadata["peak_process_rss_bytes"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("embedding run has no valid hard memory provenance") from error
    if peak_rss < 0 or peak_rss >= int(limit_gib * 1024**3):
        raise ValueError("embedding run reached or exceeded its configured memory ceiling")


def _read_never_train(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                ids.add(str(json.loads(line)["image_id"]))
    return ids


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--cache-db", type=Path, default=None)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--label-run", type=Path, default=None)
    parser.add_argument(
        "--active-v1-wal",
        type=Path,
        default=None,
        help="sealed multihead owner-label WAL (location is extracted in memory)",
    )
    parser.add_argument("--active-v1-run-manifest", type=Path, default=None)
    parser.add_argument("--active-v1-completion", type=Path, default=None)
    parser.add_argument("--owner-cohort-index", type=Path, default=None)
    parser.add_argument("--owner-cohort-manifest", type=Path, default=None)
    parser.add_argument("--truth-ledger", type=Path, default=None)
    parser.add_argument("--truth-jsonl", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--embed-meta", type=Path, default=None)
    parser.add_argument("--mlp-epochs", type=int, default=100)
    parser.add_argument(
        "--head",
        choices=ACTIVE_HEAD_NAMES,
        default=LOCATION.name,
        help="which active-v1 head to train; the legacy location WAL path is location-only",
    )
    parser.add_argument(
        "--max-working-set-gib",
        type=float,
        default=DEFAULT_OFFLINE_WORKING_SET_GIB,
        help="training-process RSS ceiling (hard maximum: 8 GiB)",
    )
    args = parser.parse_args(argv)
    if args.mlp_epochs < 1:
        parser.error("--mlp-epochs must be positive")
    try:
        validate_offline_working_set_gib(args.max_working_set_gib)
    except ValueError as error:
        parser.error(str(error))
    active_paths = (
        args.active_v1_wal,
        args.active_v1_run_manifest,
        args.active_v1_completion,
        args.owner_cohort_index,
        args.owner_cohort_manifest,
        args.truth_ledger,
    )
    if any(path is not None for path in active_paths) and not all(
        path is not None for path in active_paths
    ):
        parser.error("all six active-v1 owner evidence paths are required together")
    if all(path is not None for path in active_paths) and (
        args.labels is not None or args.label_run is not None
    ):
        parser.error("legacy --labels/--label-run cannot be mixed with active-v1 evidence")
    args.active_v1 = all(path is not None for path in active_paths)
    return args


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(
    path: Path,
    examples: list[TrainingExample],
    split_by_asset: dict[str, str],
    *,
    head: HeadSpec,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for example in examples:
                handle.write(
                    json.dumps(
                        {
                            "asset_id": example.asset_id,
                            head.name: example.label,
                            "group_key": example.group_key,
                            "preview_sha256": example.preview_sha256,
                            "source_updated": example.source_updated,
                            "split": split_by_asset[example.asset_id],
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    head = resolve_head_specs([args.head])[0]
    if head is not LOCATION and not args.active_v1:
        raise SystemExit(f"STOP: the {head.name} head only trains from active-v1 evidence")

    def memory_guard() -> int:
        return ensure_offline_process_memory(max_working_set_gib=args.max_working_set_gib)

    memory_guard()
    cache_path = args.cache_db or args.artifact_dir / "embeddings.db"
    ensure_private_directory(args.artifact_dir)
    try:
        _artifact_lock = _acquire_artifact_lock(
            args.artifact_dir,
            cache_db=cache_path,
            shared_root=DEFAULT_ARTIFACT_DIR,
        )
    except RuntimeError as error:
        raise SystemExit(f"STOP: {error}") from error
    embed_meta_path = args.embed_meta or args.artifact_dir / "embed-meta-cpu.json"
    if not cache_path.is_file() or not embed_meta_path.is_file():
        raise SystemExit("run the complete label and embedding stages before training")
    if not args.active_v1:
        labels_path = args.labels or args.artifact_dir / "location-labels.jsonl"
        label_run_path = args.label_run or args.artifact_dir / "label-run.json"
        if not labels_path.is_file() or not label_run_path.is_file():
            raise SystemExit("run the complete label and embedding stages before training")
    training_staging = new_training_bundle_staging(args.artifact_dir, head=head)
    manifest_path = training_staging.component_paths["training_manifest"]
    pca_path = training_staging.component_paths["pca_artifact"]
    linear_candidate_path = training_staging.component_paths["linear_candidate"]
    mlp_candidate_path = training_staging.component_paths["mlp_candidate"]

    # Cap native numerical libraries even if the caller forgot the shell vars.
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "4")

    if __package__:
        from .embed import (
            EXPECTED_ONNX_SHA256,
            LAYOUT_VERSION,
            MODEL_ID,
            MODEL_REVISION,
            PREPROCESS_VERSION,
            EmbeddingCache,
            EncoderSpec,
        )
        from .provenance import label_inventory_sha256
    else:  # pragma: no cover - direct script invocation
        from scripts.triage_heads.embed import (
            EXPECTED_ONNX_SHA256,
            LAYOUT_VERSION,
            MODEL_ID,
            MODEL_REVISION,
            PREPROCESS_VERSION,
            EmbeddingCache,
            EncoderSpec,
        )
        from scripts.triage_heads.provenance import label_inventory_sha256

    active_evidence: ActiveV1TrainingEvidence | None = None
    if args.active_v1:
        try:
            active_evidence = load_active_v1_training_evidence(
                active_run_manifest_path=args.active_v1_run_manifest,
                active_completion_path=args.active_v1_completion,
                wal_path=args.active_v1_wal,
                cohort_index_path=args.owner_cohort_index,
                cohort_manifest_path=args.owner_cohort_manifest,
                truth_ledger_path=args.truth_ledger,
                head=head,
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as error:
            raise SystemExit(f"STOP: {error}") from error
        examples = list(active_evidence.examples)
        expected_provenance = {
            field: active_evidence.training_input[field]
            for field in (
                "head_set_version",
                "active_heads",
                "teacher_model",
                "teacher_api_model",
                "temperature",
                "prompt_sha256",
                "schema_sha256",
            )
        }
    else:
        label_run = json.loads(label_run_path.read_text(encoding="utf-8"))
        try:
            expected_provenance = label_provenance_from_manifest(label_run)
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
        examples = load_training_examples(
            labels_path,
            expected_provenance=expected_provenance,
        )
        inventory_sha256 = label_inventory_sha256(
            (example.asset_id, example.group_key, example.source_updated) for example in examples
        )
        try:
            validate_label_completion(
                label_run,
                successful_count=len(examples),
                inventory_sha256=inventory_sha256,
            )
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
    memory_guard()
    validate_never_train(examples, _read_never_train(args.truth_jsonl))
    if len(examples) < 500:
        raise SystemExit(f"STOP: only {len(examples)} successful training labels")
    counts = Counter(example.label for example in examples)
    missing_classes = set(head.classes) - set(counts)
    if missing_classes:
        raise SystemExit(f"STOP: training labels omit {len(missing_classes)} {head.name} classes")

    merged_groups = (
        connected_split_groups(active_evidence.cohort_index.rows)
        if active_evidence is not None
        else None
    )
    split_by_asset = assign_group_splits(examples, seed=42, merged_groups=merged_groups)
    split_counts = verify_no_group_leakage(examples, split_by_asset)
    try:
        verify_split_class_coverage(examples, split_by_asset, head=head)
        if active_evidence is not None:
            validate_active_split_isolation(active_evidence.cohort_index, split_by_asset)
    except ValueError as error:
        raise SystemExit(f"STOP: {error}") from error
    memory_guard()
    metadata = json.loads(embed_meta_path.read_text(encoding="utf-8"))
    embed_meta_sha256 = _file_sha256(embed_meta_path)
    if active_evidence is not None:
        try:
            active_training_input = active_evidence.training_input
            owner_inventory_sha256 = str(active_training_input["owner_inventory_sha256"])
            validate_active_embed_metadata(
                metadata,
                expected_training_count=len(examples),
                expected_encoder_id=f"{MODEL_ID}@{MODEL_REVISION}",
                expected_weights_sha256=EXPECTED_ONNX_SHA256,
                expected_preprocess_version=PREPROCESS_VERSION,
                expected_layout_version=LAYOUT_VERSION,
                expected_fresh_certification={
                    "inventory_sha256": owner_inventory_sha256,
                    "owner_cohort_run_sha256": str(
                        active_training_input["owner_cohort_run_sha256"]
                    ),
                    "fresh_inventory_sha256": owner_inventory_sha256,
                    "fresh_truth_selection_sha256": str(
                        active_training_input["fresh_truth_selection_sha256"]
                    ),
                    "fresh_truth_approval_private_sha256": str(
                        active_training_input["fresh_truth_approval_private_sha256"]
                    ),
                    "fresh_truth_approval_public_sha256": str(
                        active_training_input["fresh_truth_approval_public_sha256"]
                    ),
                },
            )
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
    staging_encoder_key = str(metadata["encoder_key"])
    source_spec = EncoderSpec(
        encoder_id=str(metadata["encoder_id"]),
        weights_sha256=str(metadata["weights_sha256"]),
        preprocess_version=str(metadata["preprocess_version"]),
        layout_version=str(metadata["layout_version"]),
    )
    if (
        staging_encoder_key != source_spec.key
        or metadata.get("staging_encoder_key") != source_spec.key
    ):
        raise SystemExit("STOP: embedding metadata encoder key does not reproduce")
    cache = EmbeddingCache(cache_path)
    staging_snapshot = cache.staging_snapshot(
        [example.asset_id for example in examples], staging_encoder_key
    )
    staging_provenance = {
        asset_id: (preview_sha256, source_updated)
        for asset_id, (_pack, preview_sha256, source_updated) in staging_snapshot.items()
    }
    try:
        validate_label_embedding_lineage(examples, staging_provenance)
    except ValueError as error:
        raise SystemExit(f"STOP: {error}") from error
    packs = {asset_id: row[0] for asset_id, row in staging_snapshot.items()}
    if len(packs) != len(examples):
        raise SystemExit(f"STOP: cache has {len(packs)} of {len(examples)} labeled staging packs")
    memory_guard()
    pack_snapshot_sha256 = training_pack_snapshot_sha256(examples, split_by_asset, packs, head=head)
    _write_manifest(manifest_path, examples, split_by_asset, head=head)
    if active_evidence is not None:
        try:
            assert_active_v1_evidence_unchanged(active_evidence)
        except RuntimeError as error:
            raise SystemExit(f"STOP: {error}") from error
    memory_guard()

    train_examples = [
        example for example in examples if split_by_asset[example.asset_id] == "train"
    ]
    train_packs = np.stack([packs[example.asset_id] for example in train_examples])
    print(
        f"PCA: {train_packs.shape[0]} train-only packs, "
        f"working matrix={train_packs.nbytes / 1024**2:.1f} MiB",
        flush=True,
    )
    pca = fit_pca(train_packs, components=256, memory_guard=memory_guard)
    save_pca_artifact(pca_path, pca)
    memory_guard()
    pca_digest = pca_sha256(pca)
    projected_spec = EncoderSpec(
        encoder_id=str(metadata["encoder_id"]),
        weights_sha256=str(metadata["weights_sha256"]),
        preprocess_version=str(metadata["preprocess_version"]),
        layout_version=f"{metadata['layout_version']}@pca-sha256:{pca_digest}",
    )
    cache.register_encoder(projected_spec)
    projected_encoder_key = projected_spec.key
    del train_packs

    staging_ids = cache.all_staging_ids(staging_encoder_key)
    for start in range(0, len(staging_ids), 500):
        memory_guard()
        chunk = staging_ids[start : start + 500]
        chunk_packs = cache.staging_packs(chunk, staging_encoder_key)
        matrix = np.stack([chunk_packs[asset_id] for asset_id in chunk])
        projected = project_deployment_features(pca, matrix)
        cache.remember_vectors(
            {asset_id: projected[index] for index, asset_id in enumerate(chunk)},
            projected_encoder_key,
            provenance_encoder_key=staging_encoder_key,
        )
        memory_guard()
    feature_matrix = project_deployment_features(
        pca,
        np.stack([packs[example.asset_id] for example in examples]),
    )
    memory_guard()
    labels = np.asarray([example.label for example in examples])
    groups = np.asarray([example.group_key for example in examples])
    split_array = np.asarray([split_by_asset[example.asset_id] for example in examples])
    train_mask = split_array == "train"
    test_mask = split_array == "test"

    print("training grouped-CV linear candidate", flush=True)
    linear, cv_scores = fit_linear_candidate(
        feature_matrix[train_mask],
        labels[train_mask],
        groups[train_mask],
        memory_guard=memory_guard,
    )
    save_linear_artifact(linear_candidate_path, linear)
    memory_guard()
    print("training shallow MLP candidate on CPU", flush=True)
    mlp = fit_mlp_candidate(
        feature_matrix[train_mask],
        labels[train_mask],
        hidden_dim=256,
        epochs=args.mlp_epochs,
        batch_size=256,
        memory_guard=memory_guard,
    )
    save_mlp_artifact(mlp_candidate_path, mlp)
    peak_rss_bytes = memory_guard()

    report = {
        "schema_version": f"triage-{head.name}-train-v1",
        "head": head.name,
        "classes": list(head.classes),
        "encoder_key": projected_encoder_key,
        "staging_encoder_key": staging_encoder_key,
        "label_count": len(examples),
        "label_counts": dict(sorted(counts.items())),
        "label_source": f"{expected_provenance['teacher_model']}; local omlx; temperature 0.0",
        "label_provenance": expected_provenance,
        "training_pack_snapshot_sha256": pack_snapshot_sha256,
        "label_cost_usd": 0.0,
        "max_process_working_set_gib": args.max_working_set_gib,
        "peak_process_rss_bytes": peak_rss_bytes,
        "split_counts": split_counts,
        "pca": {
            "fit_split": "train",
            "input_dimensions": int(pca.components.shape[1]),
            "output_dimensions": int(pca.components.shape[0]),
            "explained_variance_sum": float(pca.explained_variance.sum()),
            "sha256": pca_digest,
            "layout_version": projected_spec.layout_version,
        },
        "linear": {
            "chosen_c": linear.c,
            "cv_accuracy_by_c": {str(c): score for c, score in cv_scores.items()},
            "untempered_test_accuracy": float(
                np.mean(linear.predict(feature_matrix[test_mask]) == labels[test_mask])
            ),
        },
        "mlp": {
            "epochs": args.mlp_epochs,
            "untempered_test_accuracy": float(
                np.mean(mlp.predict(feature_matrix[test_mask]) == labels[test_mask])
            ),
        },
    }
    if active_evidence is not None:
        report["training_input_sources"] = [
            {
                **active_evidence.training_input,
                "embed_metadata_sha256": embed_meta_sha256,
                "training_pack_snapshot_sha256": pack_snapshot_sha256,
            }
        ]
    report = seal_training_report(
        report,
        component_paths={
            "training_manifest": manifest_path,
            "pca_artifact": pca_path,
            "linear_candidate": linear_candidate_path,
            "mlp_candidate": mlp_candidate_path,
        },
    )
    if active_evidence is None:
        try:
            commit_training_bundle(
                args.artifact_dir,
                training_staging,
                report,
                pre_publish=memory_guard,
            )
        except (RuntimeError, ValueError) as error:
            raise SystemExit(f"STOP: {error}") from error
    else:
        ledger = TruthReservationLedger(active_evidence.truth_ledger_path)

        def pre_publish() -> None:
            assert_active_v1_source_files_unchanged(active_evidence)
            if _file_sha256(embed_meta_path) != embed_meta_sha256:
                raise RuntimeError("embedding metadata changed during active-v1 training")
            memory_guard()

        def commit_guard(commit: Callable[[], None]) -> None:
            ledger.commit_if_unchanged(
                expected_ledger_sha256=active_evidence.truth_ledger_sha256,
                commit=commit,
            )

        try:
            commit_training_bundle(
                args.artifact_dir,
                training_staging,
                report,
                pre_publish=pre_publish,
                commit_guard=commit_guard,
            )
        except (RuntimeError, ValueError) as error:
            raise SystemExit(f"STOP: {error}") from error
    print(
        f"training complete: labels={len(examples)} splits={split_counts}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
