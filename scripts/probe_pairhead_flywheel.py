#!/usr/bin/env python3
"""Flywheel simulation: does hard-region data grow band coverage faster than random data?

Lever 3 measurement for the pairwise-head abstention band. Reuses
scripts/probe_pairhead_cascade.py's feature construction (PCA(128) symmetric
pair features) and leakage-free connected-component train/cal/test split
(seed 42) by importing it directly, so results are comparable -- the test
split used here is byte-identical to the one behind curve.json, and it is
never trained or calibrated on anywhere in this script.

Three measurements, all scored against that one fixed test split:

  1. Learning curve: nested random subsets of the train+cal pool (10/25/50/
     75/100%), each refit + recalibrated, with a threshold re-derived per
     subset (per agreement target) to hold 95% and 98% agreement on that
     subset's own held-out calibration slice.
  2. Targeted-vs-random increment: a 50%-of-pool base model's abstained
     ("hard region") pairs in the other 50%, defined at its own 98% band,
     added back in vs. an equal-size random sample from the same 50% --
     the gap is the flywheel premium, reported at both bands.
  3. Time-ordered check: earliest 50% of the pool by answered_at vs. the
     random 50%/100% points from (1).

Writes flywheel-report.json and flywheel-report.md into --matrix-dir.
Read-only against pairs.jsonl, embeddings.npy, ids.json, and pca.pkl.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
SUBSET_FRACTIONS = (0.10, 0.25, 0.50, 0.75, 1.00)
CAL_SLICE_FRACTION = 0.125  # matches the base split's cal share of the pool (639 / 5107 ~= 0.125)
AGREEMENT_TARGETS = (0.95, 0.98)
HARD_REGION_CAP_FRACTION = 0.25
RNG_SEED = 42


def _band_key(target: float) -> str:
    return f"{round(target * 100)}pct"


BAND_KEYS = tuple(_band_key(t) for t in AGREEMENT_TARGETS)


def _within_matrix(path: Path) -> bool:
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    try:
        path.resolve().relative_to(matrix)
    except ValueError:
        return False
    return True


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    return args


def _load_cascade_module() -> ModuleType:
    """Import probe_pairhead_cascade.py from scripts/ so feature/split code is identical, not re-derived.

    Uses a plain sys.path import rather than importlib.util.spec_from_file_location:
    the latter doesn't register the module in sys.modules before exec, which breaks
    the cascade module's `@dataclass` decorator (it looks itself up via sys.modules).
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import probe_pairhead_cascade as cascade_module

    return cascade_module


def _load_answered_at(matrix_dir: Path) -> np.ndarray:
    """Read answered_at in file order, aligned 1:1 with cascade.load_pairs (same file, same order)."""
    timestamps = []
    with (matrix_dir / "pairs.jsonl").open() as fh:
        for line in fh:
            timestamps.append(json.loads(line)["answered_at"])
    return np.array(timestamps)


# --- shared fit/calibrate/evaluate routine -----------------------------------


@dataclass
class FitResult:
    model: Any
    metrics: dict[str, Any]


def _band_with_threshold(curve: list[dict[str, Any]], target: float) -> dict[str, Any]:
    """Like cascade.coverage_at_agreement, but always includes the threshold
    (even when the target isn't reached), so callers can apply the same
    best-effort operating point to a different split."""
    reachable = [p for p in curve if p["agreement"] is not None and p["agreement"] >= target]
    if reachable:
        best = max(reachable, key=lambda p: p["coverage"])
        return {
            "reached": True,
            "threshold": best["threshold"],
            "coverage": best["coverage"],
            "agreement": best["agreement"],
        }
    best = max(curve, key=lambda p: p["agreement"] or 0.0)
    return {
        "reached": False,
        "threshold": best["threshold"],
        "coverage": best["coverage"],
        "agreement": best["agreement"],
    }


