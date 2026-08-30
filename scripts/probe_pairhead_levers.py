#!/usr/bin/env python3
"""Three levers to shrink the pairwise-head abstention band.

Reuses the exact leakage-free split (seed 42) and feature construction from
``probe_pairhead_cascade.py`` so every number here is comparable to the
original run's report.md / curve.json. Three independent levers, all
evaluated on the same held-out test split:

  1. Asymmetric band -- pure arithmetic. Sweep independent accept thresholds
     for "same" (t_same) and "different" (t_diff) instead of one symmetric
     band, and report coverage plus the two error directions separately.
  2. Distance prefilter -- pure arithmetic. A raw-embedding cosine-distance
     cutoff beyond which no teacher-same pair in the bank has ever been
     observed, absorbing some fraction of pairs before either the head or the
     big model sees them.
  3. Time-delta + hash features -- a small retrain. Adds a bucketized
     |taken_at delta| and a perceptual-hash Hamming distance to the 384
     symmetric embedding features, then redoes lever 1 on the richer model.

Owner ruling (2026-08-30): report two operating profiles throughout, not one.
Banked pairs are within-moment selects comparisons -- a wrong verdict there
swaps one frame for a near-identical sibling of the same scene, which is
invisible in the finished video. The owner's bar for that pass is "95%
working is better than 100%", so a *loose* 95%-agreement profile is the
expected default. The *strict* <=0.5%-dangerous profile stays relevant only
for the separate, barely-banked cross-moment duplicate-confirmation pass,
where a wrong "same" can drop a whole occasion.

Counts and metrics only in levers-report.md -- no asset ids. levers-report.json
carries the same numbers for programmatic reuse (also id-free).
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_pairhead_cascade as cascade  # noqa: E402

MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
CONSISTENCY_DIR = Path.home() / ".immich-memories-matrix" / "smart-edit-consistency-v23-2026-08-30"

# Lever 1: the requested coarse grid.
T_SAME_COARSE = (0.93, 0.945, 0.96, 0.98)
T_DIFF_COARSE = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)

# Finer grid used only to locate good headline operating points (95/97/98%
# agreement, and the strict dangerous<=0.5% point) with better resolution
# than the 4x6 coarse grid gives. Same test split, same probabilities.
T_SAME_FINE = np.round(np.arange(0.65, 0.9951, 0.005), 4)
T_DIFF_FINE = np.round(np.arange(0.01, 0.45, 0.005), 4)

DANGEROUS_CAP = 0.005
AGREEMENT_TARGETS = (0.95, 0.97, 0.98)
MARGIN_FACTOR = 0.9

DELTA_T_BUCKET_EDGES = (5.0, 60.0, 3600.0, 86400.0)  # seconds; 5 buckets total
HAMMING_BITS = 64
HAMMING_CLOSE = 2


# --- shared setup: reproduce the original run's split + model exactly ------


def load_bank() -> dict[str, Any]:
    embeddings = np.load(MATRIX_DIR / "embeddings.npy")
    ids: list[str] = json.loads((MATRIX_DIR / "ids.json").read_text())
    id_to_row = {asset_id: row for row, asset_id in enumerate(ids)}
    pairs = cascade.load_pairs(MATRIX_DIR)

    with (MATRIX_DIR / "pca.pkl").open("rb") as fh:
        pca = pickle.load(fh)
    with (MATRIX_DIR / "model.pkl").open("rb") as fh:
        original_model = pickle.load(fh)

    embeddings_pca = pca.transform(embeddings).astype(np.float32)
    features, labels = cascade.build_features(pairs, id_to_row, embeddings_pca)

    component_of = cascade.connected_components(pairs, id_to_row)
    split_of = cascade.assign_splits(pairs, id_to_row, component_of)
    cascade.verify_no_leakage(pairs, split_of)
    split_arr = np.array(split_of)

    return {
        "embeddings": embeddings,
        "ids": ids,
        "id_to_row": id_to_row,
        "pairs": pairs,
        "pca": pca,
        "original_model": original_model,
        "embeddings_pca": embeddings_pca,
        "features": features,
        "labels": labels,
        "split_arr": split_arr,
    }


def sanity_check_against_original_report(bank: dict[str, Any]) -> dict[str, Any]:
    """Confirm the reloaded pca.pkl/model.pkl reproduce report.md's test metrics."""
    test_mask = bank["split_arr"] == "test"
    test_x, test_y = bank["features"][test_mask], bank["labels"][test_mask]
    p_test = bank["original_model"].predict_proba(test_x)[:, 1]
    predicted_same = p_test >= 0.5
    accuracy = float(np.mean(predicted_same == test_y))
    auc = float(roc_auc_score(test_y, p_test))
    ok = abs(accuracy - 0.906) < 0.002 and abs(auc - 0.958) < 0.002
    return {
        "reproduced_test_accuracy": accuracy,
        "reproduced_test_auc": auc,
        "matches_original_report_md": ok,
    }


# --- Lever 1: asymmetric band ------------------------------------------------


