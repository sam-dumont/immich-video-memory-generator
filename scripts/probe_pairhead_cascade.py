#!/usr/bin/env python3
"""Features, leakage-free split, calibrated classifier, and cascade curve.

Phase A / steps 2-6 of the pairwise-head prototype. Reads ``embeddings.npy`` +
``ids.json`` written by ``probe_pairhead_embed.py``, then:

  2. PCA(128) fit on every embedding; symmetric, order-invariant pair features
     so swapping (a, b) can never leak which asset was listed first.
  3. Connected-components split of the pair graph into train/cal/test
     (~70/10/20 by pair count, seed 42, greedy fill), verified leakage-free at
     the asset-id level.
  4. LogisticRegression with C chosen by 5-fold CV on train, isotonic-
     calibrated on the held-out calibration split.
  5. Abstention cascade curve on the held-out test split only, plus trivial
     and raw-cosine sanity baselines.
  6. model.pkl, pca.pkl, curve.json, report.md saved under the matrix dir.

report.md contains counts and metrics only -- no asset ids anywhere in it.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.decomposition import PCA
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
PCA_COMPONENTS = 128
SPLIT_TARGETS = {"train": 0.70, "cal": 0.10, "test": 0.20}
SPLIT_SEED = 42
C_GRID = (0.01, 0.1, 1.0, 10.0)
# Noted in the dataset spec: 343 pairs had conflicting teacher labels across
# repeats (latest kept here), putting the teacher's own self-agreement ceiling
# at roughly 95%. Any "agreement with teacher" number above that is noise.
CONFLICTING_TEACHER_PAIRS = 343
TEACHER_SELF_AGREEMENT_CEILING = 0.95


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


@dataclass(frozen=True)
class PairRecord:
    a: str
    b: str
    same: bool


def load_pairs(matrix_dir: Path) -> list[PairRecord]:
    records = []
    with (matrix_dir / "pairs.jsonl").open() as fh:
        for line in fh:
            payload = json.loads(line)
            records.append(PairRecord(a=payload["a"], b=payload["b"], same=bool(payload["same"])))
    return records


# --- step 2: PCA + symmetric pair features ----------------------------------


def fit_pca(embeddings: np.ndarray) -> PCA:
    pca = PCA(n_components=PCA_COMPONENTS, random_state=42)
    pca.fit(embeddings)
    return pca


def pair_features(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Order-invariant features: swapping a and b yields the identical vector."""
    return np.concatenate([np.abs(a - b), a * b, (a + b) / 2.0], axis=-1)


