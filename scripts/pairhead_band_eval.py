"""Cross-fitted abstain-band evaluation for a scalar instrument on a labelled pair set.

Used by probe_pairhead_specialist_report.py to score every specialist instrument
on the pairwise cascade's residual band under one protocol.

Every instrument reduces to one number per pair where "higher means same".
Thresholds fitted on the residual set and then scored on the residual set are
optimistic, so each headline is reported twice: the in-sample optimum (ceiling)
and a 5-fold cross-fitted number at seed 42 (thresholds chosen on 4 folds,
applied to the held-out fifth) -- the honest one.
"""

from __future__ import annotations

import numpy as np

SEED = 42
FOLDS = 5
MAX_CANDIDATES = 160


def _candidates(scores: np.ndarray) -> np.ndarray:
    quantiles = np.linspace(0.0, 1.0, MAX_CANDIDATES)
    return np.unique(np.quantile(scores, quantiles))


def describe(scores: np.ndarray, labels: np.ndarray) -> dict:
    same, diff = scores[labels], scores[~labels]

    def stats(values: np.ndarray) -> dict:
        if not len(values):
            return {}
        return {
            "n": int(len(values)),
            "mean": float(values.mean()),
            "p10": float(np.percentile(values, 10)),
            "p25": float(np.percentile(values, 25)),
            "median": float(np.median(values)),
            "p75": float(np.percentile(values, 75)),
            "p90": float(np.percentile(values, 90)),
        }

    pooled_sd = float(np.sqrt((same.var() + diff.var()) / 2)) if len(same) and len(diff) else 0.0
    return {
        "same": stats(same),
        "different": stats(diff),
        "cohens_d": (
            float((same.mean() - diff.mean()) / pooled_sd) if pooled_sd > 0 else None
        ),
        "auc": roc_auc(scores, labels),
    }


def roc_auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    if labels.all() or (~labels).all():
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    sorted_scores = scores[order]
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def best_single_threshold(scores: np.ndarray, labels: np.ndarray) -> dict:
    best = {"threshold": None, "accuracy": 0.0}
    for t in _candidates(scores):
        accuracy = float(np.mean((scores >= t) == labels))
        if accuracy > best["accuracy"]:
            best = {"threshold": float(t), "accuracy": accuracy}
    return best


def band_metrics(scores: np.ndarray, labels: np.ndarray, lo: float, hi: float) -> dict:
    call_same = scores >= hi
    call_diff = scores <= lo
    covered = call_same | call_diff
    n_covered = int(covered.sum())
    if n_covered == 0:
        return {"coverage": 0.0, "agreement": None, "n_covered": 0}
    agreement = float(np.mean(call_same[covered] == labels[covered]))
    dangerous_den = int((covered & ~labels).sum())
    dangerous_num = int((covered & ~labels & call_same).sum())
    safe_den = int((covered & labels).sum())
    safe_num = int((covered & labels & call_diff).sum())
    return {
        "lo": float(lo),
        "hi": float(hi),
        "coverage": n_covered / len(scores),
        "agreement": agreement,
        "n_covered": n_covered,
        "dangerous_rate": dangerous_num / dangerous_den if dangerous_den else None,
        "dangerous_n": dangerous_den,
        "dangerous_count": dangerous_num,
        "safe_rate": safe_num / safe_den if safe_den else None,
        "safe_n": safe_den,
        "safe_count": safe_num,
    }


def best_band(scores: np.ndarray, labels: np.ndarray, target: float) -> dict | None:
    """Max-coverage (lo, hi) whose agreement on this population is >= target."""
    grid = _candidates(scores)
    best = None
    for lo in grid:
        for hi in grid:
            if hi < lo:
                continue
            cell = band_metrics(scores, labels, lo, hi)
            if cell["agreement"] is None or cell["agreement"] < target:
                continue
            if best is None or cell["coverage"] > best["coverage"]:
                best = cell
    return best


def crossfit_band(scores: np.ndarray, labels: np.ndarray, target: float) -> dict:
    """Honest coverage/agreement: band chosen on 4 folds, applied to the fifth."""
    rng = np.random.default_rng(SEED)
    fold = rng.permutation(len(scores)) % FOLDS
    covered_total = correct_total = 0
    dangerous_num = dangerous_den = safe_num = safe_den = 0
    per_fold = []
    for k in range(FOLDS):
        held = fold == k
        band = best_band(scores[~held], labels[~held], target)
        if band is None:
            per_fold.append({"fold": k, "band": None, "n_covered": 0})
            continue
        s, y = scores[held], labels[held]
        call_same = s >= band["hi"]
        call_diff = s <= band["lo"]
        covered = call_same | call_diff
        covered_total += int(covered.sum())
        correct_total += int((call_same[covered] == y[covered]).sum())
        dangerous_den += int((covered & ~y).sum())
        dangerous_num += int((covered & ~y & call_same).sum())
        safe_den += int((covered & y).sum())
        safe_num += int((covered & y & call_diff).sum())
        per_fold.append(
            {
                "fold": k,
                "lo": band["lo"],
                "hi": band["hi"],
                "n_held": int(held.sum()),
                "n_covered": int(covered.sum()),
            }
        )
    return {
        "target_agreement": target,
        "coverage": covered_total / len(scores),
        "agreement": correct_total / covered_total if covered_total else None,
        "n_covered": covered_total,
        "n_total": int(len(scores)),
        "dangerous_rate": dangerous_num / dangerous_den if dangerous_den else None,
        "dangerous_n": dangerous_den,
        "dangerous_count": dangerous_num,
        "safe_rate": safe_num / safe_den if safe_den else None,
        "safe_n": safe_den,
        "safe_count": safe_num,
        "per_fold": per_fold,
    }