def fit_calibrate_evaluate(
    pool_features: np.ndarray,
    pool_labels: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    cascade: ModuleType,
    seed: int,
) -> FitResult:
    """Split pool into an internal train/cal slice, fit+calibrate, pick a threshold
    per agreement target that holds it on the cal slice, then apply to the fixed test set."""
    idx = np.arange(len(pool_labels))
    train_idx, cal_idx = train_test_split(
        idx, test_size=CAL_SLICE_FRACTION, random_state=seed, stratify=pool_labels
    )
    train_x, train_y = pool_features[train_idx], pool_labels[train_idx]
    cal_x, cal_y = pool_features[cal_idx], pool_labels[cal_idx]

    best_c, _ = cascade.choose_c(train_x, train_y)
    calibrated = cascade.train_and_calibrate(train_x, train_y, cal_x, cal_y, best_c)

    p_cal = calibrated.predict_proba(cal_x)[:, 1]
    cal_curve = cascade.cascade_curve(cal_y, p_cal)

    p_test = calibrated.predict_proba(test_x)[:, 1]
    predicted_same = p_test >= 0.5
    test_accuracy = float(np.mean(predicted_same == test_y))
    auc = float(roc_auc_score(test_y, p_test))

    bands: dict[str, Any] = {}
    for target in AGREEMENT_TARGETS:
        band = _band_with_threshold(cal_curve, target)
        threshold = band["threshold"]
        covered_test = (p_test >= threshold) | (p_test <= 1 - threshold)
        n_covered_test = int(covered_test.sum())
        test_agreement = (
            float(np.mean(predicted_same[covered_test] == test_y[covered_test]))
            if n_covered_test
            else None
        )
        bands[_band_key(target)] = {
            "target_agreement": target,
            "cal_reached_target": band["reached"],
            "cal_agreement_achieved": band["agreement"],
            "cal_coverage": band["coverage"],
            "threshold": threshold,
            "test_coverage": n_covered_test / len(test_y),
            "test_agreement": test_agreement,
        }

    metrics = {
        "pool_size": int(len(pool_labels)),
        "train_size": int(len(train_idx)),
        "cal_size": int(len(cal_idx)),
        "chosen_c": best_c,
        "test_accuracy": test_accuracy,
        "test_auc": auc,
        "bands": bands,
    }
    return FitResult(model=calibrated, metrics=metrics)


# --- part 1: learning curve ---------------------------------------------------


def learning_curve(
    features: np.ndarray,
    labels: np.ndarray,
    pool_order: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    cascade: ModuleType,
) -> dict[float, FitResult]:
    results: dict[float, FitResult] = {}
    n_pool = len(pool_order)
    for frac in SUBSET_FRACTIONS:
        n = int(round(frac * n_pool))
        subset_idx = pool_order[:n]
        print(f"  fitting pool_fraction={frac:.0%} (n={n})...", flush=True)
        result = fit_calibrate_evaluate(
            features[subset_idx], labels[subset_idx], test_x, test_y, cascade, RNG_SEED
        )
        result.metrics["pool_fraction"] = frac
        results[frac] = result
    return results


# --- part 2: targeted vs random increment ------------------------------------


def targeted_vs_random(
    features: np.ndarray,
    labels: np.ndarray,
    pool_order: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    cascade: ModuleType,
    base_fit: FitResult,
) -> dict[str, Any]:
    n_pool = len(pool_order)
    half = int(round(0.50 * n_pool))  # matches learning_curve's frac=0.50 sizing exactly
    base_idx = pool_order[:half]
    remaining_idx = pool_order[half:]
    if base_fit.metrics["pool_size"] != len(base_idx):
        raise AssertionError(
            "base_fit must be the learning curve's 50% point (same pool_order prefix)"
        )

    hard_threshold = base_fit.metrics["bands"]["98pct"]["threshold"]
    remaining_x = features[remaining_idx]
    p_remaining = base_fit.model.predict_proba(remaining_x)[:, 1]
    covered = (p_remaining >= hard_threshold) | (p_remaining <= 1 - hard_threshold)
    hard_positions = np.where(~covered)[0]
    hard_count = len(hard_positions)

    cap = int(round(HARD_REGION_CAP_FRACTION * n_pool))
    n_increment = min(hard_count, cap)

    rng = np.random.RandomState(RNG_SEED)
    hard_sample_positions = (
        rng.choice(hard_positions, size=n_increment, replace=False)
        if hard_count > n_increment
        else hard_positions
    )
    random_sample_positions = rng.choice(len(remaining_idx), size=n_increment, replace=False)

    targeted_idx = np.concatenate([base_idx, remaining_idx[hard_sample_positions]])
    random_idx = np.concatenate([base_idx, remaining_idx[random_sample_positions]])

    print(
        f"  hard region: {hard_count}/{len(remaining_idx)} pairs; increment N={n_increment}",
        flush=True,
    )
    targeted_result = fit_calibrate_evaluate(
        features[targeted_idx], labels[targeted_idx], test_x, test_y, cascade, RNG_SEED
    )
    random_result = fit_calibrate_evaluate(
        features[random_idx], labels[random_idx], test_x, test_y, cascade, RNG_SEED
    )

    premium = {
        band: (
            targeted_result.metrics["bands"][band]["test_coverage"]
            - random_result.metrics["bands"][band]["test_coverage"]
        )
        for band in BAND_KEYS
    }

    return {
        "pool_size": n_pool,
        "base_size": int(len(base_idx)),
        "hard_region_available": int(hard_count),
        "hard_region_fraction_of_remaining": hard_count / len(remaining_idx),
        "increment_size_n": int(n_increment),
        "capped_at_25pct_of_pool": hard_count > cap,
        "base": base_fit.metrics,
        "plus_targeted": targeted_result.metrics,
        "plus_random": random_result.metrics,
        "flywheel_premium_coverage_points": premium,
    }