def build_features(
    pairs: list[PairRecord], id_to_row: dict[str, int], embeddings_pca: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    a_rows = np.array([id_to_row[p.a] for p in pairs])
    b_rows = np.array([id_to_row[p.b] for p in pairs])
    features = pair_features(embeddings_pca[a_rows], embeddings_pca[b_rows])
    labels = np.array([p.same for p in pairs], dtype=bool)
    return features.astype(np.float32), labels


# --- step 3: connected-components split --------------------------------------


class UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self._parent[root_left] = root_right


def connected_components(pairs: list[PairRecord], id_to_row: dict[str, int]) -> list[int]:
    """Row index -> component id, using pairs.jsonl edges only."""
    union_find = UnionFind(len(id_to_row))
    for pair in pairs:
        union_find.union(id_to_row[pair.a], id_to_row[pair.b])
    return [union_find.find(row) for row in range(len(id_to_row))]


def assign_splits(pairs: list[PairRecord], id_to_row: dict[str, int], component_of: list[int]) -> list[str]:
    """Greedy-fill whole components into train/cal/test by pair count (seed 42).

    Every pair's two assets share one component by construction (the graph's
    edges are exactly the pairs), so assigning whole components to a split can
    never split one pair's two assets across splits.
    """
    pair_component = [component_of[id_to_row[pair.a]] for pair in pairs]
    pair_counts = Counter(pair_component)

    components = list(pair_counts.items())
    rng = random.Random(SPLIT_SEED)
    rng.shuffle(components)
    components.sort(key=lambda item: item[1], reverse=True)  # stable: keeps the shuffled tie order

    total_pairs = len(pairs)
    targets = {name: fraction * total_pairs for name, fraction in SPLIT_TARGETS.items()}
    filled = dict.fromkeys(SPLIT_TARGETS, 0)
    component_split: dict[int, str] = {}
    for component_id, count in components:
        most_underfilled = min(SPLIT_TARGETS, key=lambda name: filled[name] / targets[name])
        component_split[component_id] = most_underfilled
        filled[most_underfilled] += count

    return [component_split[component] for component in pair_component]


def verify_no_leakage(pairs: list[PairRecord], split_of: list[str]) -> dict[str, int]:
    """Raise if any asset id appears in two splits; return per-split asset counts."""
    ids_by_split: dict[str, set[str]] = defaultdict(set)
    for pair, split in zip(pairs, split_of, strict=True):
        ids_by_split[split].add(pair.a)
        ids_by_split[split].add(pair.b)
    names = list(ids_by_split)
    for i, name_i in enumerate(names):
        for name_j in names[i + 1 :]:
            overlap = ids_by_split[name_i] & ids_by_split[name_j]
            if overlap:
                raise AssertionError(f"{len(overlap)} asset ids leak between {name_i} and {name_j}")
    return {name: len(ids) for name, ids in ids_by_split.items()}


# --- step 4: classifier + calibration ----------------------------------------


def choose_c(features: np.ndarray, labels: np.ndarray) -> tuple[float, dict[float, float]]:
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores: dict[float, float] = {}
    for c in C_GRID:
        model = LogisticRegression(C=c, max_iter=5000, random_state=42)
        fold_scores = cross_val_score(model, features, labels, cv=cv, scoring="accuracy")
        scores[c] = float(np.mean(fold_scores))
    best_c = max(scores, key=lambda c: scores[c])
    return best_c, scores


def train_and_calibrate(
    train_x: np.ndarray, train_y: np.ndarray, cal_x: np.ndarray, cal_y: np.ndarray, best_c: float
) -> CalibratedClassifierCV:
    base = LogisticRegression(C=best_c, max_iter=5000, random_state=42)
    base.fit(train_x, train_y)
    # sklearn 1.6+ removed cv="prefit"; FrozenEstimator + a plain CalibratedClassifierCV
    # is the documented replacement (calibrator fit once on the given data, base untouched).
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
    calibrated.fit(cal_x, cal_y)
    assert list(calibrated.classes_) == [False, True], calibrated.classes_
    return calibrated


# --- step 5: cascade curve + sanity baselines --------------------------------


def cascade_curve(test_y: np.ndarray, p_same: np.ndarray) -> list[dict[str, Any]]:
    coarse = np.round(np.arange(0.500, 0.950, 0.005), 4)
    fine = np.round(np.arange(0.950, 0.9951, 0.0005), 4)
    thresholds = sorted({*coarse.tolist(), *fine.tolist(), 0.995})

    total = len(test_y)
    points = []
    for threshold in thresholds:
        covered = (p_same >= threshold) | (p_same <= 1 - threshold)
        n_covered = int(covered.sum())
        agreement = None
        if n_covered:
            predicted_same = p_same[covered] >= 0.5
            agreement = float(np.mean(predicted_same == test_y[covered]))
        points.append(
            {
                "threshold": float(threshold),
                "coverage": n_covered / total,
                "n_covered": n_covered,
                "agreement": agreement,
            }
        )
    return points


def coverage_at_agreement(curve: list[dict[str, Any]], target: float) -> dict[str, Any]:
    """Best (highest) coverage among thresholds that reach >= target agreement."""
    reachable = [point for point in curve if point["agreement"] is not None and point["agreement"] >= target]
    if not reachable:
        best = max(curve, key=lambda point: point["agreement"] or 0.0)
        return {"reached": False, "best_agreement": best["agreement"], "coverage_at_best": best["coverage"]}
    best = max(reachable, key=lambda point: point["coverage"])
    return {"reached": True, "coverage": best["coverage"], "threshold": best["threshold"]}


def trivial_baseline(train_y: np.ndarray, test_y: np.ndarray) -> dict[str, Any]:
    majority_same = bool(np.mean(train_y) >= 0.5)
    accuracy = float(np.mean(test_y == majority_same))
    return {"majority_label": "same" if majority_same else "different", "accuracy": accuracy}


def cosine_baseline(
    pairs: list[PairRecord], id_to_row: dict[str, int], embeddings: np.ndarray, split_of: list[str]
) -> dict[str, Any]:
    """Best single threshold on raw (pre-PCA) cosine similarity, fit on train, scored on test."""
    a_rows = np.array([id_to_row[pair.a] for pair in pairs])
    b_rows = np.array([id_to_row[pair.b] for pair in pairs])
    cosine = np.sum(embeddings[a_rows] * embeddings[b_rows], axis=1)  # rows are already L2-normalized
    labels = np.array([pair.same for pair in pairs], dtype=bool)
    split_arr = np.array(split_of)

    train_mask, test_mask = split_arr == "train", split_arr == "test"
    candidates = np.linspace(cosine[train_mask].min(), cosine[train_mask].max(), 200)
    best_threshold, best_train_accuracy = 0.0, -1.0
    for threshold in candidates:
        accuracy = float(np.mean((cosine[train_mask] >= threshold) == labels[train_mask]))
        if accuracy > best_train_accuracy:
            best_train_accuracy, best_threshold = accuracy, float(threshold)
    test_accuracy = float(np.mean((cosine[test_mask] >= best_threshold) == labels[test_mask]))
    return {"threshold": best_threshold, "train_accuracy": best_train_accuracy, "test_accuracy": test_accuracy}


# --- step 6: report -----------------------------------------------------------


def _cv_table(cv_scores: dict[float, float]) -> str:
    return ", ".join(f"C={c}: {score:.3f}" for c, score in cv_scores.items())


def _headline_line(headline: dict[str, Any]) -> str:
    if headline["reached"]:
        return f"{headline['coverage']:.1%} of test pairs (threshold {headline['threshold']:.3f})"
    return f"not reached; best achieved is {headline['best_agreement']:.1%} agreement at {headline['coverage_at_best']:.1%} coverage"


def render_report(payload: dict[str, Any]) -> str:
    dataset, split, model = payload["dataset"], payload["split"], payload["model"]
    test_metrics, baselines, headline = payload["test_metrics"], payload["baselines"], payload["cascade_headline"]
    embed = payload["embed_meta"] or {}
    confusion = test_metrics["confusion"]

    fallback_note = f" (fallback: {embed['fallback_reason']})" if embed.get("fallback_reason") else ""

    lines = [
        "# Pairwise-Head Prototype -- Phase A Cascade Report",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file.",
        "",
        "## Dataset",
        "",
        f"- Unique assets embedded: {dataset['unique_assets']}",
        f"- Total labeled pairs: {dataset['total_pairs']} "
        f"({dataset['same_count']} same / {dataset['different_count']} different)",
        f"- Teacher label caveat: {dataset['conflicting_teacher_labels']} pairs had conflicting teacher "
        f"labels across repeats (latest kept); the teacher's own self-agreement ceiling is "
        f"~{dataset['teacher_self_agreement_ceiling']:.0%}.",
        "",
        "## Embedding (step 1)",
        "",
        f"- Model: {embed.get('model_source', 'unknown')}",
        f"- Device: {embed.get('device', 'unknown')}{fallback_note}",
        f"- Measured throughput: {embed.get('ms_per_image', float('nan')):.2f} ms/image over "
        f"{embed.get('asset_count', 'unknown')} assets ({embed.get('elapsed_seconds', float('nan')):.1f}s total)",
        "",
        "## Split (step 3, leakage-free by connected component)",
        "",
        "| split | target | pairs | fraction |",
        "|---|---|---|---|",
    ]
    for name in ("train", "cal", "test"):
        lines.append(
            f"| {name} | {SPLIT_TARGETS[name]:.0%} | {split['pair_counts'][name]} | "
            f"{split['pair_fractions'][name]:.1%} |"
        )
    lines += [
        "",
        f"{split['component_count']} connected components across {dataset['unique_assets']} assets; "
        "no asset id appears in more than one split (verified programmatically).",
        "",
        "## Classifier (step 4)",
        "",
        "- Features: 384-d symmetric [|a-b|, a⊙b, (a+b)/2] over 128-d PCA'd embeddings",
        f"- 5-fold CV accuracy on train by C: {_cv_table(model['cv_scores'])}",
        f"- Chosen C: {model['chosen_c']}",
        f"- Calibration: isotonic, fit on the {split['pair_counts']['cal']}-pair calibration split",
        "",
        f"## Test-split metrics ({split['pair_counts']['test']} pairs, full coverage)",
        "",
        f"- Accuracy: {test_metrics['accuracy']:.3f}",
        f"- ROC-AUC: {test_metrics['roc_auc']:.3f}",
        f"- Confusion: {confusion['tp']} same-correct, {confusion['tn']} different-correct, "
        f"{confusion['fn']} same-called-different, {confusion['fp']} different-called-same",
        "",
        "## Sanity baselines (test split)",
        "",
        f"- Trivial (always predict \"{baselines['trivial']['majority_label']}\"): "
        f"{baselines['trivial']['accuracy']:.3f} accuracy",
        f"- Raw-embedding cosine distance (threshold picked on train): "
        f"{baselines['raw_cosine']['test_accuracy']:.3f} accuracy at threshold "
        f"{baselines['raw_cosine']['threshold']:.3f}",
        "",
        "## Cascade curve (step 5, test split only)",
        "",
        "| target agreement | coverage |",
        "|---|---|",
        f"| 97% | {_headline_line(headline['97pct'])} |",
        f"| 98% | {_headline_line(headline['98pct'])} |",
        f"| 99% | {_headline_line(headline['99pct'])} |",
        "",
        f"Full curve ({len(payload['cascade_curve'])} points) saved to curve.json.",
        "",
        f"Because the teacher's own repeats only agree with themselves ~"
        f"{dataset['teacher_self_agreement_ceiling']:.0%} of the time ({dataset['conflicting_teacher_labels']} "
        "conflicting pairs), any cascade point claiming agreement above that ceiling is fitting label noise, "
        "not signal -- treat a 99%-agreement coverage number as optimistic upstream of that ceiling.",
    ]
    return "\n".join(lines) + "\n"


# --- orchestration -------------------------------------------------------------


def main() -> int:
    args = _arguments()
    started = time.monotonic()

    embeddings = np.load(args.matrix_dir / "embeddings.npy")
    ids: list[str] = json.loads((args.matrix_dir / "ids.json").read_text())
    id_to_row = {asset_id: row for row, asset_id in enumerate(ids)}
    embed_meta_path = args.matrix_dir / "embed_meta.json"
    embed_meta = json.loads(embed_meta_path.read_text()) if embed_meta_path.exists() else None

    pairs = load_pairs(args.matrix_dir)

    pca = fit_pca(embeddings)
    embeddings_pca = pca.transform(embeddings).astype(np.float32)
    features, labels = build_features(pairs, id_to_row, embeddings_pca)

    component_of = connected_components(pairs, id_to_row)
    split_of = assign_splits(pairs, id_to_row, component_of)
    asset_counts_by_split = verify_no_leakage(pairs, split_of)
    print(f"leakage check passed: {asset_counts_by_split}", flush=True)

    split_arr = np.array(split_of)
    masks = {name: split_arr == name for name in SPLIT_TARGETS}
    split_pair_counts = {name: int(mask.sum()) for name, mask in masks.items()}
    print(f"split pair counts: {split_pair_counts}", flush=True)

    train_x, train_y = features[masks["train"]], labels[masks["train"]]
    cal_x, cal_y = features[masks["cal"]], labels[masks["cal"]]
    test_x, test_y = features[masks["test"]], labels[masks["test"]]

    best_c, cv_scores = choose_c(train_x, train_y)
    calibrated = train_and_calibrate(train_x, train_y, cal_x, cal_y, best_c)
    p_test = calibrated.predict_proba(test_x)[:, 1]

    curve = cascade_curve(test_y, p_test)
    headline = {f"{int(target * 100)}pct": coverage_at_agreement(curve, target) for target in (0.97, 0.98, 0.99)}

    predicted_same = p_test >= 0.5
    test_accuracy = float(np.mean(predicted_same == test_y))
    auc = float(roc_auc_score(test_y, p_test))
    tn, fp, fn, tp = confusion_matrix(test_y, predicted_same, labels=[False, True]).ravel()

    trivial = trivial_baseline(train_y, test_y)
    cosine = cosine_baseline(pairs, id_to_row, embeddings, split_of)

    payload: dict[str, Any] = {
        "schema_version": "pairhead-cascade-v1",
        "dataset": {
            "total_pairs": len(pairs),
            "unique_assets": len(ids),
            "same_count": int(labels.sum()),
            "different_count": int((~labels).sum()),
            "conflicting_teacher_labels": CONFLICTING_TEACHER_PAIRS,
            "teacher_self_agreement_ceiling": TEACHER_SELF_AGREEMENT_CEILING,
        },
        "split": {
            "seed": SPLIT_SEED,
            "targets": SPLIT_TARGETS,
            "pair_counts": split_pair_counts,
            "pair_fractions": {name: count / len(pairs) for name, count in split_pair_counts.items()},
            "asset_counts": asset_counts_by_split,
            "component_count": len(set(component_of)),
        },
        "pca": {
            "n_components": PCA_COMPONENTS,
            "explained_variance_ratio_sum": float(np.sum(pca.explained_variance_ratio_)),
        },
        "model": {
            "type": "LogisticRegression(isotonic-calibrated)",
            "c_grid": list(C_GRID),
            "cv_scores": cv_scores,
            "chosen_c": best_c,
        },
        "test_metrics": {
            "accuracy": test_accuracy,
            "roc_auc": auc,
            "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        },
        "baselines": {"trivial": trivial, "raw_cosine": cosine},
        "cascade_headline": headline,
        "cascade_curve": curve,
        "embed_meta": embed_meta,
        "wall_time_seconds": time.monotonic() - started,
    }

    (args.matrix_dir / "curve.json").write_text(json.dumps(payload, indent=2))
    with (args.matrix_dir / "pca.pkl").open("wb") as fh:
        pickle.dump(pca, fh)
    with (args.matrix_dir / "model.pkl").open("wb") as fh:
        pickle.dump(calibrated, fh)
    (args.matrix_dir / "report.md").write_text(render_report(payload))

    print(
        f"done in {payload['wall_time_seconds']:.1f}s; "
        f"test accuracy {test_accuracy:.3f}, AUC {auc:.3f}, chosen C {best_c}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