SWEEP_TARGETS = (0.85, 0.875, 0.90, 0.925, 0.95, 0.96, 0.97, 0.98, 0.99)
WILSON_Z = 1.96


def wilson_lower(successes: int, total: int, z: float = WILSON_Z) -> float | None:
    """Lower end of the Wilson interval for a proportion.

    The headline points are picked as the max-coverage cell whose *measured*
    agreement clears a floor, over a ladder of nine targets -- a max over noisy
    estimates, which biases the winner upward. At the coverages reached here
    (tens of pairs) that bias is not small, so every agreement is reported with
    its lower bound as well as its point estimate.
    """
    if total == 0:
        return None
    phat = successes / total
    denominator = 1 + z**2 / total
    centre = phat + z**2 / (2 * total)
    margin = z * np.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2))
    return float((centre - margin) / denominator)


def achieved_agreement_curve(scores: np.ndarray, labels: np.ndarray) -> list[dict]:
    """Cross-fitted (coverage, ACHIEVED agreement) for a ladder of band targets.

    The headline the brief asks for is coverage at >=95% *achieved* agreement,
    which is not the same thing as coverage at a 95% *target*: a band fitted to
    hit 95% on four folds routinely lands below it on the fifth. Only the
    achieved column is allowed to answer the question.
    """
    curve = []
    for target in SWEEP_TARGETS:
        cell = crossfit_band(scores, labels, target)
        curve.append(
            {
                "target": target,
                "coverage": cell["coverage"],
                "agreement": cell["agreement"],
                "dangerous_rate": cell["dangerous_rate"],
                "dangerous_count": cell["dangerous_count"],
                "dangerous_n": cell["dangerous_n"],
                "safe_rate": cell["safe_rate"],
                "safe_count": cell["safe_count"],
                "safe_n": cell["safe_n"],
                "n_covered": cell["n_covered"],
                "agreement_wilson_lo": (
                    wilson_lower(
                        round((cell["agreement"] or 0) * cell["n_covered"]), cell["n_covered"]
                    )
                    if cell["n_covered"]
                    else None
                ),
            }
        )
    return curve


def best_at_achieved(curve: list[dict], floor: float) -> dict | None:
    eligible = [c for c in curve if c["agreement"] is not None and c["agreement"] >= floor]
    if not eligible:
        return None
    return max(eligible, key=lambda c: c["coverage"])


def _best_at_wilson(curve: list[dict], floor: float) -> dict | None:
    eligible = [
        c
        for c in curve
        if c["agreement_wilson_lo"] is not None and c["agreement_wilson_lo"] >= floor
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda c: c["coverage"])


def evaluate(
    name: str,
    residual_scores: np.ndarray,
    residual_labels: np.ndarray,
    control_scores: np.ndarray,
    control_labels: np.ndarray,
    targets: tuple[float, ...] = (0.95, 0.90),
) -> dict:
    single = best_single_threshold(residual_scores, residual_labels)
    out = {
        "instrument": name,
        "residual_distribution": describe(residual_scores, residual_labels),
        "control_distribution": describe(control_scores, control_labels),
        "best_single_threshold_in_sample": single,
        "majority_baseline_residual": float(
            max(residual_labels.mean(), 1 - residual_labels.mean())
        ),
        "bands": {},
    }
    for target in targets:
        key = f"{int(target * 100)}pct"
        in_sample = best_band(residual_scores, residual_labels, target)
        crossfit = crossfit_band(residual_scores, residual_labels, target)
        control = None
        if in_sample is not None:
            control = band_metrics(
                control_scores, control_labels, in_sample["lo"], in_sample["hi"]
            )
        out["bands"][key] = {
            "in_sample_ceiling": in_sample,
            "crossfit": crossfit,
            "control_at_residual_band": control,
        }
    curve = achieved_agreement_curve(residual_scores, residual_labels)
    out["crossfit_curve"] = curve
    out["headline_achieved"] = {
        "ge_95pct": best_at_achieved(curve, 0.95),
        "ge_90pct": best_at_achieved(curve, 0.90),
    }
    out["headline_achieved_wilson_lo"] = {
        "ge_95pct": _best_at_wilson(curve, 0.95),
        "ge_90pct": _best_at_wilson(curve, 0.90),
    }
    # Single-threshold control sanity: the residual-fitted split point on control.
    if single["threshold"] is not None:
        out["control_accuracy_at_residual_threshold"] = float(
            np.mean((control_scores >= single["threshold"]) == control_labels)
        )
    return out