def evaluate_cell(
    p_same: np.ndarray, test_y: np.ndarray, t_same: float, t_diff: float
) -> dict[str, Any]:
    """One (t_same, t_diff) operating point on an arbitrary p_same/test_y population."""
    n_total = len(p_same)
    same_call = p_same >= t_same
    diff_call = p_same <= t_diff
    covered = same_call | diff_call
    n_covered = int(covered.sum())

    agreement = None
    if n_covered:
        agreement = float(np.mean(same_call[covered] == test_y[covered]))

    dangerous_denominator = covered & ~test_y
    dangerous_numerator = dangerous_denominator & same_call
    dangerous_rate = (
        float(dangerous_numerator.sum() / dangerous_denominator.sum())
        if dangerous_denominator.sum()
        else None
    )

    safe_denominator = covered & test_y
    safe_numerator = safe_denominator & diff_call
    safe_rate = (
        float(safe_numerator.sum() / safe_denominator.sum()) if safe_denominator.sum() else None
    )

    return {
        "t_same": float(t_same),
        "t_diff": float(t_diff),
        "n_total": n_total,
        "n_covered": n_covered,
        "coverage": n_covered / n_total if n_total else 0.0,
        "agreement": agreement,
        "dangerous_rate_same_given_teacher_different": dangerous_rate,
        "dangerous_n": int(dangerous_denominator.sum()),
        "safe_rate_different_given_teacher_same": safe_rate,
        "safe_n": int(safe_denominator.sum()),
    }


def sweep_grid(
    p_same: np.ndarray, test_y: np.ndarray, t_same_values: np.ndarray, t_diff_values: np.ndarray
) -> list[dict[str, Any]]:
    return [
        evaluate_cell(p_same, test_y, t_same, t_diff)
        for t_same in t_same_values
        for t_diff in t_diff_values
        if t_same > t_diff
    ]


def best_cell_dangerous_capped(
    cells: list[dict[str, Any]], cap: float = DANGEROUS_CAP
) -> dict[str, Any] | None:
    """Max-coverage cell among those with a defined dangerous rate <= cap.

    A cell whose covered-and-teacher-different bucket is empty has an
    undefined dangerous rate, not a zero one -- excluded rather than treated
    as trivially satisfying the cap (an empty-denominator "0%" is the exact
    false-success shape a threshold sweep can quietly produce).
    """
    eligible = [
        c
        for c in cells
        if c["dangerous_rate_same_given_teacher_different"] is not None
        and c["dangerous_rate_same_given_teacher_different"] <= cap
    ]
    if not eligible:
        return None
    return max(
        eligible, key=lambda c: (c["coverage"], -c["dangerous_rate_same_given_teacher_different"])
    )


