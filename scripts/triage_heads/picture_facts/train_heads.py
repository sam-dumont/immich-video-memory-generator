#!/usr/bin/env python3
"""Train three distilled picture-fact heads on the public corpus, the shipped way.

Teacher: openjev, asked the product's own eleven questions (see `questions.py`).
Student: PCA-256 over the frozen DINOv2 pooled pack, then multinomial logistic regression
with class-balanced weights — the recipe in `docs/research/2026-08-31-triage-heads-architecture.md`
and in the recovered `scripts/triage_heads/train.py`.

Only public Open Images pictures reach this script. Splits are held out by creator, so no
photographer's frames sit on both sides.

    python train_heads.py --labels public-labels.jsonl --out heads/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

C_GRID = (0.01, 0.1, 1.0, 10.0)
PCA_COMPONENTS = 256
SEED = 42
OI_EMBEDDINGS = Path("~/.immich-memories-distill/oi-v2/embeddings.db").expanduser()
OI_INDEX = Path("~/.immich-memories-distill/oi-index/index.parquet").expanduser()
ENCODER_KEY = "3d8a4df21037f4fb663fddc98d906a49506e10126ad89df04f5e5cdf8e821d81"  # gitleaks:allow -- the public encoder digest

HEADS: dict[str, dict] = {
    "screen": {
        "source": ("noul", "screen"),
        "classes": ("no_screen", "screen"),
        "threshold": 0.5,
    },
    "frame_kind": {
        "source": ("choice", "what"),
        "classes": (
            "accidental_or_blurred_frame",
            "body_part_closeup",
            "empty_room_ceiling_or_floor",
            "lone_everyday_object",
            "meaningful_record",
            "people_moment",
            "place_or_scenery",
            "screen_or_document",
        ),
    },
    "adult_coverage": {
        "source": ("choice", "adult_coverage"),
        "classes": ("bare_torso", "clothed", "no_adult", "nude", "swimwear", "underwear_only"),
    },
    "child_coverage": {
        "source": ("choice", "child_coverage"),
        "classes": ("clothed", "nappy_or_underwear_only", "no_child", "nude", "swimwear"),
    },
    # A public corpus carries almost no `nude` or `underwear_only`, so those arms of the two
    # coverage heads cannot be trained from public pictures at all. This binary collapses
    # them into the one fact the gates actually read, which a public corpus can support.
    "uncovered_person": {
        "source": ("derived", "uncovered"),
        "classes": ("covered", "uncovered"),
    },
}
ADULT_UNCOVERED = {"bare_torso", "underwear_only", "nude"}
CHILD_UNCOVERED = {"nappy_or_underwear_only", "nude"}


def teacher_label(head: str, answers: dict) -> str | None:
    kind, field = HEADS[head]["source"]
    if kind == "derived":
        adult = answers.get("adult_coverage")
        child = answers.get("child_coverage")
        if adult is None or child is None:
            return None
        uncovered = adult["choice"] in ADULT_UNCOVERED or child["choice"] in CHILD_UNCOVERED
        return "uncovered" if uncovered else "covered"
    value = answers.get(field)
    if value is None:
        return None
    if kind == "noul":
        return HEADS[head]["classes"][1 if float(value) >= HEADS[head]["threshold"] else 0]
    return str(value["choice"])


def load_packs(ids: list[str], extra: list[Path]) -> dict[str, np.ndarray]:
    """Packs from the shipped Open Images staging cache, plus any freshly embedded npz."""
    packs: dict[str, np.ndarray] = {}
    wanted = set(ids)
    connection = sqlite3.connect(f"file:{OI_EMBEDDINGS}?mode=ro", uri=True)
    for asset_id, blob in connection.execute(
        "SELECT asset_id, pack FROM embedding_staging WHERE encoder_key = ?", (ENCODER_KEY,)
    ):
        if asset_id in wanted:
            packs[asset_id] = np.frombuffer(blob, dtype=np.float32)
    connection.close()
    for path in extra:
        with np.load(path) as payload:
            for asset_id, pack in zip(payload["ids"].tolist(), payload["packs"], strict=True):
                if asset_id in wanted:
                    packs[asset_id] = np.asarray(pack, dtype=np.float32)
    return packs


def fit_pca(packs: np.ndarray, components: int = PCA_COMPONENTS):
    from sklearn.decomposition import PCA

    model = PCA(n_components=components, svd_solver="randomized", random_state=SEED)
    model.fit(np.asarray(packs, dtype=np.float32))
    return (
        np.asarray(model.mean_, dtype=np.float32),
        np.asarray(model.components_, dtype=np.float32),
        np.asarray(model.explained_variance_ratio_, dtype=np.float32),
    )


def project(mean: np.ndarray, components: np.ndarray, packs: np.ndarray) -> np.ndarray:
    """Training projects FP32 -> stored FP16 -> FP32; serving rounds the same way."""
    projected = (np.asarray(packs, dtype=np.float32) - mean) @ components.T
    return projected.astype(np.float16).astype(np.float32)


def class_weights(labels: np.ndarray, classes: np.ndarray) -> dict[str, float]:
    counts = Counter(str(label) for label in labels)
    return {
        str(label): min(10.0, len(labels) / (len(classes) * counts[str(label)]))
        for label in classes
    }


def fit_linear(features: np.ndarray, labels: np.ndarray, groups: np.ndarray):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_score

    classes = np.unique(labels)
    weights = class_weights(labels, classes)
    folds = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED)
    scores: dict[float, float] = {}
    for c in C_GRID:
        model = LogisticRegression(C=c, class_weight=weights, max_iter=5000, random_state=SEED)
        scores[c] = float(
            np.mean(
                cross_val_score(
                    model, features, labels, groups=groups, cv=folds, scoring="accuracy", n_jobs=1
                )
            )
        )
    best = max(C_GRID, key=lambda value: (scores[value], -value))
    model = LogisticRegression(C=best, class_weight=weights, max_iter=5000, random_state=SEED)
    model.fit(features, labels)
    ordered = tuple(str(label) for label in model.classes_)
    coef = np.asarray(model.coef_, dtype=np.float32)
    intercept = np.asarray(model.intercept_, dtype=np.float32)
    if len(ordered) == 2:
        # sklearn keeps one binary row; the bundle contract is one row per class.
        coef = np.vstack([np.zeros_like(coef), coef])
        intercept = np.concatenate([np.zeros_like(intercept), intercept])
    return ordered, coef, intercept, float(best), scores


def fit_mlp(features: np.ndarray, labels: np.ndarray):
    """The architecture doc's alternative: ship it only if it beats the probe by >=2 points."""
    from sklearn.neural_network import MLPClassifier

    model = MLPClassifier(
        hidden_layer_sizes=(256,),
        activation="relu",
        alpha=1e-4,
        batch_size=256,
        learning_rate_init=1e-3,
        max_iter=120,
        random_state=SEED,
        early_stopping=True,
        n_iter_no_change=8,
    )
    model.fit(features, labels)
    return model


