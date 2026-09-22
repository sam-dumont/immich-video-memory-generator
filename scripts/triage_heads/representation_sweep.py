#!/usr/bin/env python3
"""Develop a location representation on retired truth, then freeze it for fresh certification.

This module is intentionally unable to promote a head.  It compares only layouts
recoverable from the cached six-vector DINO pack, cross-fits calibration on the
retired owner cohort, and publishes a sealed candidate that a separate fresh
certification run must evaluate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from .calibrate_certify import (
    ACCURACY_TARGET,
    COVERAGE_TARGET,
    Decisions,
    OperatingPoint,
    _pack_snapshot_sha256,
    calibrate_operating_point,
    decide_probabilities,
    expected_calibration_error,
    fit_temperature,
    load_certification_rows,
    paired_accuracy_delta,
    score_decisions,
    select_candidate,
    softmax_temperature,
)
from .embed import DEFAULT_ARTIFACT_DIR, PACK_DIM, TOKEN_DIM, EmbeddingCache
from .memory import (
    acquire_pipeline_lock,
    validate_offline_working_set_gib,
)
from .train import (
    LinearArtifact,
    MLPArtifact,
    PCAArtifact,
    TrainingExample,
    fit_linear_candidate,
    fit_mlp_candidate,
    fit_pca,
    save_linear_artifact,
    save_mlp_artifact,
    snapshot_training_bundle,
    training_pack_snapshot_sha256,
)

PackLayout = Literal["raw6", "orth5", "global2"]
ModelKind = Literal["linear", "mlp"]

LAYOUT_INPUT_DIMENSIONS: dict[PackLayout, int] = {
    "raw6": 6 * TOKEN_DIM,
    "orth5": 5 * TOKEN_DIM,
    "global2": 2 * TOKEN_DIM,
}
PCA_DIMENSIONS = (128, 256, 384)
DEFAULT_CV_FOLDS = 5
ECE_TARGET = 0.05
ABSTENTION_MULTIPLIER = 1.5
MINIMUM_FRESH_CERTIFICATION = 250

# Eight GiB is ample for the current ~8k pack bank.  The hard cap is one quarter
# of the machine's 64-GiB user constraint, leaving room for the OS and other jobs.
DEFAULT_MEMORY_BUDGET_BYTES = 8 * 1024**3
HARD_MEMORY_CEILING_BYTES = 16 * 1024**3
MEMORY_ESTIMATE_OVERHEAD_BYTES = 512 * 1024**2

DEFAULT_RETIRED_TRUTH_DIR = Path.home() / ".immich-memories-matrix" / "description-truth-2026-08-31"
DEFAULT_TIMESTAMPS = (
    Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30" / "timestamps.json"
)
DEFAULT_COMPACT_METADATA = (
    Path.home() / ".immich-memories-matrix" / "slice4-metadata-2026-08-27" / "cache" / "lib2"
)

_HADAMARD_4 = (
    np.asarray(
        (
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 1.0, -1.0, -1.0),
            (1.0, -1.0, 1.0, -1.0),
            (1.0, -1.0, -1.0, 1.0),
        ),
        dtype=np.float32,
    )
    / 2.0
)


@dataclass(frozen=True, order=True)
class RepresentationSpec:
    layout: PackLayout
    pca_dimensions: int

    def __post_init__(self) -> None:
        if self.layout not in LAYOUT_INPUT_DIMENSIONS:
            raise ValueError(f"unknown cached-pack layout: {self.layout!r}")
        if self.pca_dimensions < 1:
            raise ValueError("PCA dimensions must be positive")
        if self.pca_dimensions > LAYOUT_INPUT_DIMENSIONS[self.layout]:
            raise ValueError("PCA dimensions exceed the layout input dimensions")

    @property
    def name(self) -> str:
        return f"{self.layout}-pca{self.pca_dimensions}"

    @property
    def input_dimensions(self) -> int:
        return LAYOUT_INPUT_DIMENSIONS[self.layout]


@dataclass(frozen=True)
class FittedRepresentation:
    spec: RepresentationSpec
    pca: PCAArtifact

    def transform(self, packs: np.ndarray) -> np.ndarray:
        from .train import project_deployment_features

        base = apply_pack_layout(packs, self.spec.layout)
        return project_deployment_features(self.pca, base)


@dataclass(frozen=True)
class DevelopmentEvaluation:
    metrics: dict[str, Any]
    decisions: Decisions
    fold_reports: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CandidateRun:
    name: str
    model_kind: ModelKind
    representation: FittedRepresentation
    model: LinearArtifact | MLPArtifact
    cv_scores: Mapping[float, float]
    development_metrics: dict[str, Any]
    development_decisions: Decisions
    development_logits: np.ndarray
    final_temperature: float
    final_operating_point: OperatingPoint
    fold_reports: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SweepOutcome:
    selected: CandidateRun
    linear_candidates: tuple[CandidateRun, ...]
    mlp_candidates: tuple[CandidateRun, ...]
    report: dict[str, Any]


class DevelopmentGateMiss(RuntimeError):
    """The sweep completed safely, but no candidate may advance to certification."""

    def __init__(self, report_path: Path) -> None:
        self.report_path = Path(report_path)
        super().__init__(
            "selected representation missed the unchanged development gate; "
            f"failure report: {self.report_path}"
        )


def default_representation_specs() -> tuple[RepresentationSpec, ...]:
    """Return the fixed 3-layout by 3-PCA development grid."""
    return tuple(
        RepresentationSpec(layout, dimensions)
        for layout in ("raw6", "orth5", "global2")
        for dimensions in PCA_DIMENSIONS
    )


def _six_vectors(packs: np.ndarray) -> np.ndarray:
    array = np.asarray(packs, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != PACK_DIM:
        raise ValueError(f"cached packs must have shape [n, {PACK_DIM}], got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError("cached packs contain non-finite values")
    return array.reshape(len(array), 6, TOKEN_DIM)


def apply_pack_layout(packs: np.ndarray, layout: PackLayout) -> np.ndarray:
    """Expose one deterministic view of the existing six cached vectors."""
    if layout not in LAYOUT_INPUT_DIMENSIONS:
        raise ValueError(f"unknown cached-pack layout: {layout!r}")
    vectors = _six_vectors(packs)
    quadrant_mean = vectors[:, 2:6].mean(axis=1)
    if not np.allclose(vectors[:, 1], quadrant_mean, rtol=2e-5, atol=2e-5):
        raise ValueError("cached pack global vector is inconsistent with its quadrant mean")
    if layout == "raw6":
        return vectors.reshape(len(vectors), 6 * TOKEN_DIM)
    if layout == "global2":
        return np.ascontiguousarray(vectors[:, :2].reshape(len(vectors), 2 * TOKEN_DIM))

    # H/2 is orthonormal.  Its first row is the mean direction (scaled by two)
    # and the other rows are independent vertical/horizontal/diagonal contrasts.
    # The explicit global slot is omitted because it duplicates that direction.
    quadrants = np.einsum("vq,nqd->nvd", _HADAMARD_4, vectors[:, 2:6], optimize=True)
    orthogonal = np.concatenate([vectors[:, :1], quadrants], axis=1)
    return np.ascontiguousarray(orthogonal.reshape(len(vectors), 5 * TOKEN_DIM))


def fit_representation(
    training_packs: np.ndarray,
    spec: RepresentationSpec,
) -> FittedRepresentation:
    features = apply_pack_layout(training_packs, spec.layout)
    if spec.pca_dimensions > min(features.shape):
        raise ValueError(f"cannot fit {spec.name} to {features.shape[0]} training rows")
    return FittedRepresentation(
        spec=spec,
        pca=fit_pca(features, components=spec.pca_dimensions),
    )


def estimate_representation_peak_bytes(
    n_training: int,
    n_development: int,
    spec: RepresentationSpec,
) -> int:
    """Conservatively estimate the largest sequential candidate working set."""
    if n_training < 1 or n_development < 1:
        raise ValueError("training and development cohorts must both be non-empty")
    floats = np.dtype(np.float32).itemsize
    held_packs = (n_training + n_development) * PACK_DIM * floats
    # Randomized PCA and sklearn may each make working copies.  Six training
    # layout copies is deliberately pessimistic for this small matrix.
    layout_work = (6 * n_training + 2 * n_development) * spec.input_dimensions * floats
    projection_work = 4 * (n_training + n_development) * spec.pca_dimensions * floats
    pca_work = 12 * spec.input_dimensions * spec.pca_dimensions * floats
    return int(
        held_packs + layout_work + projection_work + pca_work + MEMORY_ESTIMATE_OVERHEAD_BYTES
    )


def validate_memory_budget(required_bytes: int, budget_bytes: int) -> None:
    if budget_bytes < 1:
        raise ValueError("memory budget must be positive")
    if budget_bytes > HARD_MEMORY_CEILING_BYTES:
        raise ValueError(
            "memory budget exceeds the 16-GiB hard ceiling for representation development"
        )
    if required_bytes > budget_bytes:
        raise MemoryError(
            "estimated working set exceeds the configured budget: "
            f"{required_bytes / 1024**3:.2f} GiB > {budget_bytes / 1024**3:.2f} GiB"
        )


def full_location_gate(
    truth: np.ndarray,
    decisions: Decisions,
    *,
    teacher_undetermined_rate: float,
) -> dict[str, Any]:
    """Apply the unchanged 97/85 gate plus the original honesty requirements."""
    if not 0.0 <= teacher_undetermined_rate <= 1.0:
        raise ValueError("teacher undetermined rate must be in [0, 1]")
    metrics = score_decisions(np.asarray(truth, dtype=str), decisions)
    ece = expected_calibration_error(np.asarray(truth, dtype=str), decisions)
    max_emitted = ABSTENTION_MULTIPLIER * teacher_undetermined_rate
    honesty = bool(
        ece is not None
        and ece <= ECE_TARGET + 1e-12
        and metrics["emitted_undetermined_rate"] <= max_emitted + 1e-12
    )
    accuracy = metrics["accuracy_on_covered"]
    passed = bool(
        accuracy is not None
        and accuracy >= ACCURACY_TARGET - 1e-12
        and metrics["coverage"] >= COVERAGE_TARGET - 1e-12
        and honesty
    )
    return {
        **metrics,
        "ece_covered": ece,
        "teacher_undetermined_rate": teacher_undetermined_rate,
        "max_emitted_undetermined_rate": max_emitted,
        "accuracy_target": ACCURACY_TARGET,
        "coverage_target": COVERAGE_TARGET,
        "ece_target": ECE_TARGET,
        "abstention_multiplier": ABSTENTION_MULTIPLIER,
        "abstention_honesty_passed": honesty,
        "gate_passed": passed,
    }


def _grouped_folds(
    truth: np.ndarray,
    groups: np.ndarray,
    *,
    n_splits: int,
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    from sklearn.model_selection import StratifiedGroupKFold

    expected = np.asarray(truth, dtype=str)
    group_array = np.asarray(groups, dtype=str)
    if expected.ndim != 1 or group_array.shape != expected.shape:
        raise ValueError("development truth and groups must be aligned one-dimensional arrays")
    if n_splits < 2:
        raise ValueError("cross-fitting requires at least two folds")
    if len(np.unique(group_array)) < n_splits:
        raise ValueError("development cohort has fewer capture groups than CV folds")
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=42)
    placeholder = np.zeros((len(expected), 1), dtype=np.float32)
    folds = tuple(splitter.split(placeholder, expected, group_array))
    seen = np.zeros(len(expected), dtype=np.int8)
    for calibration, validation in folds:
        if set(group_array[calibration]) & set(group_array[validation]):
            raise AssertionError("capture group leaked across a calibration fold")
        if set(expected[calibration]) != {"indoor", "outdoor", "undetermined"}:
            raise ValueError("a cross-fit calibration fold omits a location class")
        seen[validation] += 1
    if not np.all(seen == 1):
        raise AssertionError("cross-fitting did not score every development row exactly once")
    return folds


def cross_fitted_development_evaluation(
    logits: np.ndarray,
    truth: np.ndarray,
    groups: np.ndarray,
    teacher_labels: np.ndarray,
    classes: tuple[str, ...],
    *,
    n_splits: int = DEFAULT_CV_FOLDS,
) -> DevelopmentEvaluation:
    """Score every retired row using calibration learned from other groups."""
    raw_logits = np.asarray(logits, dtype=np.float32)
    expected = np.asarray(truth, dtype=str)
    group_array = np.asarray(groups, dtype=str)
    teacher = np.asarray(teacher_labels, dtype=str)
    if raw_logits.shape != (len(expected), len(classes)):
        raise ValueError("development logits, truth, and classes do not align")
    if group_array.shape != expected.shape or teacher.shape != expected.shape:
        raise ValueError("development truth, groups, and teacher labels do not align")
    if set(classes) != {"indoor", "outdoor", "undetermined"}:
        raise ValueError(f"unexpected location classes: {classes}")

    labels = np.full(len(expected), "undetermined", dtype="<U12")
    confidence = np.zeros(len(expected), dtype=np.float32)
    covered = np.zeros(len(expected), dtype=bool)
    fold_reports: list[dict[str, Any]] = []
    for fold_index, (calibration, validation) in enumerate(
        _grouped_folds(expected, group_array, n_splits=n_splits)
    ):
        temperature = fit_temperature(raw_logits[calibration], expected[calibration], classes)
        calibration_probabilities = softmax_temperature(raw_logits[calibration], temperature)
        operating_point, _curve = calibrate_operating_point(
            calibration_probabilities,
            expected[calibration],
            classes,
            target_accuracy=ACCURACY_TARGET,
        )
        validation_probabilities = softmax_temperature(raw_logits[validation], temperature)
        decisions = decide_probabilities(validation_probabilities, classes, operating_point)
        labels[validation] = decisions.labels
        confidence[validation] = decisions.confidence
        covered[validation] = decisions.covered
        fold_metrics = score_decisions(expected[validation], decisions)
        fold_reports.append(
            {
                "fold": fold_index,
                "n_calibration": int(len(calibration)),
                "n_validation": int(len(validation)),
                "calibration_groups": int(len(np.unique(group_array[calibration]))),
                "validation_groups": int(len(np.unique(group_array[validation]))),
                "group_overlap_count": 0,
                "temperature": temperature,
                "operating_point": _operating_point_payload(operating_point),
                "validation": fold_metrics,
            }
        )

    oof_decisions = Decisions(labels=labels, confidence=confidence, covered=covered)
    teacher_rate = float(np.mean(teacher == "undetermined"))
    metrics = full_location_gate(
        expected,
        oof_decisions,
        teacher_undetermined_rate=teacher_rate,
    )
    metrics["evaluation_protocol"] = f"{n_splits}-fold-cross-fitted-by-capture-group"
    metrics["n_splits"] = n_splits
    return DevelopmentEvaluation(metrics, oof_decisions, tuple(fold_reports))


def _fit_final_operating_point(
    logits: np.ndarray,
    truth: np.ndarray,
    classes: tuple[str, ...],
) -> tuple[float, OperatingPoint]:
    temperature = fit_temperature(logits, truth, classes)
    probabilities = softmax_temperature(logits, temperature)
    operating_point, _curve = calibrate_operating_point(
        probabilities,
        truth,
        classes,
        target_accuracy=ACCURACY_TARGET,
    )
    return temperature, operating_point


def _candidate_sort_key(candidate: CandidateRun) -> tuple[Any, ...]:
    metrics = candidate.development_metrics
    accuracy = metrics.get("accuracy_on_covered")
    accuracy_value = float(accuracy) if accuracy is not None else 0.0
    coverage = float(metrics.get("coverage", 0.0))
    honesty = bool(metrics.get("abstention_honesty_passed", False))
    passed = bool(metrics.get("gate_passed", False))
    gate_fraction = min(accuracy_value / ACCURACY_TARGET, coverage / COVERAGE_TARGET)
    return (
        -int(passed),
        -int(honesty),
        -gate_fraction,
        -accuracy_value,
        -coverage,
        candidate.representation.spec.pca_dimensions,
        candidate.name,
        candidate.model_kind,
    )


def rank_linear_candidates(candidates: Sequence[CandidateRun]) -> list[CandidateRun]:
    if not candidates:
        raise ValueError("no linear representation candidates were evaluated")
    if any(candidate.model_kind != "linear" for candidate in candidates):
        raise ValueError("linear ranking received a non-linear candidate")
    names = [candidate.name for candidate in candidates]
    if len(set(names)) != len(names):
        raise ValueError("linear representation candidate names must be unique")
    return sorted(candidates, key=_candidate_sort_key)


def validate_mlp_scope(
    requested_names: Sequence[str],
    linear_candidates: Sequence[CandidateRun],
) -> None:
    if len(set(requested_names)) != len(requested_names):
        raise ValueError("MLP representation names must be unique")
    allowed = {candidate.name for candidate in rank_linear_candidates(linear_candidates)[:2]}
    outside = set(requested_names) - allowed
    if outside:
        raise ValueError("MLP may be trained only for the top two linear representations")


def _operating_point_payload(operating_point: OperatingPoint) -> dict[str, Any]:
    return {
        "confidence_thresholds": {
            label: float(value)
            for label, value in sorted(operating_point.confidence_thresholds.items())
        },
        "undetermined_threshold": float(operating_point.undetermined_threshold),
    }


def _candidate_report(candidate: CandidateRun) -> dict[str, Any]:
    return {
        "model_kind": candidate.model_kind,
        "representation": {
            "name": candidate.representation.spec.name,
            "layout": candidate.representation.spec.layout,
            "input_dimensions": candidate.representation.spec.input_dimensions,
            "pca_dimensions": candidate.representation.spec.pca_dimensions,
        },
        "linear_cv_accuracy_by_c": {
            str(value): float(score) for value, score in sorted(candidate.cv_scores.items())
        },
        "development_oof": candidate.development_metrics,
        "folds": list(candidate.fold_reports),
        "frozen_calibration": {
            "fit_evidence": "all-retired-development-rows-after-selection",
            "temperature": float(candidate.final_temperature),
            "operating_point": _operating_point_payload(candidate.final_operating_point),
        },
    }


def run_representation_sweep(
    training_packs: np.ndarray,
    training_labels: np.ndarray,
    training_groups: np.ndarray,
    development_packs: np.ndarray,
    development_truth: np.ndarray,
    development_groups: np.ndarray,
    development_teacher_labels: np.ndarray,
    *,
    specs: Sequence[RepresentationSpec] | None = None,
    n_splits: int = DEFAULT_CV_FOLDS,
    mlp_epochs: int = 100,
    memory_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES,
) -> SweepOutcome:
    """Run all linear layouts and MLPs for only the best two linear layouts."""
    train_packs = np.asarray(training_packs, dtype=np.float32)
    dev_packs = np.asarray(development_packs, dtype=np.float32)
    train_labels = np.asarray(training_labels, dtype=str)
    train_groups = np.asarray(training_groups, dtype=str)
    dev_truth = np.asarray(development_truth, dtype=str)
    dev_groups = np.asarray(development_groups, dtype=str)
    dev_teacher = np.asarray(development_teacher_labels, dtype=str)
    _six_vectors(train_packs)
    _six_vectors(dev_packs)
    if train_labels.shape != (len(train_packs),) or train_groups.shape != train_labels.shape:
        raise ValueError("training packs, labels, and groups do not align")
    if dev_truth.shape != (len(dev_packs),) or dev_groups.shape != dev_truth.shape:
        raise ValueError("development packs, truth, and groups do not align")
    if dev_teacher.shape != dev_truth.shape:
        raise ValueError("development teacher labels do not align")
    if mlp_epochs < 1:
        raise ValueError("MLP epochs must be positive")

    candidate_specs = tuple(specs or default_representation_specs())
    if not candidate_specs or len({spec.name for spec in candidate_specs}) != len(candidate_specs):
        raise ValueError("representation specifications must be non-empty and unique")

    linear_candidates: list[CandidateRun] = []
    for spec in candidate_specs:
        validate_memory_budget(
            estimate_representation_peak_bytes(len(train_packs), len(dev_packs), spec),
            memory_budget_bytes,
        )
        representation = fit_representation(train_packs, spec)
        training_features = representation.transform(train_packs)
        development_features = representation.transform(dev_packs)
        model, cv_scores = fit_linear_candidate(
            training_features,
            train_labels,
            train_groups,
        )
        development_logits = model.logits(development_features)
        evaluation = cross_fitted_development_evaluation(
            development_logits,
            dev_truth,
            dev_groups,
            dev_teacher,
            model.classes,
            n_splits=n_splits,
        )
        temperature, operating_point = _fit_final_operating_point(
            development_logits,
            dev_truth,
            model.classes,
        )
        linear_candidates.append(
            CandidateRun(
                name=spec.name,
                model_kind="linear",
                representation=representation,
                model=model,
                cv_scores=cv_scores,
                development_metrics=evaluation.metrics,
                development_decisions=evaluation.decisions,
                development_logits=development_logits,
                final_temperature=temperature,
                final_operating_point=operating_point,
                fold_reports=evaluation.fold_reports,
            )
        )

    ranked_linear = rank_linear_candidates(linear_candidates)
    mlp_scope = [candidate.name for candidate in ranked_linear[:2]]
    validate_mlp_scope(mlp_scope, linear_candidates)
    mlp_candidates: list[CandidateRun] = []
    for linear in ranked_linear[:2]:
        representation = linear.representation
        training_features = representation.transform(train_packs)
        development_features = representation.transform(dev_packs)
        model = fit_mlp_candidate(
            training_features,
            train_labels,
            hidden_dim=256,
            epochs=mlp_epochs,
            batch_size=256,
        )
        development_logits = model.logits(development_features)
        evaluation = cross_fitted_development_evaluation(
            development_logits,
            dev_truth,
            dev_groups,
            dev_teacher,
            model.classes,
            n_splits=n_splits,
        )
        paired_delta = paired_accuracy_delta(
            dev_truth,
            linear.development_decisions,
            evaluation.decisions,
        )
        architecture_selected = (
            select_candidate(
                linear.development_metrics,
                evaluation.metrics,
                paired_delta=paired_delta,
            )
            == "mlp"
        )
        metrics = {
            **evaluation.metrics,
            "paired_accuracy_delta_mlp_minus_linear": paired_delta,
            "mlp_two_point_rule_passed": architecture_selected,
            "compared_linear_representation": linear.name,
        }
        temperature, operating_point = _fit_final_operating_point(
            development_logits,
            dev_truth,
            model.classes,
        )
        mlp_candidates.append(
            CandidateRun(
                name=linear.name,
                model_kind="mlp",
                representation=representation,
                model=model,
                cv_scores={},
                development_metrics=metrics,
                development_decisions=evaluation.decisions,
                development_logits=development_logits,
                final_temperature=temperature,
                final_operating_point=operating_point,
                fold_reports=evaluation.fold_reports,
            )
        )

    eligible_mlps = [
        candidate
        for candidate in mlp_candidates
        if candidate.development_metrics["mlp_two_point_rule_passed"]
    ]
    selected = sorted([*linear_candidates, *eligible_mlps], key=_candidate_sort_key)[0]
    report = {
        "schema_version": "triage-location-representation-sweep-v1",
        "evidence_role": "retired-certification-development-only",
        "promotion_authorized": False,
        "fresh_certification_required": True,
        "unchanged_gate": {
            "accuracy_on_covered": ACCURACY_TARGET,
            "coverage": COVERAGE_TARGET,
            "ece_covered": ECE_TARGET,
            "emitted_undetermined_multiplier": ABSTENTION_MULTIPLIER,
        },
        "representation_grid": [spec.name for spec in candidate_specs],
        "linear_ranking": [candidate.name for candidate in ranked_linear],
        "mlp_trained_only_for": mlp_scope,
        "linear_candidates": {
            candidate.name: _candidate_report(candidate) for candidate in linear_candidates
        },
        "mlp_candidates": {
            candidate.name: _candidate_report(candidate) for candidate in mlp_candidates
        },
        "selected_development_candidate": {
            "representation": selected.name,
            "model_kind": selected.model_kind,
            "oof_gate_passed": bool(selected.development_metrics["gate_passed"]),
        },
        "selection_warning": (
            "Retired evidence selects and freezes configuration only; it cannot certify or promote."
        ),
    }
    return SweepOutcome(
        selected=selected,
        linear_candidates=tuple(linear_candidates),
        mlp_candidates=tuple(mlp_candidates),
        report=report,
    )


def _canonical_json_bytes(payload: Mapping[str, Any], *, pretty: bool = False) -> bytes:
    if pretty:
        text = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    else:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return text.encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_sha256(value: object, *, field: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _publish_private_json_create_only(path: Path, payload: Mapping[str, Any]) -> Path:
    """Durably create one private JSON record; exact retries are idempotent."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = _canonical_json_bytes(payload, pretty=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != encoded:
                raise RuntimeError(
                    f"refusing to replace different immutable sweep evidence: {destination}"
                )
        destination.chmod(0o600)
        _fsync_directory(destination.parent)
        return destination
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


