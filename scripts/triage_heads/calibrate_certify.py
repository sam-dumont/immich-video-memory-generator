#!/usr/bin/env python3
"""Calibrate abstention, select the candidate head, and certify on verified truth."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

if __package__:
    from .memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        ensure_offline_process_memory,
        validate_offline_working_set_gib,
    )
else:  # pragma: no cover - direct script invocation
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.triage_heads.memory import (
        DEFAULT_OFFLINE_WORKING_SET_GIB,
        ensure_offline_process_memory,
        validate_offline_working_set_gib,
    )

DECIDABLE_CLASSES = ("indoor", "outdoor")
UNDETERMINED = "undetermined"
DEFAULT_ARTIFACT_DIR = Path.home() / ".immich-memories-matrix" / "triage-heads"
DEFAULT_TRUTH_DIR = Path.home() / ".immich-memories-matrix" / "description-truth-2026-08-31"
FRESH_APPROVAL_RELATIVE = Path("recovery-1b") / "fresh-location-cert-v3-approval"
ACCURACY_TARGET = 0.97
COVERAGE_TARGET = 0.85


@dataclass(frozen=True)
class OperatingPoint:
    confidence_thresholds: dict[str, float]
    undetermined_threshold: float


@dataclass(frozen=True)
class Decisions:
    labels: np.ndarray
    confidence: np.ndarray
    covered: np.ndarray


@dataclass(frozen=True)
class CertificationRow:
    asset_id: str
    image_path: Path
    location: str
    teacher_location: str = ""


@dataclass(frozen=True, slots=True)
class FreshCertificationRow:
    asset_id: str
    final_audit_id: str
    image_path: Path
    preview_sha256: str
    source_updated: str
    capture_day: str
    component_key: str
    moment_key: str


@dataclass(frozen=True, slots=True)
class FreshCertificationSnapshot:
    rows: tuple[FreshCertificationRow, ...]
    pixel_bytes: dict[str, bytes]
    selection_sha256: str
    inventory_sha256: str
    certification_index_sha256: str
    approval_private_sha256: str
    approval_public_sha256: str
    image_set_sha256: str
    pixel_snapshot_sha256: str


def validate_certification_disjoint(training_ids: list[str], certification_ids: list[str]) -> None:
    overlap_count = len(set(training_ids) & set(certification_ids))
    if overlap_count:
        raise ValueError(f"{overlap_count} verified-truth assets overlap the training manifest")


def load_truth_asset_ids(path: Path) -> list[str]:
    """Load the complete eval-only set, including rows whose image is currently missing."""
    return load_truth_asset_ids_bytes(path.read_bytes())


def load_truth_asset_ids_bytes(payload: bytes) -> list[str]:
    """Parse a snapshotted complete eval-only inventory."""
    ids: list[str] = []
    for line in payload.decode("utf-8").splitlines():
        if line.strip():
            ids.append(str(json.loads(line)["image_id"]))
    if len(set(ids)) != len(ids):
        raise ValueError("verified truth contains duplicate assets")
    if not ids:
        raise ValueError("verified truth is empty")
    return ids


def decide_probabilities(
    probabilities: np.ndarray,
    classes: tuple[str, ...],
    operating_point: OperatingPoint,
) -> Decisions:
    array = np.asarray(probabilities, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != len(classes):
        raise ValueError("probability matrix does not match classes")
    if set(classes) != {*DECIDABLE_CLASSES, UNDETERMINED}:
        raise ValueError(f"unexpected location classes: {classes}")
    index = {label: position for position, label in enumerate(classes)}
    decidable_indices = np.asarray([index[label] for label in DECIDABLE_CLASSES])
    winning_local = np.argmax(array[:, decidable_indices], axis=1)
    winning_indices = decidable_indices[winning_local]
    winning_labels = np.asarray(classes)[winning_indices]
    confidence = array[np.arange(len(array)), winning_indices]
    thresholds = np.asarray(
        [operating_point.confidence_thresholds[str(label)] for label in winning_labels],
        dtype=np.float32,
    )
    covered = (confidence >= thresholds) & (
        array[:, index[UNDETERMINED]] < operating_point.undetermined_threshold
    )
    emitted = np.where(covered, winning_labels, UNDETERMINED)
    return Decisions(
        labels=np.asarray(emitted, dtype=str),
        confidence=np.asarray(confidence, dtype=np.float32),
        covered=np.asarray(covered, dtype=bool),
    )


def score_decisions(truth: np.ndarray, decisions: Decisions) -> dict[str, Any]:
    expected = np.asarray(truth, dtype=str)
    if len(expected) != len(decisions.labels):
        raise ValueError("truth and decisions must have matching rows")
    n_covered = int(decisions.covered.sum())
    accuracy = (
        float(np.mean(decisions.labels[decisions.covered] == expected[decisions.covered]))
        if n_covered
        else None
    )
    return {
        "n_total": int(len(expected)),
        "n_covered": n_covered,
        "accuracy_on_covered": accuracy,
        "coverage": n_covered / len(expected) if len(expected) else 0.0,
        "emitted_undetermined_rate": float(np.mean(decisions.labels == UNDETERMINED))
        if len(expected)
        else 0.0,
        "truth_undetermined_rate": float(np.mean(expected == UNDETERMINED))
        if len(expected)
        else 0.0,
    }


def softmax_temperature(logits: np.ndarray, temperature: float) -> np.ndarray:
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError("temperature must be finite and positive")
    scaled = np.asarray(logits, dtype=np.float64) / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return (exponentiated / exponentiated.sum(axis=1, keepdims=True)).astype(np.float32)


def fit_temperature(
    logits: np.ndarray,
    truth: np.ndarray,
    classes: tuple[str, ...],
) -> float:
    from scipy.optimize import minimize_scalar

    array = np.asarray(logits, dtype=np.float64)
    expected = np.asarray(truth, dtype=str)
    if array.ndim != 2 or array.shape != (len(expected), len(classes)):
        raise ValueError("logits, truth, and classes do not align")
    class_index = {label: index for index, label in enumerate(classes)}
    try:
        targets = np.asarray([class_index[label] for label in expected], dtype=np.int64)
    except KeyError as error:
        raise ValueError(f"unknown calibration label: {error.args[0]}") from error

    def objective(log_temperature: float) -> float:
        probabilities = softmax_temperature(array, float(np.exp(log_temperature)))
        selected = probabilities[np.arange(len(targets)), targets]
        return float(-np.mean(np.log(np.clip(selected, 1e-12, 1.0))))

    result = minimize_scalar(objective, bounds=(-4.0, 4.0), method="bounded")
    if not result.success:
        raise RuntimeError(f"temperature scaling failed: {result.message}")
    return float(np.exp(result.x))


def _class_threshold_options(
    confidence: np.ndarray,
    correct: np.ndarray,
    eligible: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return every distinct prefix induced by an attainable confidence threshold."""
    values = np.asarray(confidence[eligible], dtype=np.float64)
    correctness = np.asarray(correct[eligible], dtype=np.int64)
    if not len(values):
        return (
            np.array([2.0], dtype=np.float64),
            np.array([0], dtype=np.int64),
            np.array([0], dtype=np.int64),
        )
    order = np.argsort(-values, kind="stable")
    values = values[order]
    correctness = correctness[order]
    group_ends = np.flatnonzero(np.r_[values[1:] != values[:-1], True])
    return (
        np.r_[2.0, values[group_ends]],
        np.r_[0, group_ends + 1].astype(np.int64),
        np.r_[0, np.cumsum(correctness)[group_ends]].astype(np.int64),
    )


def _operating_point_from_grid(
    flat_index: int,
    *,
    covered_grid: np.ndarray,
    correct_grid: np.ndarray,
    indoor_thresholds: np.ndarray,
    outdoor_thresholds: np.ndarray,
    undetermined_threshold: float,
    total: int,
) -> dict[str, Any]:
    indoor_index, outdoor_index = np.unravel_index(flat_index, covered_grid.shape)
    n_covered = int(covered_grid[indoor_index, outdoor_index])
    n_correct = int(correct_grid[indoor_index, outdoor_index])
    return {
        "confidence_thresholds": {
            "indoor": float(indoor_thresholds[indoor_index]),
            "outdoor": float(outdoor_thresholds[outdoor_index]),
        },
        "undetermined_threshold": float(undetermined_threshold),
        "n_covered": n_covered,
        "coverage": n_covered / total,
        "accuracy_on_covered": n_correct / n_covered,
    }