# --- part 3: time-ordered realism check ---------------------------------------


def time_ordered_check(
    features: np.ndarray,
    labels: np.ndarray,
    answered_at: np.ndarray,
    pool_idx: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    cascade: ModuleType,
    random_50_metrics: dict[str, Any],
    full_pool_metrics: dict[str, Any],
) -> dict[str, Any]:
    pool_times = answered_at[pool_idx]
    order_by_time = pool_idx[np.argsort(pool_times, kind="stable")]
    half = len(order_by_time) // 2
    early_idx = order_by_time[:half]

    print(f"  fitting earliest 50% by answered_at (n={len(early_idx)})...", flush=True)
    early_result = fit_calibrate_evaluate(
        features[early_idx], labels[early_idx], test_x, test_y, cascade, RNG_SEED
    )

    delta = {
        band: (
            early_result.metrics["bands"][band]["test_coverage"]
            - random_50_metrics["bands"][band]["test_coverage"]
        )
        for band in BAND_KEYS
    }

    return {
        "earliest_50pct": early_result.metrics,
        "random_50pct_reference": random_50_metrics,
        "full_pool_reference_100pct": full_pool_metrics,
        "chronological_minus_random_coverage_points": delta,
    }


# --- report --------------------------------------------------------------------


def _fmt_pct(x: float | None) -> str:
    return f"{x:.1%}" if x is not None else "n/a"