_PRIVATE_REPORT_KEYS = {
    "asset_id",
    "asset_ids",
    "image_path",
    "image_paths",
    "source_image_path",
    "rows",
}


def _validate_aggregate_only(payload: object) -> None:
    if isinstance(payload, Mapping):
        exposed = set(payload) & _PRIVATE_REPORT_KEYS
        if exposed:
            raise ValueError(
                "failure sweep report exposes row-level fields: " + ", ".join(sorted(exposed))
            )
        for value in payload.values():
            _validate_aggregate_only(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _validate_aggregate_only(value)


def persist_failed_sweep_report(
    candidate_destination: Path,
    outcome: SweepOutcome,
    *,
    training_run_sha256: str,
    training_cohort_sha256: str,
    retired_certification_evidence_sha256: str,
    retired_certification_cohort_sha256: str,
    retired_development_cohort_sha256: str,
    staging_encoder_key: str,
    training_count: int,
    development_count: int,
    development_group_count: int,
    memory_budget_bytes: int,
) -> Path:
    """Persist aggregate diagnostics for a gate miss without publishing a model."""
    if outcome.selected.development_metrics.get("gate_passed") is True:
        raise ValueError("a passing sweep is not failure evidence")
    if outcome.report.get("promotion_authorized") is not False:
        raise ValueError("retired development evidence cannot authorize promotion")
    if outcome.report.get("evidence_role") != "retired-certification-development-only":
        raise ValueError("sweep report does not identify retired development evidence")
    counts = {
        "training": int(training_count),
        "retired_development": int(development_count),
        "development_capture_groups": int(development_group_count),
    }
    if any(value < 1 for value in counts.values()):
        raise ValueError("failure report cohort counts must be positive")
    validate_memory_budget(1, int(memory_budget_bytes))
    lineage = {
        "training_run_sha256": _validate_sha256(training_run_sha256, field="training run digest"),
        "training_cohort_sha256": _validate_sha256(
            training_cohort_sha256, field="training cohort digest"
        ),
        "retired_certification_evidence_sha256": _validate_sha256(
            retired_certification_evidence_sha256,
            field="retired certification evidence digest",
        ),
        "retired_certification_cohort_sha256": _validate_sha256(
            retired_certification_cohort_sha256,
            field="retired certification cohort digest",
        ),
        "retired_development_cohort_sha256": _validate_sha256(
            retired_development_cohort_sha256,
            field="retired development cohort digest",
        ),
        "staging_encoder_key": _validate_sha256(staging_encoder_key, field="staging encoder key"),
    }
    selected = outcome.selected
    report: dict[str, Any] = {
        "schema_version": "triage-location-representation-sweep-failure-v1",
        "status": "development_gate_failed",
        "failure_reason": "selected candidate missed the unchanged development gate",
        "evidence_role": "retired-certification-development-only",
        "promotion_authorized": False,
        "candidate_bundle_created": False,
        "lineage": lineage,
        "cohort_counts": counts,
        "memory_budget_bytes": int(memory_budget_bytes),
        "selected_candidate": {
            "representation": selected.name,
            "model_kind": selected.model_kind,
            "development_oof": selected.development_metrics,
            "final_development_calibration": {
                "temperature": float(selected.final_temperature),
                "operating_point": _operating_point_payload(selected.final_operating_point),
            },
        },
        "sweep": outcome.report,
    }
    _validate_aggregate_only(report)
    digest = _sha256_bytes(_canonical_json_bytes(report))
    report["failure_report_sha256"] = digest
    candidate_destination = Path(candidate_destination)
    report_path = candidate_destination.parent / (
        f"{candidate_destination.name}-failed-{digest}.json"
    )
    return _publish_private_json_create_only(report_path, report)


def validate_failed_sweep_report(path: Path) -> str:
    report_path = Path(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "triage-location-representation-sweep-failure-v1":
        raise ValueError("unsupported failed sweep report schema")
    if report.get("status") != "development_gate_failed":
        raise ValueError("failed sweep report has the wrong status")
    if report.get("promotion_authorized") is not False:
        raise ValueError("failed sweep report claims promotion authority")
    if report.get("candidate_bundle_created") is not False:
        raise ValueError("failed sweep report claims a candidate bundle exists")
    _validate_aggregate_only(report)
    stored = _validate_sha256(report.get("failure_report_sha256"), field="failure report digest")
    unsigned = dict(report)
    unsigned.pop("failure_report_sha256", None)
    if _sha256_bytes(_canonical_json_bytes(unsigned)) != stored:
        raise ValueError("failure sweep report digest does not match its contents")
    lineage = report.get("lineage")
    if not isinstance(lineage, Mapping):
        raise ValueError("failed sweep report lineage is missing")
    expected_lineage = {
        "training_run_sha256",
        "training_cohort_sha256",
        "retired_certification_evidence_sha256",
        "retired_certification_cohort_sha256",
        "retired_development_cohort_sha256",
        "staging_encoder_key",
    }
    if set(lineage) != expected_lineage:
        raise ValueError("failed sweep report lineage is incomplete")
    for field, value in lineage.items():
        _validate_sha256(value, field=field)
    expected_name = f"{report_path.name.split('-failed-', maxsplit=1)[0]}-failed-{stored}.json"
    if report_path.name != expected_name:
        raise ValueError("failure sweep report path does not match its content digest")
    return stored


def _save_representation(path: Path, representation: FittedRepresentation) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(
            handle,
            artifact_version=np.array("triage-cache-representation-v1"),
            layout=np.array(representation.spec.layout),
            pca_dimensions=np.array(representation.spec.pca_dimensions, dtype=np.int64),
            mean=representation.pca.mean,
            components=representation.pca.components,
            explained_variance=representation.pca.explained_variance,
        )
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def freeze_candidate_bundle(
    destination: Path,
    outcome: SweepOutcome,
    *,
    training_cohort_sha256: str,
    retired_development_cohort_sha256: str,
    staging_encoder_key: str,
) -> dict[str, Any]:
    """Atomically publish a create-only, explicitly non-promotable candidate."""
    if outcome.report.get("promotion_authorized") is not False:
        raise ValueError("retired development evidence cannot authorize promotion")
    if outcome.report.get("evidence_role") != "retired-certification-development-only":
        raise ValueError("sweep report does not identify retired development evidence")
    if outcome.selected.development_metrics.get("gate_passed") is not True:
        raise ValueError("selected candidate did not pass the unchanged development gate")
    training_digest = _validate_sha256(training_cohort_sha256, field="training cohort digest")
    retired_digest = _validate_sha256(
        retired_development_cohort_sha256,
        field="retired development cohort digest",
    )
    encoder_key = _validate_sha256(staging_encoder_key, field="staging encoder key")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"frozen candidate destination already exists: {destination}")

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    staging.chmod(0o700)
    try:
        representation_path = staging / "representation.npz"
        model_path = staging / "model.npz"
        report_path = staging / "sweep-report.json"
        _save_representation(representation_path, outcome.selected.representation)
        if outcome.selected.model_kind == "linear":
            if not isinstance(outcome.selected.model, LinearArtifact):
                raise TypeError("selected linear candidate has the wrong artifact type")
            save_linear_artifact(model_path, outcome.selected.model)
        else:
            if not isinstance(outcome.selected.model, MLPArtifact):
                raise TypeError("selected MLP candidate has the wrong artifact type")
            save_mlp_artifact(model_path, outcome.selected.model)
        model_path.chmod(0o600)
        _write_private_bytes(report_path, _canonical_json_bytes(outcome.report, pretty=True))
        component_paths = {
            "model.npz": model_path,
            "representation.npz": representation_path,
            "sweep-report.json": report_path,
        }
        component_sha256 = {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(component_paths.items())
        }
        selected = outcome.selected
        manifest: dict[str, Any] = {
            "schema_version": "triage-location-frozen-candidate-v1",
            "evidence_role": "retired-certification-development-only",
            "promotion_authorized": False,
            "staging_encoder_key": encoder_key,
            "training_cohort_sha256": training_digest,
            "retired_development_cohort_sha256": retired_digest,
            "components_sha256": component_sha256,
            "candidate": {
                "head_name": "location",
                "model_kind": selected.model_kind,
                "classes": list(selected.model.classes),
                "representation": selected.name,
                "layout": selected.representation.spec.layout,
                "pca_dimensions": selected.representation.spec.pca_dimensions,
                "temperature": float(selected.final_temperature),
                "operating_point": _operating_point_payload(selected.final_operating_point),
            },
            "fresh_certification": {
                "required": True,
                "minimum_usable": MINIMUM_FRESH_CERTIFICATION,
                "forbidden_cohort_sha256": retired_digest,
                "accuracy_target": ACCURACY_TARGET,
                "coverage_target": COVERAGE_TARGET,
                "ece_target": ECE_TARGET,
                "emitted_undetermined_multiplier": ABSTENTION_MULTIPLIER,
            },
        }
        manifest["candidate_seal_sha256"] = _sha256_bytes(_canonical_json_bytes(manifest))
        _write_private_bytes(
            staging / "candidate.json",
            _canonical_json_bytes(manifest, pretty=True),
        )
        _fsync_directory(staging)
        os.rename(staging, destination)
        _fsync_directory(destination.parent)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def publish_sweep_outcome(
    candidate_destination: Path,
    outcome: SweepOutcome,
    *,
    training_run_sha256: str,
    training_cohort_sha256: str,
    retired_certification_evidence_sha256: str,
    retired_certification_cohort_sha256: str,
    retired_development_cohort_sha256: str,
    staging_encoder_key: str,
    training_count: int,
    development_count: int,
    development_group_count: int,
    memory_budget_bytes: int,
) -> dict[str, Any]:
    """Commit either failure evidence or a certifiable frozen candidate, never both."""
    if outcome.selected.development_metrics.get("gate_passed") is not True:
        report_path = persist_failed_sweep_report(
            candidate_destination,
            outcome,
            training_run_sha256=training_run_sha256,
            training_cohort_sha256=training_cohort_sha256,
            retired_certification_evidence_sha256=retired_certification_evidence_sha256,
            retired_certification_cohort_sha256=retired_certification_cohort_sha256,
            retired_development_cohort_sha256=retired_development_cohort_sha256,
            staging_encoder_key=staging_encoder_key,
            training_count=training_count,
            development_count=development_count,
            development_group_count=development_group_count,
            memory_budget_bytes=memory_budget_bytes,
        )
        raise DevelopmentGateMiss(report_path)
    return freeze_candidate_bundle(
        candidate_destination,
        outcome,
        training_cohort_sha256=training_cohort_sha256,
        retired_development_cohort_sha256=retired_development_cohort_sha256,
        staging_encoder_key=staging_encoder_key,
    )


def validate_frozen_candidate(destination: Path) -> str:
    root = Path(destination)
    manifest_path = root / "candidate.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "triage-location-frozen-candidate-v1":
        raise ValueError("unsupported frozen candidate schema")
    if manifest.get("evidence_role") != "retired-certification-development-only":
        raise ValueError("frozen candidate evidence role is invalid")
    if manifest.get("promotion_authorized") is not False:
        raise ValueError("retired development candidate claims promotion authority")
    stored_seal = _validate_sha256(manifest.get("candidate_seal_sha256"), field="candidate seal")
    unsigned = dict(manifest)
    unsigned.pop("candidate_seal_sha256", None)
    if _sha256_bytes(_canonical_json_bytes(unsigned)) != stored_seal:
        raise ValueError("candidate seal does not match the frozen configuration")
    expected_components = {"model.npz", "representation.npz", "sweep-report.json"}
    components = manifest.get("components_sha256")
    if not isinstance(components, dict) or set(components) != expected_components:
        raise ValueError("frozen candidate component set is incomplete")
    for name in sorted(expected_components):
        expected = _validate_sha256(components[name], field=f"{name} digest")
        observed = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if observed != expected:
            raise ValueError(f"component digest mismatch: {name}")
    report = json.loads((root / "sweep-report.json").read_text(encoding="utf-8"))
    if report.get("evidence_role") != "retired-certification-development-only":
        raise ValueError("sweep report evidence role is invalid")
    if report.get("promotion_authorized") is not False:
        raise ValueError("sweep report claims promotion authority")
    fresh = manifest.get("fresh_certification")
    if not isinstance(fresh, dict) or fresh.get("required") is not True:
        raise ValueError("frozen candidate does not require fresh certification")
    if int(fresh.get("minimum_usable", 0)) < MINIMUM_FRESH_CERTIFICATION:
        raise ValueError("fresh certification cohort minimum was weakened")
    return stored_seal


def validate_fresh_certification_cohort(
    manifest: Mapping[str, Any],
    fresh_cohort_sha256: str,
) -> None:
    stored_seal = _validate_sha256(manifest.get("candidate_seal_sha256"), field="candidate seal")
    unsigned = dict(manifest)
    unsigned.pop("candidate_seal_sha256", None)
    if _sha256_bytes(_canonical_json_bytes(unsigned)) != stored_seal:
        raise ValueError("candidate seal does not match the frozen configuration")
    if manifest.get("promotion_authorized") is not False:
        raise ValueError("retired development candidate claims promotion authority")
    fresh_digest = _validate_sha256(fresh_cohort_sha256, field="fresh cohort digest")
    fresh = manifest.get("fresh_certification")
    if not isinstance(fresh, Mapping) or fresh.get("required") is not True:
        raise ValueError("candidate was not frozen for fresh certification")
    retired_digest = _validate_sha256(
        fresh.get("forbidden_cohort_sha256"),
        field="retired development cohort digest",
    )
    if fresh_digest == retired_digest:
        raise ValueError("fresh certification reuses the retired development cohort")


def cohort_sha256(
    asset_ids: Sequence[str],
    packs: np.ndarray,
    labels: Sequence[str],
    groups: Sequence[str],
    *,
    teacher_labels: Sequence[str] | None = None,
) -> str:
    """Bind private cohort membership and exact cached bytes without publishing rows."""
    ids = np.asarray(asset_ids, dtype=str)
    matrix = np.asarray(packs, dtype=np.float32)
    truth = np.asarray(labels, dtype=str)
    group_array = np.asarray(groups, dtype=str)
    teacher = None if teacher_labels is None else np.asarray(teacher_labels, dtype=str)
    _six_vectors(matrix)
    if ids.shape != truth.shape or group_array.shape != truth.shape or len(matrix) != len(ids):
        raise ValueError("cohort ids, packs, labels, and groups do not align")
    if teacher is not None and teacher.shape != truth.shape:
        raise ValueError("cohort teacher labels do not align")
    if len(set(ids.tolist())) != len(ids):
        raise ValueError("cohort asset ids must be unique")
    rows = []
    for index in np.argsort(ids, kind="stable"):
        pack = np.ascontiguousarray(matrix[index], dtype="<f4")
        row = {
            "asset_id": str(ids[index]),
            "group": str(group_array[index]),
            "label": str(truth[index]),
            "pack_sha256": hashlib.sha256(pack.tobytes()).hexdigest(),
        }
        if teacher is not None:
            row["teacher_label"] = str(teacher[index])
        rows.append(row)
    return _sha256_bytes(
        _canonical_json_bytes({"schema": "triage-location-sweep-cohort-v1", "rows": rows})
    )


def _capture_day(timestamp: str) -> str:
    value = timestamp.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError as error:
        raise ValueError(f"invalid capture timestamp: {timestamp!r}") from error


def load_capture_day_groups(
    asset_ids: Sequence[str],
    timestamps_path: Path,
    compact_metadata_dir: Path,
) -> dict[str, str]:
    """Resolve every retired row to a capture day without using its truth label."""
    needed = set(asset_ids)
    if len(needed) != len(asset_ids):
        raise ValueError("development asset ids must be unique")
    groups: dict[str, str] = {}
    if timestamps_path.is_file():
        payload = json.loads(timestamps_path.read_text(encoding="utf-8"))
        for asset_id, timestamp in payload.get("timestamps", {}).items():
            if asset_id in needed:
                groups[asset_id] = _capture_day(str(timestamp))
    remaining = needed - set(groups)
    if remaining and compact_metadata_dir.is_dir():
        for path in sorted(compact_metadata_dir.glob("*.json")):
            rows = json.loads(path.read_text(encoding="utf-8"))
            for row in rows:
                asset_id = str(row.get("id", ""))
                if asset_id in remaining and row.get("t"):
                    groups[asset_id] = _capture_day(str(row["t"]))
                    remaining.remove(asset_id)
            if not remaining:
                break
    if remaining:
        raise ValueError(f"{len(remaining)} development assets lack capture-day groups")
    return groups


def _load_training_manifest(path: Path) -> list[dict[str, str]]:
    return _load_training_manifest_bytes(path.read_bytes())


def _load_training_manifest_bytes(payload: bytes) -> list[dict[str, str]]:
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
    if not rows or len({row["asset_id"] for row in rows}) != len(rows):
        raise ValueError("training manifest is empty or contains duplicate assets")
    return rows


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--cache-db", type=Path, default=None)
    parser.add_argument("--retired-truth-dir", type=Path, default=DEFAULT_RETIRED_TRUTH_DIR)
    parser.add_argument("--timestamps", type=Path, default=DEFAULT_TIMESTAMPS)
    parser.add_argument("--compact-metadata-dir", type=Path, default=DEFAULT_COMPACT_METADATA)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--folds", type=int, default=DEFAULT_CV_FOLDS)
    parser.add_argument("--mlp-epochs", type=int, default=100)
    parser.add_argument("--max-working-set-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.folds < 2:
        parser.error("--folds must be at least 2")
    if args.mlp_epochs < 1:
        parser.error("--mlp-epochs must be positive")
    try:
        validate_offline_working_set_gib(args.max_working_set_gib)
    except ValueError as error:
        parser.error(str(error))
    return args


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


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    cache_path = args.cache_db or args.artifact_dir / "embeddings.db"
    try:
        _artifact_lock = _acquire_artifact_lock(
            args.artifact_dir,
            cache_db=cache_path,
            shared_root=DEFAULT_ARTIFACT_DIR,
        )
    except RuntimeError as error:
        raise SystemExit(f"STOP: {error}") from error
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "4")
    budget = int(args.max_working_set_gib * 1024**3)
    validate_memory_budget(1, budget)

    embed_meta_path = args.artifact_dir / "embed-meta-cpu.json"
    certification_metrics_path = args.artifact_dir / "certification-metrics.json"
    required = (embed_meta_path, certification_metrics_path)
    if any(not path.is_file() for path in required):
        raise SystemExit("STOP: the sealed Slice 1 training/certification inputs are incomplete")

    try:
        training_bundle = snapshot_training_bundle(args.artifact_dir)
    except (OSError, ValueError) as error:
        raise SystemExit(f"STOP: sealed Slice 1 training bundle is invalid: {error}") from error
    manifest = _load_training_manifest_bytes(training_bundle.component_bytes["training_manifest"])
    train_metrics = training_bundle.report
    embed_meta = json.loads(embed_meta_path.read_text(encoding="utf-8"))
    old_certification = json.loads(certification_metrics_path.read_text(encoding="utf-8"))
    training_run_digest = training_bundle.training_run_sha256
    staging_encoder_key = str(train_metrics.get("staging_encoder_key", ""))
    if staging_encoder_key != str(embed_meta.get("encoder_key", "")):
        raise SystemExit("STOP: training and cached six-vector encoder lineage do not match")

    cache = EmbeddingCache(cache_path)
    training_ids = [row["asset_id"] for row in manifest]
    training_snapshot = cache.staging_snapshot(training_ids, staging_encoder_key)
    if len(training_snapshot) != len(manifest):
        raise SystemExit("STOP: cached six-vector packs do not cover the training manifest")
    examples = [
        TrainingExample(
            asset_id=row["asset_id"],
            label=row["location"],
            group_key=row["group_key"],
            preview_sha256=row["preview_sha256"],
            source_updated=row["source_updated"],
        )
        for row in manifest
    ]
    training_pack_digest = training_pack_snapshot_sha256(
        examples,
        {row["asset_id"]: row["split"] for row in manifest},
        {asset_id: value[0] for asset_id, value in training_snapshot.items()},
    )
    if training_pack_digest != train_metrics.get("training_pack_snapshot_sha256"):
        raise SystemExit("STOP: cached training packs changed after the sealed Slice 1 run")

    retired_rows, _missing = load_certification_rows(
        args.retired_truth_dir / "battery_key.json",
        args.retired_truth_dir / "battery_shards",
        args.retired_truth_dir / "review_data.json",
    )
    retired_ids = [row.asset_id for row in retired_rows]
    retired_snapshot = cache.staging_snapshot(retired_ids, staging_encoder_key)
    if len(retired_snapshot) != len(retired_rows):
        raise SystemExit("STOP: cached six-vector packs do not cover retired development truth")
    original_retired_digest = _pack_snapshot_sha256(
        retired_snapshot,
        metadata_by_asset={
            row.asset_id: {
                "truth_location": row.location,
                "teacher_location": row.teacher_location,
            }
            for row in retired_rows
        },
    )
    if original_retired_digest != old_certification.get("certification_cohort_sha256"):
        raise SystemExit("STOP: retired development evidence differs from the sealed failed run")
    retired_certification_evidence_digest = _validate_sha256(
        old_certification.get("certification_evidence_sha256"),
        field="retired certification evidence digest",
    )

    group_by_asset = load_capture_day_groups(
        retired_ids,
        args.timestamps,
        args.compact_metadata_dir,
    )
    training_packs = np.stack([training_snapshot[asset_id][0] for asset_id in training_ids])
    retired_packs = np.stack([retired_snapshot[asset_id][0] for asset_id in retired_ids])
    training_labels = np.asarray([row["location"] for row in manifest])
    training_groups = np.asarray([row["group_key"] for row in manifest])
    retired_truth = np.asarray([row.location for row in retired_rows])
    retired_teacher = np.asarray([row.teacher_location for row in retired_rows])
    retired_groups = np.asarray([group_by_asset[asset_id] for asset_id in retired_ids])
    retired_sweep_digest = cohort_sha256(
        retired_ids,
        retired_packs,
        retired_truth,
        retired_groups,
        teacher_labels=retired_teacher,
    )

    outcome = run_representation_sweep(
        training_packs,
        training_labels,
        training_groups,
        retired_packs,
        retired_truth,
        retired_groups,
        retired_teacher,
        n_splits=args.folds,
        mlp_epochs=args.mlp_epochs,
        memory_budget_bytes=budget,
    )
    output_dir = args.output_dir or (args.artifact_dir / "recovery-1b" / "representation-sweep-v1")
    try:
        manifest_payload = publish_sweep_outcome(
            output_dir,
            outcome,
            training_run_sha256=training_run_digest,
            training_cohort_sha256=training_pack_digest,
            retired_certification_evidence_sha256=(retired_certification_evidence_digest),
            retired_certification_cohort_sha256=original_retired_digest,
            retired_development_cohort_sha256=retired_sweep_digest,
            staging_encoder_key=staging_encoder_key,
            training_count=len(training_ids),
            development_count=len(retired_ids),
            development_group_count=len(set(retired_groups.tolist())),
            memory_budget_bytes=budget,
        )
    except DevelopmentGateMiss as error:
        raise SystemExit(f"STOP: {error}") from error
    print(
        "development sweep frozen: "
        f"candidate={outcome.selected.model_kind}/{outcome.selected.name} "
        f"seal={manifest_payload['candidate_seal_sha256']} promotion_authorized=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