def calibrate_operating_point(
    probabilities: np.ndarray,
    truth: np.ndarray,
    classes: tuple[str, ...],
    *,
    target_accuracy: float,
) -> tuple[OperatingPoint, list[dict[str, Any]]]:
    """Maximize calibration coverage subject to accuracy-on-covered target."""
    array = np.asarray(probabilities, dtype=np.float64)
    expected = np.asarray(truth, dtype=str)
    if array.ndim != 2 or array.shape != (len(expected), len(classes)):
        raise ValueError("probabilities, truth, and classes do not align")
    if not 0 < target_accuracy <= 1:
        raise ValueError("target_accuracy must be in (0, 1]")
    index = {label: position for position, label in enumerate(classes)}
    if set(index) != {*DECIDABLE_CLASSES, UNDETERMINED}:
        raise ValueError(f"unexpected location classes: {classes}")

    decidable_indices = np.asarray([index[label] for label in DECIDABLE_CLASSES])
    winning_local = np.argmax(array[:, decidable_indices], axis=1)
    winning_labels = np.asarray(DECIDABLE_CLASSES)[winning_local]
    winning_indices = decidable_indices[winning_local]
    confidence = array[np.arange(len(array)), winning_indices]
    p_undetermined = array[:, index[UNDETERMINED]]

    # ``<`` changes only immediately after an observed p(undetermined), so these
    # are all attainable undetermined gates (plus the cover-none point).
    und_candidates = np.r_[
        0.0,
        np.nextafter(
            np.unique(p_undetermined.astype(np.float32)),
            np.float32(np.inf),
        ).astype(np.float64),
    ]

    best_eligible: dict[str, Any] | None = None
    best_fallback: dict[str, Any] | None = None
    best_correct_by_covered = np.full(len(expected) + 1, -1, dtype=np.int64)
    for und_threshold in und_candidates:
        und_ok = p_undetermined < und_threshold
        per_class: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for label in DECIDABLE_CLASSES:
            class_mask = winning_labels == label
            per_class[label] = _class_threshold_options(
                confidence,
                expected == label,
                class_mask & und_ok,
            )

        indoor_thresholds, indoor_counts, indoor_correct = per_class["indoor"]
        outdoor_thresholds, outdoor_counts, outdoor_correct = per_class["outdoor"]
        covered_grid = indoor_counts[:, None] + outdoor_counts[None, :]
        correct_grid = indoor_correct[:, None] + outdoor_correct[None, :]
        np.maximum.at(
            best_correct_by_covered,
            covered_grid.ravel(),
            correct_grid.ravel(),
        )

        valid = covered_grid > 0
        eligible = valid & (correct_grid >= target_accuracy * covered_grid - 1e-12)
        if np.any(eligible):
            eligible_covered = np.where(eligible, covered_grid, -1)
            max_covered = int(eligible_covered.max())
            eligible_correct = np.where(eligible & (covered_grid == max_covered), correct_grid, -1)
            point = _operating_point_from_grid(
                int(np.argmax(eligible_correct)),
                covered_grid=covered_grid,
                correct_grid=correct_grid,
                indoor_thresholds=indoor_thresholds,
                outdoor_thresholds=outdoor_thresholds,
                undetermined_threshold=float(und_threshold),
                total=len(expected),
            )
            if best_eligible is None or (point["n_covered"], point["accuracy_on_covered"]) > (
                best_eligible["n_covered"],
                best_eligible["accuracy_on_covered"],
            ):
                best_eligible = point

        if not np.any(valid):
            continue
        accuracy_grid = np.divide(
            correct_grid,
            covered_grid,
            out=np.full(correct_grid.shape, -1.0, dtype=np.float64),
            where=valid,
        )
        best_accuracy = float(accuracy_grid.max())
        fallback_covered = np.where(
            valid & np.isclose(accuracy_grid, best_accuracy), covered_grid, -1
        )
        point = _operating_point_from_grid(
            int(np.argmax(fallback_covered)),
            covered_grid=covered_grid,
            correct_grid=correct_grid,
            indoor_thresholds=indoor_thresholds,
            outdoor_thresholds=outdoor_thresholds,
            undetermined_threshold=float(und_threshold),
            total=len(expected),
        )
        if best_fallback is None or (point["accuracy_on_covered"], point["n_covered"]) > (
            best_fallback["accuracy_on_covered"],
            best_fallback["n_covered"],
        ):
            best_fallback = point

    selected = best_eligible or best_fallback
    if selected is None:
        raise ValueError("calibration set produced no covered operating point")
    points = [
        {
            "n_covered": count,
            "coverage": count / len(expected),
            "accuracy_on_covered": int(best_correct_by_covered[count]) / count,
        }
        for count in range(1, len(best_correct_by_covered))
        if best_correct_by_covered[count] >= 0
    ]
    return (
        OperatingPoint(
            confidence_thresholds=dict(selected["confidence_thresholds"]),
            undetermined_threshold=float(selected["undetermined_threshold"]),
        ),
        points,
    )


def load_certification_rows(
    battery_key_path: Path,
    shard_dir: Path,
    review_data_path: Path,
    *,
    minimum_usable: int = 250,
) -> tuple[list[CertificationRow], int]:
    shard_payloads = {
        path.name: path.read_bytes() for path in sorted(shard_dir.glob("bshard_*.jsonl"))
    }
    return load_certification_rows_bytes(
        battery_key_path.read_bytes(),
        shard_payloads,
        review_data_path.read_bytes(),
        minimum_usable=minimum_usable,
    )


def load_certification_rows_bytes(
    battery_key_payload: bytes,
    shard_payloads: dict[str, bytes],
    review_data_payload: bytes,
    *,
    minimum_usable: int = 250,
) -> tuple[list[CertificationRow], int]:
    """Join certification rows from one immutable input snapshot."""
    review_payload = json.loads(review_data_payload)
    if not isinstance(review_payload, list):
        raise ValueError("unexpected certification input schema")
    review_by_asset = {str(row["image_id"]): Path(row["image"]) for row in review_payload}
    truth_by_asset, teacher_by_asset = load_certification_label_maps_bytes(
        battery_key_payload,
        shard_payloads,
    )
    usable: list[CertificationRow] = []
    missing = 0
    for asset_id, location in truth_by_asset.items():
        image_path = review_by_asset.get(asset_id)
        if image_path is None:
            raise ValueError("truth asset is absent from review_data")
        if not image_path.is_file():
            missing += 1
            continue
        usable.append(
            CertificationRow(
                asset_id,
                image_path,
                location,
                teacher_by_asset[asset_id],
            )
        )
    if len(usable) < minimum_usable:
        raise RuntimeError(
            f"STOP: only {len(usable)} usable certification labels; need {minimum_usable}"
        )
    return usable, missing


def load_certification_label_maps_bytes(
    battery_key_payload: bytes,
    shard_payloads: Mapping[str, bytes],
) -> tuple[dict[str, str], dict[str, str]]:
    """Authenticate paired truth/teacher labels independently of mutable image paths."""
    battery_key = json.loads(battery_key_payload)
    if not isinstance(battery_key, dict):
        raise ValueError("unexpected certification battery-key schema")
    truth_rids = {
        str(rid)
        for rid, entry in battery_key.items()
        if isinstance(entry, dict) and entry.get("source") == "truth"
    }
    seen_truth_rids: set[str] = set()
    truth_by_asset: dict[str, str] = {}
    teacher_by_asset: dict[str, str] = {}
    for _shard_name, payload in sorted(shard_payloads.items()):
        for line in payload.decode("utf-8").splitlines():
            if not line.strip():
                continue
            shard_row = json.loads(line)
            rid = str(shard_row["rid"])
            key_row = battery_key.get(rid)
            if not isinstance(key_row, dict):
                raise ValueError("battery shard contains a rid absent from battery_key")
            source = str(key_row.get("source", ""))
            if source not in {"truth", "teacher"}:
                continue
            asset_id = str(key_row["asset"])
            location = str(shard_row["location"])
            if location not in {*DECIDABLE_CLASSES, UNDETERMINED}:
                raise ValueError(f"invalid certification location class: {location!r}")
            target = truth_by_asset if source == "truth" else teacher_by_asset
            if asset_id in target:
                raise ValueError(f"duplicate {source} location for one certification asset")
            target[asset_id] = location
            if source == "truth":
                if rid in seen_truth_rids:
                    raise ValueError("duplicate truth rid in battery shards")
                seen_truth_rids.add(rid)
    if seen_truth_rids != truth_rids:
        raise ValueError(
            f"certification shards cover {len(seen_truth_rids)} of {len(truth_rids)} truth labels"
        )
    missing_teacher = set(truth_by_asset) - set(teacher_by_asset)
    if missing_teacher:
        raise ValueError(
            f"{len(missing_teacher)} certification assets lack a paired teacher location"
        )
    return truth_by_asset, teacher_by_asset


def paired_accuracy_delta(
    truth: np.ndarray,
    linear_decisions: Decisions,
    mlp_decisions: Decisions,
) -> float | None:
    expected = np.asarray(truth, dtype=str)
    paired = linear_decisions.covered & mlp_decisions.covered
    if len(expected) != len(paired) or not np.any(paired):
        return None
    linear_correct = linear_decisions.labels[paired] == expected[paired]
    mlp_correct = mlp_decisions.labels[paired] == expected[paired]
    net_wins = int(np.count_nonzero(mlp_correct)) - int(np.count_nonzero(linear_correct))
    return net_wins / int(np.count_nonzero(paired))