def render_report(payload: dict[str, Any]) -> str:
    split = payload["base_split"]
    lines = [
        "# Pairwise-Head Flywheel Simulation",
        "",
        "Lever 3 measurement: does retraining on abstained-then-answered pairs shrink "
        "the abstention band faster than the same amount of random data? Same feature "
        "construction (128-d PCA, symmetric pair features) and leakage-free "
        f"connected-component train/cal/test split (seed {split['seed']}) as "
        "scripts/probe_pairhead_cascade.py -- the "
        f"{split['test_pairs']}-pair test split is byte-identical to curve.json's and "
        "is never trained or calibrated on anywhere in this file.",
        "",
        "Operating point: for every model below, the 95% and 98% agreement thresholds "
        "are picked fresh on THAT model's own calibration slice (never on test), then "
        "applied once to the fixed test set. This is stricter than report.md's headline "
        "(which scanned the test set directly for its threshold) -- treat these as the "
        "honest, deployment-realistic numbers for the same question.",
        "",
        f"## 1. Learning curve (nested random subsets of the {split['pool_pairs']}-pair pool)",
        "",
        "| pool % | pairs (train/cal) | test acc | test AUC | @95% coverage | @95% agreement | "
        "@98% coverage | @98% agreement |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in payload["learning_curve"]:
        b95, b98 = m["bands"]["95pct"], m["bands"]["98pct"]
        lines.append(
            f"| {m['pool_fraction']:.0%} | {m['pool_size']} ({m['train_size']}/{m['cal_size']}) | "
            f"{m['test_accuracy']:.3f} | {m['test_auc']:.3f} | "
            f"{_fmt_pct(b95['test_coverage'])} | {_fmt_pct(b95['test_agreement'])} | "
            f"{_fmt_pct(b98['test_coverage'])} | {_fmt_pct(b98['test_agreement'])} |"
        )

    fw = payload["targeted_vs_random"]
    lines += [
        "",
        "## 2. Targeted vs random increment (the flywheel premium)",
        "",
        f"Base model: 50% of pool ({fw['base_size']} pairs). Scored the other "
        f"{fw['pool_size'] - fw['base_size']} pairs at the base model's own 98%-band "
        f"threshold: {fw['hard_region_available']} pairs "
        f"({fw['hard_region_fraction_of_remaining']:.1%} of the held-out half) fell in "
        f'the abstain region (the "hard region"). Increment size N = '
        f"{fw['increment_size_n']}{' (capped at 25% of pool)' if fw['capped_at_25pct_of_pool'] else ''}.",
        "",
        "| model | pairs | @95% coverage | @95% agreement | @98% coverage | @98% agreement |",
        "|---|---|---|---|---|---|",
    ]
    for label, m in (
        ("base (50%)", fw["base"]),
        ("+targeted (hard region)", fw["plus_targeted"]),
        ("+random", fw["plus_random"]),
    ):
        b95, b98 = m["bands"]["95pct"], m["bands"]["98pct"]
        lines.append(
            f"| {label} | {m['pool_size']} | {_fmt_pct(b95['test_coverage'])} | {_fmt_pct(b95['test_agreement'])} | "
            f"{_fmt_pct(b98['test_coverage'])} | {_fmt_pct(b98['test_agreement'])} |"
        )
    premium = fw["flywheel_premium_coverage_points"]
    lines += [
        "",
        f"Flywheel premium (+targeted minus +random coverage): "
        f"{premium['95pct'] * 100:+.1f} points @95%, {premium['98pct'] * 100:+.1f} points @98%.",
    ]

    tm = payload["time_ordered"]
    early, rand50, full = (
        tm["earliest_50pct"],
        tm["random_50pct_reference"],
        tm["full_pool_reference_100pct"],
    )
    delta = tm["chronological_minus_random_coverage_points"]
    lines += [
        "",
        "## 3. Time-ordered realism check",
        "",
        "| slice | pairs | @95% coverage | @98% coverage |",
        "|---|---|---|---|",
        f"| earliest 50% (by answered_at) | {early['pool_size']} | "
        f"{_fmt_pct(early['bands']['95pct']['test_coverage'])} | {_fmt_pct(early['bands']['98pct']['test_coverage'])} |",
        f"| random 50% (learning-curve reference) | {rand50['pool_size']} | "
        f"{_fmt_pct(rand50['bands']['95pct']['test_coverage'])} | {_fmt_pct(rand50['bands']['98pct']['test_coverage'])} |",
        f"| full pool 100% (reference) | {full['pool_size']} | "
        f"{_fmt_pct(full['bands']['95pct']['test_coverage'])} | {_fmt_pct(full['bands']['98pct']['test_coverage'])} |",
        "",
        f"Chronological-minus-random coverage at equal volume (50%): "
        f"{delta['95pct'] * 100:+.1f} points @95%, {delta['98pct'] * 100:+.1f} points @98%.",
        "",
        f"Full numbers: flywheel-report.json. Wall time: {payload['wall_time_seconds']:.1f}s.",
    ]
    return "\n".join(lines) + "\n"


# --- orchestration --------------------------------------------------------------


def main() -> int:
    args = _arguments()
    started = time.monotonic()
    cascade = _load_cascade_module()
    matrix_dir = args.matrix_dir

    embeddings = np.load(matrix_dir / "embeddings.npy")
    ids: list[str] = json.loads((matrix_dir / "ids.json").read_text())
    id_to_row = {asset_id: row for row, asset_id in enumerate(ids)}
    pairs = cascade.load_pairs(matrix_dir)
    answered_at = _load_answered_at(matrix_dir)
    if len(answered_at) != len(pairs):
        raise AssertionError(f"answered_at count {len(answered_at)} != pairs count {len(pairs)}")

    with (matrix_dir / "pca.pkl").open("rb") as fh:
        pca = pickle.load(fh)
    embeddings_pca = pca.transform(embeddings).astype(np.float32)
    features, labels = cascade.build_features(pairs, id_to_row, embeddings_pca)

    component_of = cascade.connected_components(pairs, id_to_row)
    split_of = cascade.assign_splits(pairs, id_to_row, component_of)
    cascade.verify_no_leakage(pairs, split_of)
    split_arr = np.array(split_of)

    pool_idx = np.where((split_arr == "train") | (split_arr == "cal"))[0]
    test_idx = np.where(split_arr == "test")[0]
    test_x, test_y = features[test_idx], labels[test_idx]
    print(
        f"pool={len(pool_idx)} test={len(test_idx)} (fixed test split, seed {cascade.SPLIT_SEED})",
        flush=True,
    )

    pool_order = pool_idx.copy()
    np.random.RandomState(RNG_SEED).shuffle(pool_order)

    print("part 1: learning curve...", flush=True)
    curve_fits = learning_curve(features, labels, pool_order, test_x, test_y, cascade)

    print("part 2: targeted vs random increment...", flush=True)
    flywheel_result = targeted_vs_random(
        features, labels, pool_order, test_x, test_y, cascade, curve_fits[0.50]
    )

    print("part 3: time-ordered realism check...", flush=True)
    time_result = time_ordered_check(
        features,
        labels,
        answered_at,
        pool_idx,
        test_x,
        test_y,
        cascade,
        curve_fits[0.50].metrics,
        curve_fits[1.00].metrics,
    )

    payload: dict[str, Any] = {
        "schema_version": "pairhead-flywheel-v1",
        "agreement_targets": list(AGREEMENT_TARGETS),
        "base_split": {
            "seed": cascade.SPLIT_SEED,
            "pool_pairs": int(len(pool_idx)),
            "test_pairs": int(len(test_idx)),
        },
        "learning_curve": [curve_fits[frac].metrics for frac in SUBSET_FRACTIONS],
        "targeted_vs_random": flywheel_result,
        "time_ordered": time_result,
        "wall_time_seconds": time.monotonic() - started,
    }

    (matrix_dir / "flywheel-report.json").write_text(json.dumps(payload, indent=2))
    (matrix_dir / "flywheel-report.md").write_text(render_report(payload))

    print(f"done in {payload['wall_time_seconds']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