def softmax_predict(coef, intercept, features):
    logits = features @ coef.T + intercept
    logits = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--extra-packs", nargs="*", type=Path, default=[])
    parser.add_argument("--topup-manifest", type=Path, default=None)
    parser.add_argument(
        "--pca-from",
        type=Path,
        default=None,
        help=(
            "Reuse an existing bundle's PCA instead of fitting one. A bundle carries a single "
            "projection, so heads that are to be appended to the shipped bundle must share its "
            "PCA — otherwise the product would have to load two bundles."
        ),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    rows = []
    with args.labels.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                if row.get("status") == "described":
                    rows.append(row)
    print(f"{len(rows)} teacher rows", flush=True)

    index = pd.read_parquet(OI_INDEX).set_index("image_id")
    author = index["author"].to_dict()
    landing = index["landing_url"].to_dict()

    ids = [r["image_id"] for r in rows]
    packs_by_id = load_packs(ids, args.extra_packs)
    rows = [r for r in rows if r["image_id"] in packs_by_id]
    print(f"{len(rows)} rows with a pack", flush=True)

    ids = [r["image_id"] for r in rows]
    packs = np.stack([packs_by_id[i] for i in ids])
    # Creator-held-out: an author with no recorded name gets their own group, never a shared one.
    groups = np.asarray(
        [str(author.get(i) or f"__unknown__{i}") for i in ids],
        dtype=object,
    )

    rng = np.random.default_rng(SEED)
    unique_groups = np.array(sorted(set(groups.tolist())))
    rng.shuffle(unique_groups)
    cut = int(0.8 * len(unique_groups))
    train_groups = set(unique_groups[:cut].tolist())
    is_train = np.asarray([g in train_groups for g in groups])
    assert not (set(groups[is_train].tolist()) & set(groups[~is_train].tolist()))
    print(f"train {int(is_train.sum())} / holdout {int((~is_train).sum())}", flush=True)

    if args.pca_from:
        with np.load(args.pca_from, allow_pickle=False) as payload:
            mean = np.asarray(payload["pca_mean"], dtype=np.float32)
            components = np.asarray(payload["pca_components"], dtype=np.float32)
        explained = np.asarray([float("nan")], dtype=np.float32)
        pca_source = str(args.pca_from)
    else:
        mean, components, explained = fit_pca(packs[is_train])
        pca_source = "fitted on this corpus' training split"
    features = project(mean, components, packs)

    report: dict = {
        "corpus": {
            "rows": len(rows),
            "train": int(is_train.sum()),
            "holdout": int((~is_train).sum()),
            "creators": len(unique_groups),
            "pca_source": pca_source,
            "pca_explained_variance": round(float(explained.sum()), 4),
        },
        "heads": {},
    }
    bundle_heads = {}
    thresholds: dict[str, float] = {}
    for head in HEADS:
        labels = np.asarray([teacher_label(head, r["answers"]) for r in rows], dtype=object)
        keep = labels != None  # noqa: E711
        counts = Counter(labels[keep].tolist())
        train_mask = keep & is_train
        # A class the teacher never uses cannot be trained; record it as an absent class.
        # A class the teacher barely uses cannot be trained or cross-validated: below ten
        # training rows StratifiedGroupKFold cannot fill five folds, so it is dropped and
        # named rather than learned from four examples.
        train_counts = Counter(labels[train_mask].tolist())
        absent = [c for c in HEADS[head]["classes"] if c not in train_counts]
        drop = {c for c in train_counts if train_counts[c] < 10}
        train_mask = train_mask & np.asarray([label not in drop for label in labels])
        y = np.asarray([str(v) for v in labels[train_mask]])
        classes, coef, intercept, best_c, cv_scores = fit_linear(
            features[train_mask], y, groups[train_mask]
        )
        held = keep & ~is_train & np.asarray([label not in drop for label in labels])
        probabilities = softmax_predict(coef, intercept, features[held])
        predicted = np.asarray(classes)[probabilities.argmax(axis=1)]
        truth = np.asarray([str(v) for v in labels[held]])
        accuracy = float((predicted == truth).mean())
        mlp = fit_mlp(features[train_mask], y)
        mlp_accuracy = float((mlp.predict(features[held]) == truth).mean())
        per_class = {}
        for label in classes:
            mask = truth == label
            if mask.any():
                per_class[label] = {
                    "n": int(mask.sum()),
                    "recall": round(float((predicted[mask] == label).mean()), 4),
                    "precision": round(
                        float((truth[predicted == label] == label).mean())
                        if (predicted == label).any()
                        else 0.0,
                        4,
                    ),
                }
        report["heads"][head] = {
            "teacher_counts": dict(sorted(counts.items())),
            "absent_classes": absent,
            "dropped_thin_classes": sorted(drop),
            "trained_classes": list(classes),
            "C": best_c,
            "cv_accuracy": {str(k): round(v, 4) for k, v in cv_scores.items()},
            "holdout_n": int(held.sum()),
            "holdout_accuracy": round(accuracy, 4),
            "holdout_accuracy_mlp": round(mlp_accuracy, 4),
            "mlp_beats_linear_by": round(100 * (mlp_accuracy - accuracy), 2),
            "per_class": per_class,
        }
        # The architecture doc ships a calibrated band, not a bare argmax. For the two
        # binaries, pick the operating point on the PUBLIC holdout — never on the owner's
        # pictures, which would be fitting the evaluation set.
        if len(classes) == 2:
            positive = list(classes).index(HEADS[head]["classes"][-1])
            scores = probabilities[:, positive]
            hit = truth == classes[positive]
            best = (0.0, 0.5)
            for threshold in np.unique(np.round(scores, 3)):
                chosen = scores >= threshold
                if not chosen.any():
                    continue
                precision = float(hit[chosen].mean())
                recall = float(chosen[hit].mean()) if hit.any() else 0.0
                f1 = (
                    0.0
                    if precision + recall == 0
                    else 2 * precision * recall / (precision + recall)
                )
                if f1 > best[0]:
                    best = (f1, float(threshold))
            report["heads"][head]["public_holdout_threshold"] = round(best[1], 4)
            report["heads"][head]["public_holdout_f1_at_threshold"] = round(best[0], 4)
            thresholds[head] = round(best[1], 4)
        bundle_heads[head] = (classes, coef, intercept)
        print(
            f"  {head}: holdout {accuracy:.4f} (mlp {mlp_accuracy:.4f}) "
            f"C={best_c} classes={len(classes)}",
            flush=True,
        )

    np.savez_compressed(
        args.out / "heads.npz",
        pca_mean=mean,
        pca_components=components,
        **{
            f"{head}__{field}": value
            for head, (classes, coef, intercept) in bundle_heads.items()
            for field, value in (
                ("classes", np.asarray(classes)),
                ("coef", coef),
                ("intercept", intercept),
            )
        },
    )
    (args.out / "train-report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    (args.out / "thresholds.json").write_text(json.dumps(thresholds, indent=1, sort_keys=True))
    sources = sorted({str(landing.get(i, "")) for i in ids if landing.get(i)})
    (args.out / "corpus-ids.txt").write_text("\n".join(sorted(ids)) + "\n")
    print(json.dumps(report["corpus"]), f"{len(sources)} distinct source pages", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