def select_candidate(
    linear_metrics: dict[str, Any],
    mlp_metrics: dict[str, Any],
    *,
    paired_delta: float | None,
) -> str:
    linear_accuracy = linear_metrics.get("accuracy_on_covered")
    mlp_accuracy = mlp_metrics.get("accuracy_on_covered")
    if linear_accuracy is None or mlp_accuracy is None or paired_delta is None:
        return "linear"
    mlp_coverage = float(mlp_metrics.get("coverage", 0.0))
    aggregate_delta = float(mlp_accuracy) - float(linear_accuracy)
    wins = (
        mlp_coverage >= COVERAGE_TARGET
        and aggregate_delta >= 0.02 - 1e-12
        and paired_delta >= 0.02 - 1e-12
        and bool(mlp_metrics.get("abstention_honesty_passed", True))
    )
    return "mlp" if wins else "linear"


def expected_calibration_error(
    truth: np.ndarray,
    decisions: Decisions,
    *,
    bins: int = 10,
) -> float | None:
    expected = np.asarray(truth, dtype=str)
    covered = decisions.covered
    if not np.any(covered):
        return None
    confidence = decisions.confidence[covered]
    correct = (decisions.labels[covered] == expected[covered]).astype(np.float32)
    boundaries = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for index in range(bins):
        if index == bins - 1:
            in_bin = (confidence >= boundaries[index]) & (confidence <= boundaries[index + 1])
        else:
            in_bin = (confidence >= boundaries[index]) & (confidence < boundaries[index + 1])
        if np.any(in_bin):
            weight = float(np.mean(in_bin))
            ece += weight * abs(
                float(np.mean(correct[in_bin])) - float(np.mean(confidence[in_bin]))
            )
    return ece


def _load_manifest_bytes(payload: bytes) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in payload.decode("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append(
                {
                    key: str(row[key])
                    for key in (
                        "asset_id",
                        "location",
                        "group_key",
                        "preview_sha256",
                        "source_updated",
                        "split",
                    )
                }
            )
    if len({row["asset_id"] for row in rows}) != len(rows):
        raise ValueError("training manifest contains duplicate assets")
    return rows


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
    if __package__:
        from .memory import acquire_pipeline_lock
    else:  # pragma: no cover - direct script invocation
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.triage_heads.memory import acquire_pipeline_lock

    return acquire_pipeline_lock(
        directory,
        cache_db=cache_db,
        shared_root=shared_root,
    )


def _write_json(path: Path, payload: Any) -> None:
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


def _artifact_identity_sha256(arrays: dict[str, Any]) -> str:
    digest = hashlib.sha256(b"triage-location-head-identity-v1\0")
    for key in sorted(arrays):
        array = np.ascontiguousarray(np.asarray(arrays[key]))
        if array.dtype.hasobject:
            raise ValueError("promoted artifacts cannot contain object arrays")
        digest.update(key.encode("utf-8") + b"\0")
        digest.update(array.dtype.str.encode("ascii") + b"\0")
        digest.update(json.dumps(array.shape, separators=(",", ":")).encode("ascii") + b"\0")
        digest.update(array.tobytes())
    return digest.hexdigest()


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def certification_evidence_sha256(report: dict[str, Any]) -> str:
    """Seal evaluation evidence while excluding only publication state."""
    evidence = dict(report)
    evidence.pop("certification_evidence_sha256", None)
    evidence.pop("promoted", None)
    return _canonical_json_sha256(evidence)


def _snapshot_certification_inputs(
    truth_dir: Path,
    *,
    include_review_data: bool = True,
    expected_fresh_binding: Any | None = None,
    expected_truth_manifest_sha256: str | None = None,
) -> dict[str, bytes]:
    if not include_review_data:
        if expected_fresh_binding is None:
            raise ValueError("active certification requires its frozen fresh-truth binding")
        if __package__:
            from .fresh_truth import load_fresh_truth_package
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.fresh_truth import load_fresh_truth_package

        package = load_fresh_truth_package(
            truth_dir,
            expected_binding=expected_fresh_binding,
            expected_truth_manifest_sha256=expected_truth_manifest_sha256,
        )
        return dict(package.files)
    paths = [
        truth_dir / "library_truth.jsonl",
        truth_dir / "battery_key.json",
        *sorted((truth_dir / "battery_shards").glob("bshard_*.jsonl")),
    ]
    if include_review_data:
        paths.append(truth_dir / "review_data.json")
    return {str(path.relative_to(truth_dir)): path.read_bytes() for path in paths}


def _certification_inputs_sha256(snapshots: dict[str, bytes]) -> str:
    if "truth-manifest.json" in snapshots:
        if __package__:
            from .fresh_truth import fresh_truth_inputs_sha256
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.fresh_truth import fresh_truth_inputs_sha256

        return fresh_truth_inputs_sha256(snapshots["truth-manifest.json"])
    digest = hashlib.sha256(b"triage-certification-inputs-v1\0")
    for name, payload in sorted(snapshots.items()):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _reveal_active_certification_inputs(
    *,
    artifact_dir: Path,
    truth_dir: Path,
    fresh_truth_binding: Any,
    _test_attempt_ledger_dir: Path | None = None,
) -> tuple[dict[str, bytes] | None, Any, Any | None]:
    """Freeze the attempt and consume its reveal marker before opening truth labels."""
    if __package__:
        from .fresh_truth import (
            commit_certification_truth_reveal,
            load_fresh_truth_commitment,
            open_certification_attempt,
        )
    else:  # pragma: no cover - direct script invocation
        from scripts.triage_heads.fresh_truth import (
            commit_certification_truth_reveal,
            load_fresh_truth_commitment,
            open_certification_attempt,
        )

    commitment = load_fresh_truth_commitment(
        truth_dir,
        expected_binding=fresh_truth_binding,
    )
    attempt, sealed_decision = open_certification_attempt(
        artifact_dir=artifact_dir,
        binding=fresh_truth_binding,
        truth_manifest_sha256=commitment.truth_manifest_sha256,
        certification_inputs_sha256=commitment.certification_inputs_sha256,
        _test_ledger_dir=_test_attempt_ledger_dir,
    )
    if sealed_decision is not None:
        return None, attempt, sealed_decision
    commit_certification_truth_reveal(attempt)
    snapshots = _snapshot_certification_inputs(
        truth_dir,
        include_review_data=False,
        expected_fresh_binding=fresh_truth_binding,
        expected_truth_manifest_sha256=commitment.truth_manifest_sha256,
    )
    if _certification_inputs_sha256(snapshots) != commitment.certification_inputs_sha256:
        raise RuntimeError("fresh truth snapshots differ from the pre-reveal commitment")
    return snapshots, attempt, None


def _canonical_private_bytes(payload: object) -> bytes:
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


def _load_private_snapshot_file(path: Path, *, label: str) -> bytes:
    candidate = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise RuntimeError(f"{label} must be a regular local file") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{label} must be a regular local file")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise PermissionError(f"{label} must be mode 0600")
        if before.st_nlink != 1:
            raise RuntimeError(f"{label} must be a single-link regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda value: (  # noqa: E731
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
    )
    if identity(before) != identity(after):
        raise RuntimeError(f"{label} changed while it was being snapshotted")
    return payload


def _verify_plain_self_digest(
    payload: Mapping[str, Any],
    *,
    field: str,
    label: str,
) -> str:
    digest = payload.get(field)
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if (
        not _is_sha256(digest)
        or hashlib.sha256(_canonical_private_bytes(unsigned)).hexdigest() != digest
    ):
        raise RuntimeError(f"{label} digest does not reproduce")
    return str(digest)


def _fresh_selection_sha256(rows: list[Mapping[str, Any]]) -> str:
    selection = sorted(
        (
            str(row["asset_id"]),
            str(row["component_key"]),
            str(row["moment_key"]),
            str(row["capture_day"]),
            str(row["stratum"]),
        )
        for row in rows
    )
    encoded = json.dumps(
        selection,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"triage-cohort-selection-v1\0" + encoded).hexdigest()