def best_cell_min_dangerous(cells: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest defined dangerous rate on the grid, tie-broken by higher coverage."""
    eligible = [c for c in cells if c["dangerous_rate_same_given_teacher_different"] is not None]
    if not eligible:
        return None
    return min(
        eligible, key=lambda c: (c["dangerous_rate_same_given_teacher_different"], -c["coverage"])
    )


def strict_operating_point(
    cells: list[dict[str, Any]], cap: float = DANGEROUS_CAP
) -> dict[str, Any]:
    """The <=cap dangerous-direction cell if one exists; otherwise the closest achieved.

    Never silently substitutes a different profile's cell here: when the cap
    genuinely is not reachable on this grid, that is itself the finding, not
    something to paper over by falling back to the loose-agreement cell.
    """
    capped = best_cell_dangerous_capped(cells, cap)
    if capped is not None:
        return {"reached": True, "cap": cap, "cell": capped}
    return {"reached": False, "cap": cap, "cell": best_cell_min_dangerous(cells)}


def best_cell_at_agreement(cells: list[dict[str, Any]], target: float) -> dict[str, Any]:
    reachable = [c for c in cells if c["agreement"] is not None and c["agreement"] >= target]
    if not reachable:
        best = max(cells, key=lambda c: c["agreement"] or 0.0)
        return {"reached": False, "best_agreement": best["agreement"], "cell_at_best": best}
    best = max(reachable, key=lambda c: c["coverage"])
    return {"reached": True, "cell": best}


def lever1_report(p_same: np.ndarray, test_y: np.ndarray, *, label: str) -> dict[str, Any]:
    coarse = sweep_grid(p_same, test_y, np.array(T_SAME_COARSE), np.array(T_DIFF_COARSE))
    fine = sweep_grid(p_same, test_y, T_SAME_FINE, T_DIFF_FINE)
    return {
        "model": label,
        "coarse_grid": coarse,
        "strict_coarse": strict_operating_point(coarse),
        "strict_fine": strict_operating_point(fine),
        "headline_by_agreement_target": {
            f"{int(target * 100)}pct": best_cell_at_agreement(fine, target)
            for target in AGREEMENT_TARGETS
        },
    }


# --- Lever 2: distance prefilter --------------------------------------------


def raw_cosine_distance(
    pairs: list[cascade.PairRecord], id_to_row: dict[str, int], embeddings: np.ndarray
) -> np.ndarray:
    a_rows = np.array([id_to_row[p.a] for p in pairs])
    b_rows = np.array([id_to_row[p.b] for p in pairs])
    similarity = np.sum(embeddings[a_rows] * embeddings[b_rows], axis=1)
    return 1.0 - similarity


def find_tau_far(distance: np.ndarray, same_mask: np.ndarray) -> float:
    """Tightest distance beyond which zero teacher-same pairs lie, over the full bank."""
    return float(distance[same_mask].max())


def lever2_prefilter(distance_all: np.ndarray, same_mask_all: np.ndarray) -> dict[str, Any]:
    diff_mask_all = ~same_mask_all
    tau_far = find_tau_far(distance_all, same_mask_all)
    beyond = distance_all > tau_far
    frac_diff_beyond = float(np.mean(beyond[diff_mask_all])) if diff_mask_all.any() else 0.0
    n_same_beyond = int(np.sum(beyond & same_mask_all))  # must be 0 by construction of tau_far

    tau_margin = tau_far * MARGIN_FACTOR
    beyond_margin = distance_all > tau_margin
    n_same_beyond_margin = int(np.sum(beyond_margin & same_mask_all))
    frac_diff_beyond_margin = (
        float(np.mean(beyond_margin[diff_mask_all])) if diff_mask_all.any() else 0.0
    )

    return {
        "tau_far": tau_far,
        "same_pairs_beyond_tau_far": n_same_beyond,
        "fraction_teacher_different_beyond_tau_far": frac_diff_beyond,
        "n_teacher_different_total": int(diff_mask_all.sum()),
        "margin_factor": MARGIN_FACTOR,
        "tau_margin": tau_margin,
        "same_pairs_beyond_tau_margin": n_same_beyond_margin,
        "fraction_teacher_different_beyond_tau_margin": frac_diff_beyond_margin,
        "caveat": (
            "tau_far is fit and evaluated on the same 6,384-pair bank (including the test split's own "
            "labels), so the zero-same-pairs-beyond-it guarantee is in-sample; it is only as good as this "
            "bank's label quality (~95% teacher self-agreement ceiling, 343 conflicting-label pairs) and "
            "is not a hard out-of-sample guarantee against a future same-pair sitting further out."
        ),
    }


def cascade_breakdown(
    distance: np.ndarray,
    p_same: np.ndarray,
    test_y: np.ndarray,
    tau: float,
    t_same: float,
    t_diff: float,
) -> dict[str, Any]:
    """Prefilter (distance > tau) + head band (t_same/t_diff) on the remainder + residual.

    All fractions are of the population passed in (the test split, unless
    noted otherwise) -- prefilter_frac + head_frac + residual_frac == 1.
    """
    n_total = len(distance)
    prefilter_mask = distance > tau
    remainder_mask = ~prefilter_mask

    same_call = p_same >= t_same
    diff_call = p_same <= t_diff
    covered = remainder_mask & (same_call | diff_call)
    residual_mask = remainder_mask & ~covered

    dangerous_denominator = covered & ~test_y
    dangerous_numerator = dangerous_denominator & same_call
    dangerous_rate = (
        float(dangerous_numerator.sum() / dangerous_denominator.sum())
        if dangerous_denominator.sum()
        else None
    )
    safe_denominator = covered & test_y
    safe_numerator = safe_denominator & diff_call
    safe_rate = (
        float(safe_numerator.sum() / safe_denominator.sum()) if safe_denominator.sum() else None
    )

    return {
        "t_same": float(t_same),
        "t_diff": float(t_diff),
        "tau": float(tau),
        "n_total": n_total,
        "prefilter_frac": float(prefilter_mask.mean()) if n_total else 0.0,
        "head_frac": float(covered.mean()) if n_total else 0.0,
        "residual_frac": float(residual_mask.mean()) if n_total else 0.0,
        "dangerous_rate_within_head": dangerous_rate,
        "dangerous_n_within_head": int(dangerous_denominator.sum()),
        "safe_rate_within_head": safe_rate,
        "safe_n_within_head": int(safe_denominator.sum()),
    }


# --- Lever 3: time-delta + hash features ------------------------------------


def load_timestamps() -> dict[str, datetime]:
    payload = json.loads((MATRIX_DIR / "timestamps.json").read_text())
    return {
        aid: datetime.fromisoformat(iso.replace("Z", "+00:00"))
        for aid, iso in payload["timestamps"].items()
    }


def load_hashes() -> dict[str, str]:
    payload = json.loads((MATRIX_DIR / "hashes.json").read_text())
    return payload["hashes"]


def delta_t_bucket_onehot(seconds: float | None) -> np.ndarray:
    onehot = np.zeros(5, dtype=np.float32)
    if seconds is None:
        onehot[4] = 1.0  # unknown treated as the least informative (farthest) bucket
        return onehot
    for index, edge in enumerate(DELTA_T_BUCKET_EDGES):
        if seconds < edge:
            onehot[index] = 1.0
            return onehot
    onehot[4] = 1.0
    return onehot


def hamming_pair_features(hamming: int | None) -> np.ndarray:
    if hamming is None:
        return np.array([1.0, 0.0], dtype=np.float32)
    return np.array(
        [hamming / HAMMING_BITS, 1.0 if hamming <= HAMMING_CLOSE else 0.0], dtype=np.float32
    )


def build_extra_features(
    pairs: list[cascade.PairRecord], timestamps: dict[str, datetime], hashes: dict[str, str]
) -> np.ndarray:
    from immich_memories.analysis.duplicate_hashing import hamming_distance

    rows = []
    for pair in pairs:
        ta, tb = timestamps.get(pair.a), timestamps.get(pair.b)
        delta_seconds = (
            abs((ta - tb).total_seconds()) if ta is not None and tb is not None else None
        )
        ha, hb = hashes.get(pair.a), hashes.get(pair.b)
        hamming = hamming_distance(ha, hb) if ha is not None and hb is not None else None
        rows.append(
            np.concatenate([delta_t_bucket_onehot(delta_seconds), hamming_pair_features(hamming)])
        )
    return np.stack(rows).astype(np.float32)


def retrain_with_extra_features(bank: dict[str, Any], extra: np.ndarray) -> dict[str, Any]:
    features = np.concatenate([bank["features"], extra], axis=1)
    labels = bank["labels"]
    split_arr = bank["split_arr"]
    masks = {name: split_arr == name for name in cascade.SPLIT_TARGETS}

    train_x, train_y = features[masks["train"]], labels[masks["train"]]
    cal_x, cal_y = features[masks["cal"]], labels[masks["cal"]]
    test_x, test_y = features[masks["test"]], labels[masks["test"]]

    best_c, cv_scores = cascade.choose_c(train_x, train_y)
    calibrated = cascade.train_and_calibrate(train_x, train_y, cal_x, cal_y, best_c)
    p_test = calibrated.predict_proba(test_x)[:, 1]

    predicted_same = p_test >= 0.5
    accuracy = float(np.mean(predicted_same == test_y))
    auc = float(roc_auc_score(test_y, p_test))
    tn, fp, fn, tp = confusion_matrix(test_y, predicted_same, labels=[False, True]).ravel()

    return {
        "feature_dim": features.shape[1],
        "chosen_c": best_c,
        "cv_scores": cv_scores,
        "test_accuracy": accuracy,
        "test_roc_auc": auc,
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "p_test": p_test,
        "test_y": test_y,
        "calibrated_model": calibrated,
    }


# --- 2007-case empirical cross-check -----------------------------------------


def load_2007_asset_universe() -> set[str]:
    cards_path = CONSISTENCY_DIR / "01-year-2007" / "cards.json"
    payload = json.loads(cards_path.read_text())
    ids: set[str] = set()
    for card in payload["cards"]:
        ids.update(card["asset_ids"])
    return ids


def pairs_in_universe_mask(pairs: list[cascade.PairRecord], universe: set[str]) -> np.ndarray:
    return np.array([p.a in universe and p.b in universe for p in pairs])


def empirical_2007_subset(bank: dict[str, Any], mask: np.ndarray) -> dict[str, Any]:
    """Bank pairs whose both ids fall in the 2007 case's card asset universe.

    Cross-checks against shadow-2007-report.json's independently-extracted
    535-pair set (699 raw rows from the pass-2-selects judgment DB, deduped) --
    a different extraction path landing within one pair of this one is a
    strong signal both are (near enough) the same real population.
    """
    split_arr = bank["split_arr"]
    n = int(mask.sum())
    split_counts = {name: int(np.sum(mask & (split_arr == name))) for name in cascade.SPLIT_TARGETS}
    labels = bank["labels"][mask]
    return {
        "n_pairs": n,
        "same_count": int(labels.sum()),
        "different_count": int((~labels).sum()),
        "split_breakdown": split_counts,
        "note": (
            "534/535 pairs match the shadow-2007-report.json extraction (699 raw pass-2-selects rows, "
            "deduped to 535 unordered pairs) to within one pair -- treated as (near enough) the same "
            "population. Only its test-split slice (see measured_test_split_only) is out-of-sample for "
            "the retrained model; the rest were seen during training/calibration, so an all-534 accuracy "
            "number would be optimistic."
        ),
    }


# --- report rendering ---------------------------------------------------------


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _cell_line(cell: dict[str, Any]) -> str:
    return (
        f"t_same={cell['t_same']:.3f}, t_diff={cell['t_diff']:.3f} -> coverage {_fmt_pct(cell['coverage'])}, "
        f"agreement {_fmt_pct(cell['agreement'])}, dangerous {_fmt_pct(cell['dangerous_rate_same_given_teacher_different'])} "
        f"(n={cell['dangerous_n']}), safe {_fmt_pct(cell['safe_rate_different_given_teacher_same'])} (n={cell['safe_n']})"
    )


def _strict_line(point: dict[str, Any]) -> str:
    """Render a strict_operating_point result, distinguishing a real cap hit from a closest-effort fallback."""
    cell = point["cell"]
    if cell is None:
        return f"no cell on this grid has a defined dangerous rate (cap {point['cap']:.1%})"
    verb = "reaches" if point["reached"] else "does NOT reach -- closest achieved is"
    return f"{verb} the <={point['cap']:.1%} dangerous cap: {_cell_line(cell)}"


def render_markdown(payload: dict[str, Any]) -> str:
    l1, l2, l3, final = (
        payload["lever1"],
        payload["lever2"],
        payload["lever3"],
        payload["final_arithmetic"],
    )
    lines = [
        "# Pairwise-Head Levers -- Shrinking the Abstention Band",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file.",
        "",
        "## Framing (owner ruling, 2026-08-30)",
        "",
        "Two operating profiles are reported throughout:",
        "",
        "- **Loose (95% agreement)** -- the default for these banked pairs, which are within-moment "
        "selects comparisons. A wrong verdict there swaps one frame for a near-identical sibling of the "
        'same scene: invisible in the finished video. "95% working is better than 100%."',
        "- **Strict (dangerous <=0.5%)** -- reserved for the separate, barely-banked cross-moment "
        'duplicate-confirmation pass, where a wrong "same" verdict can drop a whole occasion.',
        "",
        "## Setup sanity check",
        "",
        f"- Reproduced test accuracy: {payload['sanity']['reproduced_test_accuracy']:.3f} "
        f"(original report.md: 0.906)",
        f"- Reproduced test ROC-AUC: {payload['sanity']['reproduced_test_auc']:.3f} (original report.md: 0.958)",
        f"- Matches original run: {payload['sanity']['matches_original_report_md']}",
        "",
        "## Lever 1 -- Asymmetric band (original model)",
        "",
        "Coarse grid (as specified): t_same in {0.93, 0.945, 0.96, 0.98} x "
        "t_diff in {0.05, 0.10, 0.15, 0.20, 0.25, 0.30}, evaluated on the held-out test split "
        f"({l1['coarse_grid'][0]['n_total']} pairs).",
        "",
        "| t_same | t_diff | coverage | agreement | dangerous (same\\|different) | safe (different\\|same) |",
        "|---|---|---|---|---|---|",
    ]
    for cell in l1["coarse_grid"]:
        lines.append(
            f"| {cell['t_same']:.3f} | {cell['t_diff']:.3f} | {_fmt_pct(cell['coverage'])} | "
            f"{_fmt_pct(cell['agreement'])} | {_fmt_pct(cell['dangerous_rate_same_given_teacher_different'])} "
            f"(n={cell['dangerous_n']}) | {_fmt_pct(cell['safe_rate_different_given_teacher_same'])} "
            f"(n={cell['safe_n']}) |"
        )
    lines += [
        "",
        f"Coarse-grid strict cell: {_strict_line(l1['strict_coarse'])}",
        "",
        "Headline operating points (finer grid, same test split):",
        "",
        "| target | reached | coverage | dangerous | safe |",
        "|---|---|---|---|---|",
    ]
    for target_key, headline in l1["headline_by_agreement_target"].items():
        if headline["reached"]:
            c = headline["cell"]
            lines.append(
                f"| {target_key} agreement | yes | {_fmt_pct(c['coverage'])} | "
                f"{_fmt_pct(c['dangerous_rate_same_given_teacher_different'])} | "
                f"{_fmt_pct(c['safe_rate_different_given_teacher_same'])} |"
            )
        else:
            lines.append(
                f"| {target_key} agreement | no | best={_fmt_pct(headline['best_agreement'])} | -- | -- |"
            )
    lines += [
        "",
        f"Fine-grid strict cell (original model): {_strict_line(l1['strict_fine'])}",
        "",
        "## Lever 2 -- Distance prefilter",
        "",
        f"- tau_far (tightest distance with zero teacher-same pairs beyond it, full 6,384-pair bank): "
        f"{l2['tau_far']:.4f}",
        f"- Teacher-same pairs beyond tau_far: {l2['same_pairs_beyond_tau_far']} (by construction)",
        f"- Teacher-different pairs beyond tau_far (need no adjudication at all): "
        f"{_fmt_pct(l2['fraction_teacher_different_beyond_tau_far'])} of {l2['n_teacher_different_total']} "
        "different pairs",
        f"- Safety-margin variant (tau_far x {l2['margin_factor']} = {l2['tau_margin']:.4f}): "
        f"{l2['same_pairs_beyond_tau_margin']} teacher-same pairs now beyond it, "
        f"{_fmt_pct(l2['fraction_teacher_different_beyond_tau_margin'])} of different pairs beyond it "
        "(shrinking tau_far widens, not narrows, the zero-adjudication zone -- this variant is a stress "
        "test of how close the boundary sits to a real same-pair, not a more conservative setting)",
        f"- Caveat: {l2['caveat']}",
        "",
        "Recomputed cascade arithmetic (prefilter + head-on-remainder + residual), test split, "
        "original model:",
        "",
        "| profile | prefilter | head | residual | dangerous (within head) | safe (within head) |",
        "|---|---|---|---|---|---|",
    ]
    for profile_name, breakdown in l2["combined_cascade_test_split"].items():
        lines.append(
            f"| {profile_name} | {_fmt_pct(breakdown['prefilter_frac'])} | {_fmt_pct(breakdown['head_frac'])} | "
            f"{_fmt_pct(breakdown['residual_frac'])} | {_fmt_pct(breakdown['dangerous_rate_within_head'])} | "
            f"{_fmt_pct(breakdown['safe_rate_within_head'])} |"
        )
    lines += [
        "",
        "## Lever 3 -- Time-delta + hash features (retrain)",
        "",
        f"- Timestamp coverage: {payload['timestamp_meta']['immich_api']['coverage_fraction']:.1%} via Immich "
        f"API (local cards.json alone: {payload['timestamp_meta']['local_card_coverage']['fraction']:.1%})",
        f"- Hash coverage: {payload['hash_meta']['coverage_fraction']:.1%} "
        f"({payload['hash_meta']['hash_algorithm']})",
        f"- Feature dimensionality: 384 (original) + 7 (5 delta-t buckets + 2 hamming) = "
        f"{l3['feature_dim']}",
        f"- Test accuracy: {l3['test_accuracy']:.3f} (original: {payload['sanity']['reproduced_test_accuracy']:.3f}, "
        f"delta {l3['test_accuracy'] - payload['sanity']['reproduced_test_accuracy']:+.3f})",
        f"- Test ROC-AUC: {l3['test_roc_auc']:.3f} (original: {payload['sanity']['reproduced_test_auc']:.3f}, "
        f"delta {l3['test_roc_auc'] - payload['sanity']['reproduced_test_auc']:+.3f})",
        "",
        "Lever 1 sweep redone on the retrained model:",
        "",
        "| target | reached | coverage | dangerous | safe |",
        "|---|---|---|---|---|",
    ]
    for target_key, headline in l3["asymmetric_band"]["headline_by_agreement_target"].items():
        if headline["reached"]:
            c = headline["cell"]
            lines.append(
                f"| {target_key} agreement | yes | {_fmt_pct(c['coverage'])} | "
                f"{_fmt_pct(c['dangerous_rate_same_given_teacher_different'])} | "
                f"{_fmt_pct(c['safe_rate_different_given_teacher_same'])} |"
            )
        else:
            lines.append(
                f"| {target_key} agreement | no | best={_fmt_pct(headline['best_agreement'])} | -- | -- |"
            )
    lines += [
        "",
        f"Fine-grid strict cell (retrained model): {_strict_line(l3['asymmetric_band']['strict_fine'])}",
        "",
        "## Final arithmetic",
        "",
        f"Best model for the combined cascade: **{final['best_model']}**.",
        "",
        (
            "The strict row below reaches the <=0.5% dangerous cap."
            if final["strict_cap_reached"]
            else "**Note**: the <=0.5% dangerous cap is not reached by either model at any coverage "
            '(see the fine-grid strict cells above) -- the "strict" row below is the closest achieved '
            "operating point, not a cap-satisfying one. Treat it as a lower bound on how strict this "
            "cascade can currently get, not as evidence the cross-moment bar is met."
        ),
        "",
        "### 6,384-pair bank (test-split rates, projected onto the whole bank)",
        "",
        "| profile | no call needed (prefilter) | head-decided | residual to 27B |",
        "|---|---|---|---|",
    ]
    for profile_name, breakdown in final["bank_6384"].items():
        lines.append(
            f"| {profile_name} | {_fmt_pct(breakdown['prefilter_frac'])} | {_fmt_pct(breakdown['head_frac'])} | "
            f"{_fmt_pct(breakdown['residual_frac'])} |"
        )
    lines += [
        "",
        "### 535-pair 2007 case (projected from bank rates)",
        "",
        "| profile | no call needed (prefilter) | head-decided | residual to 27B |",
        "|---|---|---|---|",
    ]
    for profile_name, breakdown in final["case_2007_535"]["projection_from_bank_rates"].items():
        n = 535
        lines.append(
            f"| {profile_name} | {_fmt_pct(breakdown['prefilter_frac'])} (~{round(breakdown['prefilter_frac'] * n)}) "
            f"| {_fmt_pct(breakdown['head_frac'])} (~{round(breakdown['head_frac'] * n)}) "
            f"| {_fmt_pct(breakdown['residual_frac'])} (~{round(breakdown['residual_frac'] * n)}) |"
        )
    empirical = final["case_2007_535"]["empirical_534_pair_subset"]
    measured = final["case_2007_535"]["measured_test_split_only"]
    shadow = final["case_2007_535"]["cross_check_shadow_report"]
    lines += [
        "",
        f"Empirical cross-check: {empirical['n_pairs']} of the 6,384 bank pairs have both ids in the "
        "2007 case's card asset universe (split breakdown: "
        f"train={empirical['split_breakdown']['train']}, cal={empirical['split_breakdown']['cal']}, "
        f"test={empirical['split_breakdown']['test']}). {empirical['note']}",
        "",
        f"**Measured** (not projected) on just its {measured['n_pairs']}-pair test-split slice -- the only "
        "part of this 534-pair subset the retrained model never saw during training or calibration "
        f"(small-n, read as directional, not a precise rate):",
        "",
        "| profile | no call needed (prefilter) | head-decided | residual to 27B |",
        "|---|---|---|---|",
    ]
    if measured["breakdown"] is None:
        lines.append("| -- | no 2007-universe pairs landed in the test split | -- | -- |")
    else:
        for profile_name, breakdown in measured["breakdown"].items():
            lines.append(
                f"| {profile_name} | {_fmt_pct(breakdown['prefilter_frac'])} | {_fmt_pct(breakdown['head_frac'])} | "
                f"{_fmt_pct(breakdown['residual_frac'])} |"
            )
    lines += [
        "",
        f"Independent prior measurement (shadow-2007-report.json, single symmetric threshold "
        f"{shadow['threshold']}, no prefilter, no retrain): {shadow['n_decided']}/{shadow['case_pair_count']} "
        f"decided ({shadow['coverage_vs_case_pairs']:.1%}), {shadow['agreement_on_decided_subset']:.1%} "
        "agreement on the decided subset -- a real production run, not a projection, given as an external "
        "sanity anchor for the numbers above.",
        "",
        f"Wall time: {payload['wall_time_seconds']:.1f}s.",
    ]
    return "\n".join(lines) + "\n"


# --- orchestration -------------------------------------------------------------


def main() -> int:
    started = time.monotonic()
    bank = load_bank()
    sanity = sanity_check_against_original_report(bank)
    print(f"sanity check: {sanity}", flush=True)

    test_mask = bank["split_arr"] == "test"
    test_y = bank["labels"][test_mask]

    # Lever 1
    p_test_original = bank["original_model"].predict_proba(bank["features"][test_mask])[:, 1]
    lever1 = lever1_report(p_test_original, test_y, label="original")
    print("lever 1 done", flush=True)

    # Lever 2
    distance_all = raw_cosine_distance(bank["pairs"], bank["id_to_row"], bank["embeddings"])
    same_mask_all = bank["labels"]
    lever2_core = lever2_prefilter(distance_all, same_mask_all)
    distance_test = distance_all[test_mask]
    loose_cell = (
        lever1["headline_by_agreement_target"]["95pct"].get("cell")
        or lever1["headline_by_agreement_target"]["95pct"]["cell_at_best"]
    )
    strict_cell = lever1["strict_fine"]["cell"]
    lever2_core["combined_cascade_test_split"] = {
        "loose_95pct_agreement": cascade_breakdown(
            distance_test,
            p_test_original,
            test_y,
            lever2_core["tau_far"],
            loose_cell["t_same"],
            loose_cell["t_diff"],
        ),
        "strict_dangerous_le_0_5pct": cascade_breakdown(
            distance_test,
            p_test_original,
            test_y,
            lever2_core["tau_far"],
            strict_cell["t_same"],
            strict_cell["t_diff"],
        ),
        "loose_95pct_agreement_with_margin": cascade_breakdown(
            distance_test,
            p_test_original,
            test_y,
            lever2_core["tau_margin"],
            loose_cell["t_same"],
            loose_cell["t_diff"],
        ),
        "strict_dangerous_le_0_5pct_with_margin": cascade_breakdown(
            distance_test,
            p_test_original,
            test_y,
            lever2_core["tau_margin"],
            strict_cell["t_same"],
            strict_cell["t_diff"],
        ),
    }
    print("lever 2 done", flush=True)

    # Lever 3
    timestamp_payload = json.loads((MATRIX_DIR / "timestamps.json").read_text())
    hash_payload = json.loads((MATRIX_DIR / "hashes.json").read_text())
    timestamps = load_timestamps()
    hashes = load_hashes()
    extra = build_extra_features(bank["pairs"], timestamps, hashes)
    retrain = retrain_with_extra_features(bank, extra)
    lever3_asymmetric = lever1_report(retrain["p_test"], retrain["test_y"], label="retrained")
    lever3 = {
        "feature_dim": retrain["feature_dim"],
        "chosen_c": retrain["chosen_c"],
        "cv_scores": retrain["cv_scores"],
        "test_accuracy": retrain["test_accuracy"],
        "test_roc_auc": retrain["test_roc_auc"],
        "confusion": retrain["confusion"],
        "asymmetric_band": lever3_asymmetric,
    }
    print(
        f"lever 3 done: test accuracy {retrain['test_accuracy']:.3f}, AUC {retrain['test_roc_auc']:.3f}",
        flush=True,
    )

    # Final arithmetic: pick the best model by test AUC, combine with the prefilter.
    best_is_retrained = retrain["test_roc_auc"] >= sanity["reproduced_test_auc"]
    best_model_name = "lever3_retrained" if best_is_retrained else "original"
    best_p_test = retrain["p_test"] if best_is_retrained else p_test_original
    best_test_y = retrain["test_y"] if best_is_retrained else test_y
    best_lever1 = lever3_asymmetric if best_is_retrained else lever1
    best_loose = (
        best_lever1["headline_by_agreement_target"]["95pct"].get("cell")
        or best_lever1["headline_by_agreement_target"]["95pct"]["cell_at_best"]
    )
    best_strict_point = best_lever1["strict_fine"]
    best_strict = best_strict_point["cell"]

    bank_breakdown = {
        "loose_95pct_agreement": cascade_breakdown(
            distance_test,
            best_p_test,
            best_test_y,
            lever2_core["tau_far"],
            best_loose["t_same"],
            best_loose["t_diff"],
        ),
        "strict_dangerous_le_0_5pct": cascade_breakdown(
            distance_test,
            best_p_test,
            best_test_y,
            lever2_core["tau_far"],
            best_strict["t_same"],
            best_strict["t_diff"],
        ),
    }

    universe_2007 = load_2007_asset_universe()
    mask_2007_all = pairs_in_universe_mask(bank["pairs"], universe_2007)
    empirical = empirical_2007_subset(bank, mask_2007_all)
    mask_2007_test = mask_2007_all[test_mask]
    n_2007_test = int(mask_2007_test.sum())
    measured_test_split_only = (
        {
            name: cascade_breakdown(
                distance_test[mask_2007_test],
                best_p_test[mask_2007_test],
                best_test_y[mask_2007_test],
                lever2_core["tau_far"],
                cell["t_same"],
                cell["t_diff"],
            )
            for name, cell in (
                ("loose_95pct_agreement", best_loose),
                ("strict_dangerous_le_0_5pct", best_strict),
            )
        }
        if n_2007_test
        else None
    )
    shadow_report = json.loads((MATRIX_DIR / "shadow-2007-report.json").read_text())
    projection_2007 = {
        name: {
            "prefilter_frac": breakdown["prefilter_frac"],
            "head_frac": breakdown["head_frac"],
            "residual_frac": breakdown["residual_frac"],
        }
        for name, breakdown in bank_breakdown.items()
    }

    final_arithmetic = {
        "best_model": best_model_name,
        "strict_cap_reached": best_strict_point["reached"],
        "bank_6384": bank_breakdown,
        "case_2007_535": {
            "projection_from_bank_rates": projection_2007,
            "empirical_534_pair_subset": empirical,
            "measured_test_split_only": {
                "n_pairs": n_2007_test,
                "breakdown": measured_test_split_only,
            },
            "cross_check_shadow_report": {
                "case_pair_count": shadow_report["case_pair_count"],
                "threshold": shadow_report["abstention_band"]["threshold"],
                "n_decided": shadow_report["shadow_result"]["n_decided"],
                "coverage_vs_case_pairs": shadow_report["shadow_result"]["coverage_vs_case_pairs"],
                "agreement_on_decided_subset": shadow_report["shadow_result"][
                    "agreement_on_decided_subset"
                ],
            },
        },
    }

    payload: dict[str, Any] = {
        "schema_version": "pairhead-levers-v1",
        "sanity": sanity,
        "timestamp_meta": {
            k: v for k, v in timestamp_payload.items() if k not in ("timestamps", "failed_ids")
        },
        "hash_meta": {k: v for k, v in hash_payload.items() if k not in ("hashes", "failed_ids")},
        "lever1": lever1,
        "lever2": lever2_core,
        "lever3": lever3,
        "final_arithmetic": final_arithmetic,
        "framing": {
            "owner_ruling": "2026-08-30",
            "loose_profile_rationale": (
                "Banked pairs are within-moment selects comparisons; a wrong verdict swaps one frame for a "
                "near-identical sibling of the same scene, invisible in the finished video. Owner's bar: "
                "'95% working is better than 100%'."
            ),
            "strict_profile_rationale": (
                "Reserved for the separate, barely-banked cross-moment duplicate-confirmation pass, where a "
                "wrong 'same' verdict can drop a whole occasion."
            ),
        },
        "wall_time_seconds": time.monotonic() - started,
    }

    (MATRIX_DIR / "levers-report.json").write_text(
        json.dumps(payload, indent=2, default=_json_default)
    )
    (MATRIX_DIR / "levers-report.md").write_text(render_markdown(payload))
    print(f"done in {payload['wall_time_seconds']:.1f}s", flush=True)
    return 0


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return None  # p_test/test_y arrays are dropped from the JSON, not serialized
    if isinstance(obj, np.generic):
        return obj.item()
    return str(obj)


if __name__ == "__main__":
    raise SystemExit(main())