def require_active_certification_source(
    train_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Select the sole active source and require its reserved fresh-truth seal."""
    raw_sources = train_metrics.get("training_input_sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != 1:
        raise ValueError("active certification requires exactly one active-v1 training source")
    source = raw_sources[0]
    if not isinstance(source, dict) or source.get("mode") != "active-v1-owner":
        raise ValueError("active certification requires exactly one active-v1 training source")
    if not _is_sha256(source.get("fresh_truth_selection_sha256")):
        raise ValueError("active training report has no valid fresh truth selection")
    if not _is_sha256(source.get("fresh_truth_approval_private_sha256")) or not _is_sha256(
        source.get("fresh_truth_approval_public_sha256")
    ):
        raise ValueError("active training report has no valid fresh truth approval digests")
    return dict(source)


def load_fresh_certification_snapshot(
    approval_dir: Path,
    *,
    expected_selection_sha256: str | None = None,
    expected_approval_private_sha256: str | None = None,
    expected_approval_public_sha256: str | None = None,
    memory_guard: Callable[[], int] | None = None,
) -> FreshCertificationSnapshot:
    """Authenticate exact400 v3 evidence and snapshot each approved pixel once.

    Expected digests are mandatory at certification call sites. The embedding
    stage may omit them before a training report exists; the self-authenticated
    approval then supplies the same identities that training later seals.
    """
    root = Path(approval_dir)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("fresh certification approval must be a private directory")
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise PermissionError("fresh certification approval directory must be mode 0700")
    expected_files = {
        "certification-index.jsonl",
        "approval-private.json",
        "approval-public.json",
    }
    if {path.name for path in root.iterdir()} != expected_files:
        raise RuntimeError("fresh certification approval is incomplete or has extra files")
    index_bytes = _load_private_snapshot_file(
        root / "certification-index.jsonl",
        label="fresh certification index",
    )
    private_bytes = _load_private_snapshot_file(
        root / "approval-private.json",
        label="fresh private approval",
    )
    public_bytes = _load_private_snapshot_file(
        root / "approval-public.json",
        label="fresh public approval",
    )
    try:
        private = json.loads(private_bytes)
        public = json.loads(public_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("fresh certification approval contains invalid JSON") from error
    if not isinstance(private, dict) or not isinstance(public, dict):
        raise RuntimeError("fresh certification approval manifests are malformed")
    if private_bytes != _canonical_private_bytes(
        private
    ) or public_bytes != _canonical_private_bytes(public):
        raise RuntimeError("fresh certification approval manifests are not canonical")
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
        raise RuntimeError("fresh certification approval violates the exact v3 schema")
    private_sha256 = _verify_plain_self_digest(
        private,
        field="approval_private_sha256",
        label="fresh private approval",
    )
    public_sha256 = _verify_plain_self_digest(
        public,
        field="approval_public_sha256",
        label="fresh public approval",
    )
    bound_selection_sha256 = (
        private.get("selection_sha256")
        if expected_selection_sha256 is None
        else expected_selection_sha256
    )
    bound_private_sha256 = (
        private_sha256
        if expected_approval_private_sha256 is None
        else expected_approval_private_sha256
    )
    bound_public_sha256 = (
        public_sha256
        if expected_approval_public_sha256 is None
        else expected_approval_public_sha256
    )
    if (
        not _is_sha256(bound_selection_sha256)
        or not _is_sha256(bound_private_sha256)
        or not _is_sha256(bound_public_sha256)
        or private_sha256 != bound_private_sha256
        or public_sha256 != bound_public_sha256
    ):
        raise RuntimeError("fresh certification approval differs from the active training report")
    expected_header = (
        "truth-reserved",
        "fresh-location-cert-v3",
        bound_selection_sha256,
        400,
    )
    if (
        private.get("status"),
        private.get("cohort_name"),
        private.get("selection_sha256"),
        private.get("selected_count"),
    ) != expected_header or (
        public.get("status"),
        public.get("cohort_name"),
        public.get("selection_sha256"),
        public.get("selected_count"),
    ) != expected_header:
        raise RuntimeError("fresh certification approval differs from active training lineage")
    if (
        private.get("schema") != "triage-fresh-certification-approval-private-v3"
        or public.get("schema") != "triage-fresh-certification-approval-public-v3"
    ):
        raise RuntimeError("fresh certification approval uses an unsupported schema")
    inventory_sha256 = private.get("inventory_sha256")
    if not _is_sha256(inventory_sha256) or public.get("inventory_sha256") != inventory_sha256:
        raise RuntimeError("fresh certification inventory lineage is malformed")
    if (
        public.get("distinct_days"),
        public.get("distinct_components"),
        public.get("distinct_moments"),
    ) != (400, 400, 400):
        raise RuntimeError("fresh certification approval lacks exact 400-way diversity")
    private_evidence = public.get("private_evidence")
    truth_ledger = public.get("truth_ledger")
    private_final = private.get("final_preview_store")
    public_final = public.get("final_preview_evidence")
    if not all(
        isinstance(value, dict)
        for value in (private_evidence, truth_ledger, private_final, public_final)
    ):
        raise RuntimeError("fresh certification approval lineage is malformed")
    if (
        set(private_evidence)
        != {
            "certification_index_sha256",
            "approval_private_sha256",
        }
        or private_evidence.get("approval_private_sha256") != private_sha256
    ):
        raise RuntimeError("fresh public approval does not bind its private approval")
    if set(truth_ledger) != {"sha256_at_selection", "sha256_after_reservation"} or (
        truth_ledger.get("sha256_at_selection"),
        truth_ledger.get("sha256_after_reservation"),
    ) != (
        private.get("truth_ledger_sha256_at_selection"),
        private.get("truth_ledger_sha256_after_reservation"),
    ):
        raise RuntimeError("fresh certification approval disagrees about truth lineage")
    final_fields = {
        "manifest_sha256",
        "private_index_sha256",
        "selection_lock_sha256",
        "image_set_sha256",
    }
    if set(private_final) != {"path", *final_fields} or set(public_final) != final_fields:
        raise RuntimeError("fresh certification preview evidence is malformed")
    if any(private_final.get(field) != public_final.get(field) for field in final_fields) or any(
        not _is_sha256(private_final.get(field)) for field in final_fields
    ):
        raise RuntimeError("fresh certification preview evidence does not reproduce")
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    if (
        private.get("certification_index_file") != "certification-index.jsonl"
        or private.get("certification_index_sha256") != index_sha256
        or private_evidence.get("certification_index_sha256") != index_sha256
    ):
        raise RuntimeError("fresh certification index digest does not reproduce")
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
    try:
        raw_rows = [json.loads(line) for line in index_bytes.splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("fresh certification index contains invalid JSON") from error
    if len(raw_rows) != 400 or any(
        not isinstance(row, dict) or set(row) != index_keys for row in raw_rows
    ):
        raise RuntimeError("fresh certification index violates its exact400 schema")
    if index_bytes != b"".join(_canonical_private_bytes(row) for row in raw_rows):
        raise RuntimeError("fresh certification index is not canonical")
    identity_fields = (
        "asset_id",
        "source_audit_id",
        "final_audit_id",
        "image_relpath",
    )
    for field in identity_fields:
        values = [str(row[field]) for row in raw_rows]
        if any(not value for value in values) or len(set(values)) != 400:
            raise RuntimeError("fresh certification index identities are invalid or duplicated")
    for field in ("capture_day", "component_key", "moment_key"):
        if len({str(row[field]) for row in raw_rows}) != 400:
            raise RuntimeError("fresh certification index lacks exact 400-way diversity")
    for row in raw_rows:
        relative = Path(str(row["image_relpath"]))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not _is_sha256(row.get("preview_sha256"))
            or type(row.get("preview_width")) is not int
            or int(row["preview_width"]) < 1
            or type(row.get("preview_height")) is not int
            or int(row["preview_height"]) < 1
            or not str(row.get("source_updated", ""))
        ):
            raise RuntimeError("fresh certification index preview evidence is invalid")
    if _fresh_selection_sha256(raw_rows) != bound_selection_sha256:
        raise RuntimeError("fresh certification selection digest does not reproduce")
    image_digest_rows = [
        {
            "final_audit_id": str(row["final_audit_id"]),
            "image_relpath": str(row["image_relpath"]),
            "preview_sha256": str(row["preview_sha256"]),
        }
        for row in raw_rows
    ]
    image_set_sha256 = hashlib.sha256(
        _canonical_private_bytes(
            {"schema": "triage-fresh-cert-image-set-v3", "rows": image_digest_rows}
        )
    ).hexdigest()
    if image_set_sha256 != private_final.get("image_set_sha256"):
        raise RuntimeError("fresh certification image-set digest does not reproduce")

    final_store_raw = private_final.get("path")
    if not isinstance(final_store_raw, str) or not Path(final_store_raw).is_absolute():
        raise RuntimeError("fresh certification final preview store path is invalid")
    final_store = Path(final_store_raw)
    if final_store.is_symlink() or not final_store.is_dir():
        raise RuntimeError("fresh certification final preview store is missing")
    if stat.S_IMODE(final_store.stat().st_mode) != 0o700:
        raise PermissionError("fresh certification final preview store must be mode 0700")
    final_root = final_store.resolve()
    pixel_bytes: dict[str, bytes] = {}
    parsed_rows: list[FreshCertificationRow] = []
    pixel_digest = hashlib.sha256(b"triage-fresh-cert-pixels-v1\0")
    for position, row in enumerate(raw_rows):
        if memory_guard is not None and position % 16 == 0:
            memory_guard()
        relative = Path(str(row["image_relpath"]))
        unresolved = final_store / relative
        if unresolved.is_symlink():
            raise RuntimeError("fresh certification pixel cannot be a symlink")
        image_path = unresolved.resolve()
        if not image_path.is_relative_to(final_root):
            raise RuntimeError("fresh certification pixel escapes its approved store")
        payload = _load_private_snapshot_file(
            image_path,
            label="fresh certification pixel",
        )
        preview_sha256 = hashlib.sha256(payload).hexdigest()
        if preview_sha256 != row["preview_sha256"]:
            raise RuntimeError("fresh certification pixel digest does not reproduce")
        asset_id = str(row["asset_id"])
        pixel_bytes[asset_id] = payload
        pixel_digest.update(asset_id.encode("utf-8") + b"\0")
        pixel_digest.update(len(payload).to_bytes(8, "big"))
        pixel_digest.update(payload)
        parsed_rows.append(
            FreshCertificationRow(
                asset_id=asset_id,
                final_audit_id=str(row["final_audit_id"]),
                image_path=image_path,
                preview_sha256=preview_sha256,
                source_updated=str(row["source_updated"]),
                capture_day=str(row["capture_day"]),
                component_key=str(row["component_key"]),
                moment_key=str(row["moment_key"]),
            )
        )
    if memory_guard is not None:
        memory_guard()
    return FreshCertificationSnapshot(
        rows=tuple(parsed_rows),
        pixel_bytes=pixel_bytes,
        selection_sha256=str(bound_selection_sha256),
        inventory_sha256=str(inventory_sha256),
        certification_index_sha256=index_sha256,
        approval_private_sha256=private_sha256,
        approval_public_sha256=public_sha256,
        image_set_sha256=image_set_sha256,
        pixel_snapshot_sha256=pixel_digest.hexdigest(),
    )


def validate_fresh_certification_cache(
    approved: FreshCertificationSnapshot,
    cached: Mapping[str, tuple[np.ndarray, str, str]],
) -> None:
    """Reject missing or stale packs before any certification model is evaluated."""
    expected = {row.asset_id: row for row in approved.rows}
    if set(cached) != set(expected):
        raise ValueError("cached pack inventory differs from exact fresh certification approval")
    mismatches = sum(
        (cached[asset_id][1], cached[asset_id][2]) != (row.preview_sha256, row.source_updated)
        for asset_id, row in expected.items()
    )
    if mismatches:
        raise ValueError(f"{mismatches} cached pack lineage rows differ from approved pixels")


def build_active_certification_rows(
    approved: FreshCertificationSnapshot,
    certification_inputs: Mapping[str, bytes],
    *,
    expected_training_run_sha256: str,
    expected_candidate_sha256: Mapping[str, str],
) -> list[CertificationRow]:
    """Join exact400 fresh labels to the authenticated approval inventory."""
    if __package__:
        from .fresh_truth import FreshTruthBinding, validate_fresh_truth_snapshots
    else:  # pragma: no cover - direct script invocation
        from scripts.triage_heads.fresh_truth import (
            FreshTruthBinding,
            validate_fresh_truth_snapshots,
        )

    binding = FreshTruthBinding(
        head_version="location-v1",
        training_run_sha256=expected_training_run_sha256,
        candidate_sha256=expected_candidate_sha256,
        inventory_sha256=approved.inventory_sha256,
        selection_sha256=approved.selection_sha256,
        certification_index_sha256=approved.certification_index_sha256,
        approval_private_sha256=approved.approval_private_sha256,
        approval_public_sha256=approved.approval_public_sha256,
        image_set_sha256=approved.image_set_sha256,
        pixel_snapshot_sha256=approved.pixel_snapshot_sha256,
    )
    package = validate_fresh_truth_snapshots(
        certification_inputs,
        expected_binding=binding,
    )
    truth_ids = load_truth_asset_ids_bytes(certification_inputs["library_truth.jsonl"])
    truth = package.truth_by_asset
    teacher = package.teacher_by_asset
    expected = {row.asset_id for row in approved.rows}
    if len(truth_ids) != 400 or set(truth_ids) != expected:
        raise ValueError("fresh library truth does not match the exact400 approved index")
    if set(truth) != expected or set(teacher) != expected:
        raise ValueError("fresh paired labels do not match the exact400 approved index")
    return [
        CertificationRow(
            asset_id=row.asset_id,
            image_path=row.image_path,
            location=truth[row.asset_id],
            teacher_location=teacher[row.asset_id],
        )
        for row in approved.rows
    ]


def _pack_snapshot_sha256(
    snapshot: dict[str, tuple[np.ndarray, str, str]],
    *,
    metadata_by_asset: dict[str, dict[str, str]],
) -> str:
    if set(snapshot) != set(metadata_by_asset):
        raise ValueError("pack snapshot and cohort metadata cover different assets")
    rows: list[dict[str, Any]] = []
    for asset_id in sorted(snapshot):
        pack, preview_sha256, source_updated = snapshot[asset_id]
        contiguous = np.ascontiguousarray(pack, dtype=np.float32)
        rows.append(
            {
                "asset_id": asset_id,
                "preview_sha256": preview_sha256,
                "source_updated": source_updated,
                "pack_sha256": hashlib.sha256(contiguous.tobytes()).hexdigest(),
                **metadata_by_asset[asset_id],
            }
        )
    return _canonical_json_sha256({"schema": "triage-pack-snapshot-v1", "rows": rows})


def _existing_promotion_matches(destination: Path, proposed_identity: str) -> bool:
    if not destination.exists():
        return False
    with np.load(destination, allow_pickle=False) as existing:
        if "artifact_identity_sha256" not in existing.files:
            raise RuntimeError(
                "STOP: location-v1 lacks full immutable identity; bump the head version"
            )
        stored_identity = str(existing["artifact_identity_sha256"])
        existing_arrays = {
            key: np.array(existing[key], copy=True)
            for key in existing.files
            if key != "artifact_identity_sha256"
        }
    if _artifact_identity_sha256(existing_arrays) != stored_identity:
        raise RuntimeError("STOP: location-v1 failed its immutable identity check")
    if stored_identity != proposed_identity:
        raise RuntimeError(
            "STOP: location-v1 already names different weights or policy; bump the head version"
        )
    return True


def _build_promoted_arrays(
    source: Path | bytes,
    *,
    expected_candidate_sha256: str,
    model_kind: str,
    encoder_key: str,
    training_run_sha256: str,
    certification_evidence_sha256: str,
    temperature: float,
    operating_point: OperatingPoint,
) -> dict[str, Any]:
    candidate_bytes = source if isinstance(source, bytes) else source.read_bytes()
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    if candidate_sha256 != expected_candidate_sha256:
        raise RuntimeError("STOP: selected candidate changed after training-bundle validation")
    with np.load(io.BytesIO(candidate_bytes), allow_pickle=False) as payload:
        candidate_arrays = {
            key: np.array(payload[key], copy=True)
            for key in payload.files
            if key != "artifact_version"
        }
    metadata: dict[str, Any] = {
        "artifact_version": np.array("triage-location-head-v1"),
        "head_name": np.array("location"),
        "head_version": np.array("location-v1"),
        "model_kind": np.array(model_kind),
        "encoder_key": np.array(encoder_key),
        "training_run_sha256": np.array(training_run_sha256),
        "certification_evidence_sha256": np.array(certification_evidence_sha256),
        "candidate_sha256": np.array(candidate_sha256),
        "temperature": np.array(temperature, dtype=np.float64),
        "indoor_confidence_threshold": np.array(
            operating_point.confidence_thresholds["indoor"], dtype=np.float64
        ),
        "outdoor_confidence_threshold": np.array(
            operating_point.confidence_thresholds["outdoor"], dtype=np.float64
        ),
        "undetermined_threshold": np.array(
            operating_point.undetermined_threshold, dtype=np.float64
        ),
    }
    overlap = set(metadata) & set(candidate_arrays)
    if overlap:
        raise RuntimeError("STOP: candidate artifact collides with promoted metadata")
    return {**candidate_arrays, **metadata}


def _validate_promotion_compatibility(
    source: Path | bytes,
    destination: Path,
    **kwargs: Any,
) -> str:
    promoted_arrays = _build_promoted_arrays(source, **kwargs)
    identity = _artifact_identity_sha256(promoted_arrays)
    _existing_promotion_matches(destination, identity)
    return identity


def _promote_artifact(
    source: Path | bytes,
    destination: Path,
    *,
    expected_candidate_sha256: str,
    model_kind: str,
    encoder_key: str,
    training_run_sha256: str,
    certification_evidence_sha256: str,
    temperature: float,
    operating_point: OperatingPoint,
) -> None:
    promoted_arrays = _build_promoted_arrays(
        source,
        expected_candidate_sha256=expected_candidate_sha256,
        model_kind=model_kind,
        encoder_key=encoder_key,
        training_run_sha256=training_run_sha256,
        certification_evidence_sha256=certification_evidence_sha256,
        temperature=temperature,
        operating_point=operating_point,
    )
    identity = _artifact_identity_sha256(promoted_arrays)
    if _existing_promotion_matches(destination, identity):
        return
    promoted_arrays["artifact_identity_sha256"] = np.array(identity)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **promoted_arrays)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not _existing_promotion_matches(destination, identity):  # pragma: no cover
                raise AssertionError("existing-promotion validation returned unexpectedly")
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _upsert_metrics(path: Path, row: dict[str, Any]) -> None:
    existing: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            existing = [json.loads(line) for line in handle if line.strip()]
    existing = [item for item in existing if item.get("head_version") != row["head_version"]]
    existing.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for item in existing:
                handle.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--cache-db", type=Path, default=None)
    parser.add_argument(
        "--truth-dir",
        type=Path,
        default=None,
        help="fresh exact400 label package (required in active mode)",
    )
    parser.add_argument(
        "--fresh-approval-dir",
        type=Path,
        default=None,
        help="fresh-location-cert-v3 approval directory (active mode)",
    )
    parser.add_argument(
        "--legacy-mode",
        action="store_true",
        help="explicitly authorize retired truth and pointerless legacy training artifacts",
    )
    parser.add_argument(
        "--max-working-set-gib",
        type=float,
        default=DEFAULT_OFFLINE_WORKING_SET_GIB,
        help="certification-process RSS ceiling (hard maximum: 8 GiB)",
    )
    args = parser.parse_args(argv)
    try:
        validate_offline_working_set_gib(args.max_working_set_gib)
    except ValueError as error:
        parser.error(str(error))
    return args


def _publish_active_certification_decision(
    *,
    artifact_dir: Path,
    component_bytes: Mapping[str, bytes],
    train_metrics: Mapping[str, Any],
    encoder_key: str,
    sealed_decision: Any,
    memory_guard: Callable[[], int],
) -> int:
    """Resume publication from sealed evidence without opening truth again."""
    if __package__:
        from .fresh_truth import finalize_certification_attempt
    else:  # pragma: no cover - direct script invocation
        from scripts.triage_heads.fresh_truth import finalize_certification_attempt

    report = dict(sealed_decision.report)
    selected = str(report["selected_candidate"])
    evaluated = report["candidates"]
    selected_report = evaluated[selected]
    selected_metrics = selected_report["certification"]
    acceptance = report["acceptance"]
    acceptance_passed = bool(acceptance["passed"])
    accuracy = selected_metrics["accuracy_on_covered"]
    coverage = float(selected_metrics["coverage"])
    miss_over_five = bool(acceptance["misses_by_more_than_five_points"])
    final_path = artifact_dir / "location-v1.npz"
    if acceptance_passed:
        operating_point_payload = selected_report["operating_point"]
        operating_point = OperatingPoint(
            confidence_thresholds={
                str(label): float(threshold)
                for label, threshold in operating_point_payload["confidence_thresholds"].items()
            },
            undetermined_threshold=float(operating_point_payload["undetermined_threshold"]),
        )
        component_name = f"{selected}_candidate"
        selected_candidate_bytes = component_bytes[component_name]
        promotion_kwargs = {
            "expected_candidate_sha256": str(train_metrics["component_sha256"][component_name]),
            "model_kind": selected,
            "encoder_key": encoder_key,
            "training_run_sha256": str(report["training_run_sha256"]),
            "certification_evidence_sha256": str(report["certification_evidence_sha256"]),
            "temperature": float(selected_report["temperature"]),
            "operating_point": operating_point,
        }
        _validate_promotion_compatibility(
            selected_candidate_bytes,
            final_path,
            **promotion_kwargs,
        )
        memory_guard()
        _promote_artifact(selected_candidate_bytes, final_path, **promotion_kwargs)
        sidecar = {
            "head_name": "location",
            "head_version": "location-v1",
            "model_kind": selected,
            "encoder_key": encoder_key,
            "training_run_sha256": report["training_run_sha256"],
            "certification_evidence_sha256": report["certification_evidence_sha256"],
            "artifact": final_path.name,
            "temperature": selected_report["temperature"],
            "operating_point": selected_report["operating_point"],
            "promotion_authorized": True,
            "promoted": True,
        }
        _write_json(artifact_dir / "location-v1.json", sidecar)
    finalize_certification_attempt(sealed_decision=sealed_decision)
    if not acceptance_passed and final_path.exists():
        raise SystemExit(
            "STOP: a failing candidate cannot overwrite metadata for the existing location-v1"
        )
    print(
        f"certification: accuracy-on-covered={accuracy if accuracy is not None else 'n/a'} "
        f"coverage={coverage:.4f} selected={selected} passed={acceptance_passed}",
        flush=True,
    )
    if miss_over_five:
        print("STOP: acceptance misses by more than five points; no tuning performed", flush=True)
        return 2
    if not acceptance_passed:
        print("STOP: candidate was reported but not promoted because an acceptance gate failed")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)

    def memory_guard() -> int:
        return ensure_offline_process_memory(max_working_set_gib=args.max_working_set_gib)

    memory_guard()
    cache_path = args.cache_db or args.artifact_dir / "embeddings.db"
    if args.legacy_mode:
        if args.fresh_approval_dir is not None:
            raise SystemExit("STOP: legacy mode cannot consume active fresh approval evidence")
        truth_dir = args.truth_dir or DEFAULT_TRUTH_DIR
        fresh_approval_dir = None
    else:
        if args.truth_dir is None:
            raise SystemExit("STOP: active certification requires an explicit fresh --truth-dir")
        truth_dir = args.truth_dir
        fresh_approval_dir = args.fresh_approval_dir or (
            args.artifact_dir / FRESH_APPROVAL_RELATIVE
        )
    if __package__:
        from .memory import ensure_private_directory
    else:  # pragma: no cover - direct script invocation
        from scripts.triage_heads.memory import ensure_private_directory

    ensure_private_directory(args.artifact_dir)
    try:
        _artifact_lock = _acquire_artifact_lock(
            args.artifact_dir,
            cache_db=cache_path,
            shared_root=DEFAULT_ARTIFACT_DIR,
        )
    except RuntimeError as error:
        raise SystemExit(f"STOP: {error}") from error
    embed_meta_path = args.artifact_dir / "embed-meta-cpu.json"

    if __package__:
        from .embed import EmbeddingCache
        from .train import (
            TrainingExample,
            load_linear_artifact,
            load_mlp_artifact,
            load_pca_artifact,
            pca_sha256,
            project_deployment_features,
            snapshot_training_bundle,
            training_pack_snapshot_sha256,
            validate_label_embedding_lineage,
        )
    else:  # pragma: no cover - direct script invocation
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.triage_heads.embed import EmbeddingCache
        from scripts.triage_heads.train import (
            TrainingExample,
            load_linear_artifact,
            load_mlp_artifact,
            load_pca_artifact,
            pca_sha256,
            project_deployment_features,
            snapshot_training_bundle,
            training_pack_snapshot_sha256,
            validate_label_embedding_lineage,
        )

    try:
        training_bundle = snapshot_training_bundle(
            args.artifact_dir,
            allow_legacy=args.legacy_mode,
        )
    except (OSError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error
    train_metrics_path = training_bundle.report_path
    component_paths = training_bundle.component_paths
    manifest_path = component_paths["training_manifest"]
    pca_path = component_paths["pca_artifact"]
    candidate_paths = {
        "linear": component_paths["linear_candidate"],
        "mlp": component_paths["mlp_candidate"],
    }
    required = (
        manifest_path,
        pca_path,
        embed_meta_path,
        train_metrics_path,
        *candidate_paths.values(),
    )
    if any(not path.is_file() for path in required):
        raise SystemExit("run embedding, labeling, and training before certification")

    embed_meta = json.loads(embed_meta_path.read_text(encoding="utf-8"))
    train_metrics = training_bundle.report
    if args.legacy_mode:
        if train_metrics.get("training_input_sources") is not None:
            raise SystemExit("STOP: active-v1 training cannot use legacy certification mode")
        active_source = None
        fresh_approval = None
    else:
        try:
            active_source = require_active_certification_source(train_metrics)
            fresh_approval = load_fresh_certification_snapshot(
                fresh_approval_dir,
                expected_selection_sha256=active_source["fresh_truth_selection_sha256"],
                expected_approval_private_sha256=active_source[
                    "fresh_truth_approval_private_sha256"
                ],
                expected_approval_public_sha256=active_source["fresh_truth_approval_public_sha256"],
                memory_guard=memory_guard,
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as error:
            raise SystemExit(f"STOP: {error}") from error
    training_run_sha256 = training_bundle.training_run_sha256
    component_bytes = training_bundle.component_bytes
    candidate_sha256 = {
        name: hashlib.sha256(component_bytes[f"{name}_candidate"]).hexdigest()
        for name in ("linear", "mlp")
    }
    fresh_truth_binding = None
    if fresh_approval is not None:
        if __package__:
            from .fresh_truth import FreshTruthBinding
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.fresh_truth import FreshTruthBinding

        fresh_truth_binding = FreshTruthBinding(
            head_version="location-v1",
            training_run_sha256=training_run_sha256,
            candidate_sha256=candidate_sha256,
            inventory_sha256=fresh_approval.inventory_sha256,
            selection_sha256=fresh_approval.selection_sha256,
            certification_index_sha256=fresh_approval.certification_index_sha256,
            approval_private_sha256=fresh_approval.approval_private_sha256,
            approval_public_sha256=fresh_approval.approval_public_sha256,
            image_set_sha256=fresh_approval.image_set_sha256,
            pixel_snapshot_sha256=fresh_approval.pixel_snapshot_sha256,
        )
    encoder_key = str(train_metrics["encoder_key"])
    staging_encoder_key = str(train_metrics.get("staging_encoder_key"))
    if staging_encoder_key != str(embed_meta["encoder_key"]):
        raise SystemExit("STOP: training and embedding lineage do not match")
    pca = load_pca_artifact(io.BytesIO(component_bytes["pca_artifact"]))
    semantic_pca_sha256 = pca_sha256(pca)
    if semantic_pca_sha256 != str(train_metrics.get("pca", {}).get("sha256")):
        raise SystemExit("STOP: sealed PCA does not match its semantic training identity")
    if f"@pca-sha256:{semantic_pca_sha256}" not in str(
        train_metrics.get("pca", {}).get("layout_version")
    ):
        raise SystemExit("STOP: projected layout does not bind the sealed PCA")
    cache = EmbeddingCache(cache_path)
    manifest = _load_manifest_bytes(component_bytes["training_manifest"])
    manifest_ids = [row["asset_id"] for row in manifest]
    manifest_examples = [
        TrainingExample(
            asset_id=row["asset_id"],
            label=row["location"],
            group_key=row["group_key"],
            preview_sha256=row["preview_sha256"],
            source_updated=row["source_updated"],
        )
        for row in manifest
    ]
    manifest_snapshot = cache.staging_snapshot(manifest_ids, staging_encoder_key)
    try:
        validate_label_embedding_lineage(
            manifest_examples,
            {
                asset_id: (preview_sha256, source_updated)
                for asset_id, (_pack, preview_sha256, source_updated) in manifest_snapshot.items()
            },
        )
    except ValueError as error:
        raise SystemExit(f"STOP: {error}") from error
    manifest_packs = {asset_id: row[0] for asset_id, row in manifest_snapshot.items()}
    if len(manifest_packs) != len(manifest):
        raise SystemExit("STOP: staging training packs are incomplete")
    manifest_pack_snapshot_sha256 = training_pack_snapshot_sha256(
        manifest_examples,
        {row["asset_id"]: row["split"] for row in manifest},
        manifest_packs,
    )
    if manifest_pack_snapshot_sha256 != str(train_metrics.get("training_pack_snapshot_sha256")):
        raise SystemExit("STOP: training staging packs changed after the sealed training run")
    feature_matrix = project_deployment_features(
        pca,
        np.stack([manifest_packs[row["asset_id"]] for row in manifest]),
    )
    labels = np.asarray([row["location"] for row in manifest])
    splits = np.asarray([row["split"] for row in manifest])
    cal_mask = splits == "cal"
    test_mask = splits == "test"
    if int(cal_mask.sum()) < 250:
        raise SystemExit(f"STOP: calibration split has only {int(cal_mask.sum())} rows")

    candidates = {
        "linear": load_linear_artifact(io.BytesIO(component_bytes["linear_candidate"])),
        "mlp": load_mlp_artifact(io.BytesIO(component_bytes["mlp_candidate"])),
    }
    cert_snapshot = None
    if fresh_approval is not None:
        approved_ids = [row.asset_id for row in fresh_approval.rows]
        try:
            validate_certification_disjoint(manifest_ids, approved_ids)
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
        cert_snapshot = cache.staging_snapshot(approved_ids, staging_encoder_key)
        try:
            validate_fresh_certification_cache(fresh_approval, cert_snapshot)
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
        memory_guard()

    certification_attempt = None
    sealed_decision = None
    try:
        if args.legacy_mode:
            certification_inputs = _snapshot_certification_inputs(
                truth_dir,
                include_review_data=True,
            )
        else:
            certification_inputs, certification_attempt, sealed_decision = (
                _reveal_active_certification_inputs(
                    artifact_dir=args.artifact_dir,
                    truth_dir=truth_dir,
                    fresh_truth_binding=fresh_truth_binding,
                )
            )
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        raise SystemExit(f"STOP: {error}") from error
    if sealed_decision is not None:
        return _publish_active_certification_decision(
            artifact_dir=args.artifact_dir,
            component_bytes=component_bytes,
            train_metrics=train_metrics,
            encoder_key=encoder_key,
            sealed_decision=sealed_decision,
            memory_guard=memory_guard,
        )
    if certification_inputs is None:  # pragma: no cover - state invariant
        raise AssertionError("active certification lacks truth inputs and a sealed decision")
    if args.legacy_mode:
        try:
            validate_certification_disjoint(
                manifest_ids,
                load_truth_asset_ids_bytes(certification_inputs["library_truth.jsonl"]),
            )
        except ValueError as error:
            raise SystemExit(f"STOP: {error}") from error
    if args.legacy_mode:
        certification_rows, missing_images = load_certification_rows_bytes(
            certification_inputs["battery_key.json"],
            {
                name: payload
                for name, payload in certification_inputs.items()
                if name.startswith("battery_shards/")
            },
            certification_inputs["review_data.json"],
        )
    else:
        try:
            certification_rows = build_active_certification_rows(
                fresh_approval,
                certification_inputs,
                expected_training_run_sha256=training_run_sha256,
                expected_candidate_sha256=candidate_sha256,
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as error:
            raise SystemExit(f"STOP: {error}") from error
        missing_images = 0
    cert_ids = [row.asset_id for row in certification_rows]
    if cert_snapshot is None:
        cert_snapshot = cache.staging_snapshot(cert_ids, staging_encoder_key)
    memory_guard()
    cert_packs = {asset_id: row[0] for asset_id, row in cert_snapshot.items()}
    if len(cert_packs) != len(certification_rows):
        raise SystemExit(
            f"STOP: cache has {len(cert_packs)} of {len(certification_rows)} certification packs"
        )
    cert_features = project_deployment_features(
        pca,
        np.stack([cert_packs[row.asset_id] for row in certification_rows]),
    )
    cert_truth = np.asarray([row.location for row in certification_rows])
    cert_teacher = np.asarray([row.teacher_location for row in certification_rows])
    teacher_undetermined_rate = float(np.mean(cert_teacher == UNDETERMINED))
    memory_guard()

    evaluated: dict[str, dict[str, Any]] = {}
    runtime: dict[str, dict[str, Any]] = {}
    for name, model in candidates.items():
        cal_logits = model.logits(feature_matrix[cal_mask])
        temperature = fit_temperature(cal_logits, labels[cal_mask], model.classes)
        cal_probabilities = softmax_temperature(cal_logits, temperature)
        operating_point, curve = calibrate_operating_point(
            cal_probabilities,
            labels[cal_mask],
            model.classes,
            target_accuracy=ACCURACY_TARGET,
        )
        cal_decisions = decide_probabilities(cal_probabilities, model.classes, operating_point)
        test_probabilities = softmax_temperature(
            model.logits(feature_matrix[test_mask]), temperature
        )
        test_decisions = decide_probabilities(test_probabilities, model.classes, operating_point)
        cert_probabilities = softmax_temperature(model.logits(cert_features), temperature)
        cert_decisions = decide_probabilities(cert_probabilities, model.classes, operating_point)
        cal_metrics = score_decisions(labels[cal_mask], cal_decisions)
        test_metrics = score_decisions(labels[test_mask], test_decisions)
        cert_metrics = score_decisions(cert_truth, cert_decisions)
        cert_metrics["ece_covered"] = expected_calibration_error(cert_truth, cert_decisions)
        cert_metrics["teacher_undetermined_rate"] = teacher_undetermined_rate
        cert_metrics["abstention_honesty_passed"] = (
            cert_metrics["ece_covered"] is not None
            and cert_metrics["ece_covered"] <= 0.05
            and cert_metrics["emitted_undetermined_rate"] <= 1.5 * teacher_undetermined_rate
        )
        evaluated[name] = {
            "temperature": temperature,
            "operating_point": {
                "confidence_thresholds": operating_point.confidence_thresholds,
                "undetermined_threshold": operating_point.undetermined_threshold,
            },
            "calibration": cal_metrics,
            "teacher_labeled_test": test_metrics,
            "certification": cert_metrics,
        }
        runtime[name] = {
            "model": model,
            "temperature": temperature,
            "operating_point": operating_point,
            "curve": curve,
            "cert_decisions": cert_decisions,
        }

    paired_delta = paired_accuracy_delta(
        cert_truth,
        runtime["linear"]["cert_decisions"],
        runtime["mlp"]["cert_decisions"],
    )
    selected = select_candidate(
        evaluated["linear"]["certification"],
        evaluated["mlp"]["certification"],
        paired_delta=paired_delta,
    )
    selected_metrics = evaluated[selected]["certification"]
    accuracy = selected_metrics["accuracy_on_covered"]
    coverage = selected_metrics["coverage"]
    abstention_honesty_passed = bool(selected_metrics["abstention_honesty_passed"])
    acceptance_passed = bool(
        accuracy is not None
        and accuracy >= ACCURACY_TARGET
        and coverage >= COVERAGE_TARGET
        and abstention_honesty_passed
    )
    miss_over_five = (
        accuracy is None or accuracy < ACCURACY_TARGET - 0.05 or coverage < COVERAGE_TARGET - 0.05
    )
    selected_runtime = runtime[selected]
    selected_candidate_bytes = component_bytes[f"{selected}_candidate"]
    final_path = args.artifact_dir / "location-v1.npz"
    curves_payload = {name: runtime[name]["curve"] for name in runtime}
    certification_cohort_sha256 = _pack_snapshot_sha256(
        cert_snapshot,
        metadata_by_asset={
            row.asset_id: {
                "truth_location": row.location,
                "teacher_location": row.teacher_location,
            }
            for row in certification_rows
        },
    )
    report = {
        "schema_version": "triage-location-certification-v1",
        "head_version": "location-v1",
        "encoder_key": encoder_key,
        "training_run_sha256": training_run_sha256,
        "certification_inputs_sha256": _certification_inputs_sha256(certification_inputs),
        "manifest_pack_snapshot_sha256": manifest_pack_snapshot_sha256,
        "certification_cohort_sha256": certification_cohort_sha256,
        "cascade_curves_sha256": _canonical_json_sha256(curves_payload),
        "selected_candidate": selected,
        "selection_rule": (
            "MLP only when paired and aggregate certification accuracy improve by >=2 points, "
            "coverage is >=0.85, and abstention honesty passes"
        ),
        "paired_accuracy_delta_mlp_minus_linear": paired_delta,
        "promotion_authorized": acceptance_passed,
        # For active fresh certification this is the immutable decision intent,
        # not a publication progress bit.  Publication happens only after the
        # scored decision has been sealed.
        "promoted": (acceptance_passed if certification_attempt is not None else False),
        "usable_certification_labels": len(certification_rows),
        "missing_certification_images": missing_images,
        "certification_label_counts": dict(
            sorted(
                {label: int(np.sum(cert_truth == label)) for label in np.unique(cert_truth)}.items()
            )
        ),
        "candidates": evaluated,
        "measured_pair": {
            "accuracy_on_covered": accuracy,
            "coverage": coverage,
        },
        "acceptance": {
            "accuracy_target": ACCURACY_TARGET,
            "coverage_target": COVERAGE_TARGET,
            "passed": acceptance_passed,
            "misses_by_more_than_five_points": miss_over_five,
        },
        "abstention_honesty": {
            "passed": abstention_honesty_passed,
            "ece_target": 0.05,
            "teacher_undetermined_rate": teacher_undetermined_rate,
            "max_emitted_undetermined_rate": 1.5 * teacher_undetermined_rate,
        },
    }
    if fresh_approval is not None:
        report["fresh_certification_evidence"] = {
            "cohort_name": "fresh-location-cert-v3",
            "selected_count": len(fresh_approval.rows),
            "inventory_sha256": fresh_approval.inventory_sha256,
            "selection_sha256": fresh_approval.selection_sha256,
            "certification_index_sha256": (fresh_approval.certification_index_sha256),
            "approval_private_sha256": fresh_approval.approval_private_sha256,
            "approval_public_sha256": fresh_approval.approval_public_sha256,
            "image_set_sha256": fresh_approval.image_set_sha256,
            "pixel_snapshot_sha256": fresh_approval.pixel_snapshot_sha256,
            "training_fresh_truth_selection_sha256": active_source["fresh_truth_selection_sha256"],
            "training_fresh_truth_approval_private_sha256": active_source[
                "fresh_truth_approval_private_sha256"
            ],
            "training_fresh_truth_approval_public_sha256": active_source[
                "fresh_truth_approval_public_sha256"
            ],
        }
    if certification_attempt is not None:
        report["certification_attempt_sha256"] = certification_attempt.attempt_sha256

    evidence_sha256 = certification_evidence_sha256(report)
    report["certification_evidence_sha256"] = evidence_sha256
    curves_path = args.artifact_dir / "cascade-curves.json"
    certification_path = args.artifact_dir / "certification-metrics.json"
    registry_path = args.artifact_dir / "metrics.jsonl"
    sidecar_path = args.artifact_dir / "location-v1.json"
    # Seal the active fresh-truth score before publishing any candidate-side
    # artifact.  A later invocation can resume from this immutable decision
    # without reopening truth or recomputing scores.
    if certification_attempt is not None:
        if __package__:
            from .fresh_truth import seal_certification_decision
        else:  # pragma: no cover - direct script execution
            from fresh_truth import seal_certification_decision

        sealed_decision = seal_certification_decision(
            attempt=certification_attempt,
            report=report,
            curves=curves_payload,
            facts=(),
        )
        return _publish_active_certification_decision(
            artifact_dir=args.artifact_dir,
            component_bytes=component_bytes,
            train_metrics=train_metrics,
            encoder_key=encoder_key,
            sealed_decision=sealed_decision,
            memory_guard=memory_guard,
        )

    registry_row = {
        "head_version": "location-v1" if acceptance_passed else "location-v1-candidate",
        "encoder_key": encoder_key,
        "training_run_sha256": training_run_sha256,
        "certification_evidence_sha256": evidence_sha256,
        "selected_candidate": selected,
        "accuracy_on_covered": accuracy,
        "coverage": coverage,
        "n_total": selected_metrics["n_total"],
        "n_covered": selected_metrics["n_covered"],
        "acceptance_passed": acceptance_passed,
        "promoted": False,
    }

    component_name = f"{selected}_candidate"
    expected_candidate_sha256 = str(train_metrics["component_sha256"][component_name])
    promotion_kwargs = {
        "expected_candidate_sha256": expected_candidate_sha256,
        "model_kind": selected,
        "encoder_key": encoder_key,
        "training_run_sha256": training_run_sha256,
        "certification_evidence_sha256": evidence_sha256,
        "temperature": selected_runtime["temperature"],
        "operating_point": selected_runtime["operating_point"],
    }
    if acceptance_passed:
        try:
            _validate_promotion_compatibility(
                selected_candidate_bytes, final_path, **promotion_kwargs
            )
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
    elif final_path.exists():
        if certification_attempt is not None:
            if __package__:
                from .fresh_truth import seal_certification_attempt
            else:  # pragma: no cover - direct script invocation
                from scripts.triage_heads.fresh_truth import seal_certification_attempt

            seal_certification_attempt(
                attempt=certification_attempt,
                report=report,
                curves=curves_payload,
                promoted=False,
            )
        else:
            rejected_path = (
                args.artifact_dir / f"certification-rejected-{evidence_sha256[:12]}.json"
            )
            _write_json(rejected_path, report)
        raise SystemExit(
            "STOP: a failing candidate cannot overwrite metadata for the existing location-v1"
        )

    # Durable evidence is the prepare record; the create-only NPZ below is the
    # commit point. A crash before publication therefore cannot leave an
    # untraceable location-v1 artifact.
    if certification_attempt is None:
        _write_json(certification_path, report)
        _write_json(curves_path, curves_payload)
    if acceptance_passed:
        memory_guard()
        sidecar = {
            "head_name": "location",
            "head_version": "location-v1",
            "model_kind": selected,
            "encoder_key": encoder_key,
            "training_run_sha256": training_run_sha256,
            "certification_evidence_sha256": evidence_sha256,
            "artifact": final_path.name,
            "temperature": selected_runtime["temperature"],
            "operating_point": evaluated[selected]["operating_point"],
            "promotion_authorized": True,
            "promoted": True,
        }
        _promote_artifact(selected_candidate_bytes, final_path, **promotion_kwargs)
        decisions = selected_runtime["cert_decisions"]
        cache.remember_facts(
            [
                {
                    "asset_id": row.asset_id,
                    "label": str(decisions.labels[index]),
                    "confidence": float(decisions.confidence[index]),
                    "covered": bool(decisions.covered[index]),
                }
                for index, row in enumerate(certification_rows)
            ],
            head_name="location",
            head_version="location-v1",
            encoder_key=encoder_key,
        )
        report["promoted"] = True
        registry_row["promoted"] = True
        if certification_attempt is None:
            _write_json(certification_path, report)
            _upsert_metrics(registry_path, registry_row)
        _write_json(
            sidecar_path,
            sidecar,
        )
    else:
        if certification_attempt is None:
            _upsert_metrics(registry_path, registry_row)
    if certification_attempt is not None:
        if __package__:
            from .fresh_truth import seal_certification_attempt
        else:  # pragma: no cover - direct script invocation
            from scripts.triage_heads.fresh_truth import seal_certification_attempt

        seal_certification_attempt(
            attempt=certification_attempt,
            report=report,
            curves=curves_payload,
            promoted=bool(report["promoted"]),
        )
    print(
        f"certification: accuracy-on-covered={accuracy if accuracy is not None else 'n/a'} "
        f"coverage={coverage:.4f} selected={selected} passed={acceptance_passed}",
        flush=True,
    )
    if miss_over_five:
        print("STOP: acceptance misses by more than five points; no tuning performed", flush=True)
        return 2
    if not acceptance_passed:
        print("STOP: candidate was reported but not promoted because an acceptance gate failed")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
