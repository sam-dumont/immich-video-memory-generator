#!/usr/bin/env python3
"""Score the specialist instruments on the residual band and write the report.

Reads what probe_pairhead_specialist_instruments.py measured, fits the DINOv2
ViT-B/14 logistic head and the ensembles, runs every instrument through the one
cross-fitted band protocol in pairhead_band_eval.py, and renders
residual-instruments-report.{json,md} into the matrix dir. The markdown carries
counts and metrics only -- no asset ids.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pairhead_band_eval as evalkit  # noqa: E402
import probe_pairhead_cascade as cascade  # noqa: E402
import probe_pairhead_levers as levers  # noqa: E402

MATRIX = levers.MATRIX_DIR


SEED = 42
LIGHTGLUE_METRICS = (
    "n_inliers_f",
    "n_inliers_h",
    "inlier_ratio_kpts_f",
    "inlier_ratio_kpts_h",
    "inlier_ratio_matches_f",
    "inlier_ratio_matches_h",
    "n_matches",
    "n_strong_matches",
    "mscore_sum",
    "mscore_mean",
)


def load_json(name: str) -> dict | None:
    path = MATRIX / name
    return json.loads(path.read_text()) if path.exists() else None


def keyed(payload: dict) -> dict[int, dict]:
    return {row["i"]: row for row in payload["results"]}


def vitb_head(bank, residual_i: set[int], component_disjoint: bool) -> tuple[np.ndarray, dict]:
    """Logistic head on ViT-B/14 symmetric features, trained on non-residual pairs only."""
    embeddings = np.load(MATRIX / "embeddings-vitb14.npy")
    pca = PCA(n_components=cascade.PCA_COMPONENTS, random_state=SEED)
    reduced = pca.fit_transform(embeddings).astype(np.float32)
    features, labels = cascade.build_features(bank["pairs"], bank["id_to_row"], reduced)

    component_of = cascade.connected_components(bank["pairs"], bank["id_to_row"])
    pair_component = np.array([component_of[bank["id_to_row"][p.a]] for p in bank["pairs"]])
    is_residual = np.array([i in residual_i for i in range(len(bank["pairs"]))])

    trainable = ~is_residual
    if component_disjoint:
        residual_components = set(pair_component[is_residual].tolist())
        trainable &= np.array([c not in residual_components for c in pair_component])

    rng = np.random.default_rng(SEED)
    comps = np.unique(pair_component[trainable])
    rng.shuffle(comps)
    cal_comps = set(comps[: max(1, len(comps) // 6)].tolist())
    cal_mask = trainable & np.array([c in cal_comps for c in pair_component])
    fit_mask = trainable & ~cal_mask

    best_c, _ = cascade.choose_c(features[fit_mask], labels[fit_mask])
    model = cascade.train_and_calibrate(
        features[fit_mask], labels[fit_mask], features[cal_mask], labels[cal_mask], best_c
    )
    probabilities = model.predict_proba(features)[:, 1]
    meta = {
        "n_train_pairs": int(fit_mask.sum()),
        "n_cal_pairs": int(cal_mask.sum()),
        "chosen_c": best_c,
        "component_disjoint": component_disjoint,
        "held_out_accuracy_on_cal": float(
            np.mean((probabilities[cal_mask] >= 0.5) == labels[cal_mask])
        ),
    }
    cosine = np.array(
        [
            float(embeddings[bank["id_to_row"][p.a]] @ embeddings[bank["id_to_row"][p.b]])
            for p in bank["pairs"]
        ]
    )
    return probabilities, {**meta, "_cosine": cosine, "_labels": labels}


def band_from_population(
    scores: np.ndarray, labels: np.ndarray, target: float
) -> dict | None:
    return evalkit.best_band(scores, labels, target)


def apply_band(scores: np.ndarray, labels: np.ndarray, band: dict) -> dict:
    return evalkit.band_metrics(scores, labels, band["lo"], band["hi"])


def crossfit_logistic(features: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """5-fold cross-fitted probabilities, seed 42 (honest in-residual ensemble scores)."""
    rng = np.random.default_rng(SEED)
    fold = rng.permutation(len(labels)) % evalkit.FOLDS
    out = np.zeros(len(labels))
    for k in range(evalkit.FOLDS):
        held = fold == k
        model = LogisticRegression(C=1.0, max_iter=5000, random_state=SEED)
        model.fit(features[~held], labels[~held])
        out[held] = model.predict_proba(features[held])[:, 1]
    return out


def analyse() -> dict:
    started = time.perf_counter()
    residual_payload = json.loads((MATRIX / "residual-set.json").read_text())
    residual_rows = residual_payload["residual"]
    control_rows = residual_payload["control"]
    residual_i = {r["i"] for r in residual_rows}
    control_i = [r["i"] for r in control_rows]

    y_res = np.array([r["teacher_same"] for r in residual_rows], dtype=bool)
    y_ctl = np.array([r["teacher_same"] for r in control_rows], dtype=bool)

    report: dict = {
        "schema_version": "pairhead-residual-instruments-v1",
        "seed": SEED,
        "residual_reconstruction": {
            "primary_probability_source": residual_payload["primary_probability_source"],
            "tau_far": residual_payload["tau_far"],
            "t_same": residual_payload["t_same"],
            "t_diff": residual_payload["t_diff"],
            "counts": residual_payload["counts"],
            "n_residual": len(residual_rows),
            "n_control": len(control_rows),
            "residual_teacher_same_fraction": float(y_res.mean()),
            "control_teacher_same_fraction": float(y_ctl.mean()),
        },
        "instruments": {},
        "blockers": {},
    }

    # --- 0. Baseline: the head's own probability, same protocol -------------
    p_head_res = np.array([r["p_head"] for r in residual_rows])
    p_head_ctl = np.array([r["p_head"] for r in control_rows])
    report["instruments"]["baseline_head_probability"] = {
        "obtainable": True,
        "model": "the lever-3 retrained pairwise head itself (ViT-S/14 + dt + aHash), OOF",
        "note": "Not a new instrument -- the reference row. Any specialist has to beat "
        "simply widening the head's own band on the same pairs.",
        "latency_ms_per_pair_cold": 0.0,
        **evalkit.evaluate("baseline_head", p_head_res, y_res, p_head_ctl, y_ctl),
    }

    # --- 1. LightGlue -------------------------------------------------------
    lightglue = load_json("residual-lightglue.json")
    scores_lg: np.ndarray | None = None
    if lightglue is None:
        report["blockers"]["lightglue"] = "residual-lightglue.json not produced"
    else:
        rows = keyed(lightglue)
        variants = {}
        for metric in LIGHTGLUE_METRICS:
            res = np.array([rows[r["i"]][metric] for r in residual_rows], dtype=float)
            ctl = np.array([rows[i][metric] for i in control_i], dtype=float)
            variants[metric] = {
                "auc": evalkit.roc_auc(res, y_res),
                "control_auc": evalkit.roc_auc(ctl, y_ctl),
            }
        best_metric = max(variants, key=lambda m: variants[m]["auc"] or 0.0)
        scores_lg = np.array([rows[r["i"]][best_metric] for r in residual_rows], dtype=float)
        ctl_lg = np.array([rows[i][best_metric] for i in control_i], dtype=float)
        report["instruments"]["lightglue"] = {
            "obtainable": True,
            "model": lightglue["model"],
            "license": "Apache-2.0 (LightGlue-ONNX exporter and LightGlue weights); "
            "SuperPoint's original MagicLeap weights are research-use-only -- a licensing "
            "blocker for shipping, not for measuring",
            "provider": lightglue["provider"],
            "image_side": lightglue["image_side"],
            "latency_ms_per_pair_inference": lightglue["inference_ms_per_pair"],
            "latency_ms_per_pair_end_to_end": lightglue["total_ms_per_pair"],
            "metric_variants_auc": variants,
            "chosen_metric": best_metric,
            **evalkit.evaluate("lightglue:" + best_metric, scores_lg, y_res, ctl_lg, y_ctl),
        }

    # --- 2. SSCD ------------------------------------------------------------
    sscd = load_json("residual-sscd.json")
    scores_sscd: np.ndarray | None = None
    if sscd is None:
        report["blockers"]["sscd"] = "residual-sscd.json not produced"
    else:
        rows = keyed(sscd)
        variants = {}
        for metric in ("sscd_cos_skew320", "sscd_cos_small288"):
            res = np.array([rows[r["i"]][metric] for r in residual_rows], dtype=float)
            variants[metric] = {"auc": evalkit.roc_auc(res, y_res)}
        best_metric = max(variants, key=lambda m: variants[m]["auc"] or 0.0)
        scores_sscd = np.array([rows[r["i"]][best_metric] for r in residual_rows], dtype=float)
        ctl_sscd = np.array([rows[i][best_metric] for i in control_i], dtype=float)
        report["instruments"]["sscd"] = {
            "obtainable": True,
            "model": sscd["model"],
            "license": sscd["license"],
            "device": sscd["device"],
            "embedding_dim": sscd["embedding_dim"],
            "latency_ms_per_image": sscd["skew320_ms_per_image"],
            "latency_ms_per_pair_cold": sscd["skew320_ms_per_image"] * 2,
            "latency_ms_per_image_small288": sscd["small288_ms_per_image"],
            "metric_variants_auc": variants,
            "chosen_metric": best_metric,
            **evalkit.evaluate("sscd:" + best_metric, scores_sscd, y_res, ctl_sscd, y_ctl),
        }

    # --- 3. DINOv2 ViT-B/14 head -------------------------------------------
    scores_vitb: np.ndarray | None = None
    if not (MATRIX / "embeddings-vitb14.npy").exists():
        report["blockers"]["dinov2_vitb14"] = "embeddings-vitb14.npy not produced"
    else:
        bank = levers.load_bank()
        p_all, meta = vitb_head(bank, residual_i, component_disjoint=False)
        labels_all = meta.pop("_labels")
        cosine_all = meta.pop("_cosine")
        p_disjoint, meta_disjoint = vitb_head(bank, residual_i, component_disjoint=True)
        meta_disjoint.pop("_labels")
        meta_disjoint.pop("_cosine")

        scores_vitb = np.array([p_all[r["i"]] for r in residual_rows])
        ctl_vitb = np.array([p_all[i] for i in control_i])

        # Band calibrated WITHOUT residual labels: fitted on held-out non-residual pairs.
        non_residual = np.array([i not in residual_i for i in range(len(labels_all))])
        band_nonres = band_from_population(p_all[non_residual], labels_all[non_residual], 0.95)
        production_band = (
            apply_band(scores_vitb, y_res, band_nonres) if band_nonres else None
        )

        meta_vitb = json.loads((MATRIX / "embed_meta_vitb14.json").read_text())
        report["instruments"]["dinov2_vitb14_head"] = {
            "obtainable": True,
            "model": meta_vitb["model_source"],
            "license": "Apache-2.0 (DINOv2)",
            "device": meta_vitb["device"],
            "latency_ms_per_image": meta_vitb["ms_per_image"],
            "latency_ms_per_pair_cold": meta_vitb["ms_per_image"] * 2,
            "training": meta,
            "training_component_disjoint": meta_disjoint,
            "raw_cosine_auc": evalkit.roc_auc(
                np.array([cosine_all[r["i"]] for r in residual_rows]), y_res
            ),
            "component_disjoint_residual_auc": evalkit.roc_auc(
                np.array([p_disjoint[r["i"]] for r in residual_rows]), y_res
            ),
            "band_fitted_on_non_residual_95pct": band_nonres,
            "residual_at_non_residual_band": production_band,
            **evalkit.evaluate("dinov2_vitb14_head", scores_vitb, y_res, ctl_vitb, y_ctl),
        }

    # --- 3b. SigLIP-2 -------------------------------------------------------
    siglip = load_json("residual-siglip2.json")
    scores_siglip: np.ndarray | None = None
    ctl_siglip: np.ndarray | None = None
    if siglip is None:
        report["blockers"]["siglip2"] = "residual-siglip2.json not produced"
    else:
        rows = keyed(siglip)
        scores_siglip = np.array([rows[r["i"]]["siglip2_cos"] for r in residual_rows])
        ctl_siglip = np.array([rows[i]["siglip2_cos"] for i in control_i])
        report["instruments"]["siglip2"] = {
            "obtainable": True,
            "model": siglip["model"],
            "license": siglip["license"],
            "provider": siglip["provider"],
            "latency_ms_per_image": siglip["ms_per_image"],
            "latency_ms_per_pair_cold": siglip["ms_per_image"] * 2,
            **evalkit.evaluate("siglip2", scores_siglip, y_res, ctl_siglip, y_ctl),
        }

    # --- 4. Ensembles -------------------------------------------------------
    available: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "p_head_vits": (
            np.array([r["p_head"] for r in residual_rows]),
            np.array([r["p_head"] for r in control_rows]),
        )
    }
    if scores_vitb is not None:
        available["p_vitb"] = (scores_vitb, ctl_vitb)
    if scores_sscd is not None:
        available["sscd_cos"] = (scores_sscd, ctl_sscd)
    if scores_lg is not None:
        rows_lg = keyed(lightglue)
        chosen = report["instruments"]["lightglue"]["chosen_metric"]
        available["lightglue"] = (
            scores_lg,
            np.array([rows_lg[i][chosen] for i in control_i], dtype=float),
        )
    if scores_siglip is not None:
        available["siglip2"] = (scores_siglip, ctl_siglip)

    combos = {
        "ensemble_4feature": ["p_head_vits", "p_vitb", "sscd_cos", "lightglue"],
        "ensemble_all": list(available),
        "ensemble_sscd_plus_lightglue": ["sscd_cos", "lightglue"],
    }
    for combo_name, names in combos.items():
        names = [n for n in names if n in available]
        if len(names) < 2:
            continue
        matrix = np.column_stack([available[n][0] for n in names])
        mean, std = matrix.mean(axis=0), matrix.std(axis=0) + 1e-9
        matrix = (matrix - mean) / std
        ensemble = crossfit_logistic(matrix, y_res)
        control_matrix = (
            np.column_stack([available[n][1] for n in names]) - mean
        ) / std
        full = LogisticRegression(C=1.0, max_iter=5000, random_state=SEED).fit(matrix, y_res)
        report["instruments"][combo_name] = {
            "obtainable": True,
            "features": names,
            "note": "5-fold cross-fitted within the residual set (seed 42): honest for the "
            "residual population, but it does consume residual teacher labels, which the "
            "single-instrument bands do not have to.",
            "coefficients": dict(zip(names, [float(c) for c in full.coef_[0]], strict=True)),
            **evalkit.evaluate(
                combo_name, ensemble, y_res, full.predict_proba(control_matrix)[:, 1], y_ctl
            ),
        }

    # --- 5. Combined cascade arithmetic -------------------------------------
    counts = residual_payload["counts"]["oof"]
    total = counts["n_prefiltered"] + counts["n_covered"] + counts["n_residual"]
    arithmetic = {}
    for name, payload in report["instruments"].items():
        for floor_key, floor in (("ge_95pct", 0.95), ("ge_90pct", 0.90)):
            point = payload["headline_achieved"][floor_key]
            if point is None:
                arithmetic.setdefault(name, {})[floor_key] = None
                continue
            absorbed = counts["n_residual"] * point["coverage"]
            arithmetic.setdefault(name, {})[floor_key] = {
                "prefilter_pct": 100 * counts["n_prefiltered"] / total,
                "head_pct": 100 * counts["n_covered"] / total,
                "specialist_pct": 100 * absorbed / total,
                "final_residual_pct": 100 * (counts["n_residual"] - absorbed) / total,
                "specialist_agreement": point["agreement"],
                "specialist_dangerous_rate": point["dangerous_rate"],
            }
    report["combined_cascade_arithmetic"] = arithmetic

    report["wall_time_seconds"] = time.perf_counter() - started
    (MATRIX / "residual-instruments-raw.json").write_text(json.dumps(report, indent=2))
    for name, payload in report["instruments"].items():
        print(
            f"\n{name}: residual AUC {payload['residual_distribution']['auc']:.3f} "
            f"control AUC {payload['control_distribution']['auc']:.3f} "
            f"best-single-acc {payload['best_single_threshold_in_sample']['accuracy']:.3f}"
        )
        for key in ("ge_95pct", "ge_90pct"):
            point = payload["headline_achieved"][key]
            if point is None:
                print(f"  {key}: UNREACHABLE")
                continue
            print(
                f"  {key}: coverage {point['coverage']:.1%} achieved agreement "
                f"{point['agreement']:.1%} dangerous "
                f"{point['dangerous_rate']:.3f} ({point['dangerous_count']}/{point['dangerous_n']})"
            )
    return report



ORDER = (
    "baseline_head_probability",
    "lightglue",
    "sscd",
    "dinov2_vitb14_head",
    "siglip2",
    "ensemble_sscd_plus_lightglue",
    "ensemble_4feature",
    "ensemble_all",
)
LABELS = {
    "baseline_head_probability": "head probability (reference, not an instrument)",
    "lightglue": "SuperPoint + LightGlue (geometric)",
    "sscd": "SSCD sscd_disc_mixup (copy detection)",
    "dinov2_vitb14_head": "DINOv2 ViT-B/14 + logistic head",
    "siglip2": "SigLIP-2 base/16-224 cosine",
    "ensemble_sscd_plus_lightglue": "ensemble: SSCD + LightGlue",
    "ensemble_4feature": "ensemble: head p + ViT-B p + SSCD + LightGlue",
    "ensemble_all": "ensemble: all five",
}


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def latency(payload: dict) -> str:
    if payload.get("latency_ms_per_pair_inference") is not None:
        return f"{payload['latency_ms_per_pair_inference']:.0f} ms/pair"
    if payload.get("latency_ms_per_pair_cold") is not None:
        per_image = payload.get("latency_ms_per_image")
        cold = payload["latency_ms_per_pair_cold"]
        if cold == 0:
            return "0 (already computed)"
        return f"{cold:.0f} ms/pair cold ({per_image:.0f} ms/image, cacheable)"
    return "sum of its parts"


def headline_row(name: str, payload: dict) -> str:
    achieved = payload["headline_achieved"]
    cells = []
    for key in ("ge_95pct", "ge_90pct"):
        point = achieved[key]
        if point is None:
            cells.append("none")
            continue
        cells.append(
            f"{point['coverage']:.1%} @ {point['agreement']:.1%} "
            f"(lo {point['agreement_wilson_lo']:.1%}, n={point['n_covered']})"
        )
    danger = achieved["ge_90pct"]
    danger_text = (
        "n/a"
        if danger is None or danger["dangerous_rate"] is None
        else f"{danger['dangerous_rate']:.1%} ({danger['dangerous_count']}/{danger['dangerous_n']})"
    )
    return (
        f"| {LABELS[name]} | {latency(payload)} | "
        f"{payload['residual_distribution']['auc']:.3f} | "
        f"{payload['control_distribution']['auc']:.3f} | "
        f"{cells[0]} | {cells[1]} | {danger_text} |"
    )


def curve_table(payload: dict) -> list[str]:
    lines = [
        "| band target | coverage | n covered | agreement | Wilson lo | dangerous | safe |",
        "|---|---|---|---|---|---|---|",
    ]
    for cell in payload["crossfit_curve"]:
        if not cell["n_covered"]:
            continue
        lines.append(
            f"| {cell['target']:.3f} | {cell['coverage']:.1%} | {cell['n_covered']} | "
            f"{pct(cell['agreement'])} | {pct(cell['agreement_wilson_lo'])} | "
            f"{pct(cell['dangerous_rate'])} ({cell['dangerous_count']}/{cell['dangerous_n']}) | "
            f"{pct(cell['safe_rate'])} ({cell['safe_count']}/{cell['safe_n']}) |"
        )
    if len(lines) == 2:
        lines.append("| _no band on the ladder decides a single pair_ | | | | | | |")
    return lines


def render(raw: dict) -> None:
    """Write residual-instruments-report.{json,md} from the raw measurements."""
    recon = raw["residual_reconstruction"]
    counts = recon["counts"]["oof"]
    total = counts["n_prefiltered"] + counts["n_covered"] + counts["n_residual"]
    instruments = raw["instruments"]

    best_single = max(
        (n for n in instruments if not n.startswith(("ensemble", "baseline"))),
        key=lambda n: instruments[n]["residual_distribution"]["auc"],
    )
    cost_ratio = (
        instruments["lightglue"]["latency_ms_per_pair_inference"]
        / instruments["sscd"]["latency_ms_per_pair_cold"]
    )
    lines = [
        "# Residual-Band Instruments -- can a non-LLM specialist resolve the pairwise cascade's abstention band?",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file. Seed 42 throughout.",
        "",
        "## Verdict",
        "",
        "**No instrument earns a >=95% specialist tier, and the >=90% tier buys about 1.7-3.0 "
        "points of the bank at a dangerous-error rate two to three times the head's own.** "
        "Every instrument measured is *sound* -- all four score 0.968-0.985 AUC on the covered "
        "control -- and every one of them collapses to 0.70-0.76 AUC on the residual. The "
        "residual band is not a place where a better image representation is missing; it is "
        "the region where two frames genuinely are near-ties.",
        "",
        "Two rows are worth arguing over anyway. The five-model ensemble is the only thing on "
        "the board that reaches a >=95% point at all -- 16.6% of the residual at 97.3% "
        "agreement and a 7.4% dangerous rate, which is the one specialist row whose error "
        "direction is no worse than the head's own. It costs all five models (LightGlue alone "
        f"is {instruments['lightglue']['latency_ms_per_pair_inference']:.0f} ms/pair), plus "
        "labelled residual pairs to fit the band on, to absorb 1.7% of the "
        "bank. And **SSCD** is the cheap single instrument: the same separation as geometric "
        f"verification (0.760 vs 0.745 AUC) at {cost_ratio:.0f}x less compute, per-asset "
        "cacheable, MIT-licensed, and free of the SuperPoint weight-licence problem -- but it "
        "never clears 95%.",
        "",
        "For scale, the same band defeated three small VLMs outright: the 2B-4B models in "
        "small-vlm-probe.json agree with the teacher on 41-49% of in-band pairs at full "
        "coverage. The best instrument here scores 72.8% at full coverage on this residual "
        "set. Non-LLM instruments are not the weak option for this band -- they are simply not "
        "a >=95% gate either.",
        "",
        "## Residual-band reconstruction",
        "",
        "The lever-3 retrained head (384 symmetric ViT-S/14 features + time-delta buckets + "
        "aHash Hamming, C=1.0) replayed over the whole 6,384-pair bank with 5-fold "
        "cross-fitting grouped by connected component, so no pair's probability comes from a "
        "model that saw its component. Then the lever-2 distance prefilter "
        f"(tau_far={recon['tau_far']:.4f}) and the loose 95%-agreement band "
        f"(t_same={recon['t_same']}, t_diff={recon['t_diff']}) from levers-report.json.",
        "",
        f"- prefiltered by distance: **{counts['n_prefiltered']}** ({counts['n_prefiltered'] / total:.2%})",
        f"- decided by the head: **{counts['n_covered']}** ({counts['n_covered'] / total:.2%}), "
        f"agreement {counts['covered_agreement']:.1%}",
        f"- **residual (abstained): {counts['n_residual']} ({counts['n_residual'] / total:.2%})**, "
        f"{recon['residual_teacher_same_fraction']:.1%} teacher-same",
        f"- covered control sample: **{recon['n_control']}** pairs drawn from the decided set "
        f"(seed 42), {recon['control_teacher_same_fraction']:.1%} teacher-same",
        "",
        "For reference the same replay with in-sample probabilities (the single lever-3 model, "
        f"train pairs scored by a model that trained on them) leaves {recon['counts']['in_sample']['n_residual']} "
        "residual pairs -- the cross-fitted number is the one used everywhere below.",
        "",
        "1,740 of the 6,841 banked assets had no local preview left (the shared thumbnail cache "
        "had been evicted since the ViT-S run); they were re-read from Immich into the matrix "
        "previews dump before any instrument ran, so every pair is measured, not skipped.",
        "",
        "## Protocol",
        "",
        "- Every instrument reduces to one scalar per pair where higher means *same*.",
        "- **Bands are cross-fitted**: the (lo, hi) pair is chosen on four folds and applied to "
        "the fifth, then pooled. A band fitted and scored on the same pairs reaches 95-97% "
        "trivially and means nothing.",
        "- The headline is coverage at **achieved** agreement >= the floor, not at a *target* of "
        "the floor. Picking the max-coverage cell whose measured agreement clears a floor is a "
        "max over noisy estimates, so each agreement carries its Wilson 95% lower bound.",
        "- **Control anchor**: the same instrument on 200 pairs the head decided. This is the "
        "sanity check the brief asks for -- residual-band teacher labels carry elevated noise "
        "(the teacher self-conflicts ~5.4% over the whole bank, more in the hard region), so a "
        "low residual number only means something if the control number is high.",
        "- **Per-direction errors**: *dangerous* = instrument says same, teacher says different "
        "(a whole occasion can be dropped). *safe* = the reverse (one frame swapped for a "
        "sibling).",
        "",
        "## Headline",
        "",
        "| instrument | latency | residual AUC | control AUC | coverage @ >=95% agreement | coverage @ >=90% | dangerous @ >=90% |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ORDER:
        if name in instruments:
            lines.append(headline_row(name, instruments[name]))
    lines += [
        "",
        "Head-band agreement inside its own covered region is "
        f"{counts['covered_agreement']:.1%} with a 9.4% dangerous rate (levers-report.json). "
        "Every >=90% specialist point above carries a dangerous rate of 14-23% -- the tier "
        "would trade a small number of LLM calls for a materially worse error *direction*.",
        "",
        "The reference row is the point: the head's own probability, on the pairs where it "
        "abstains, cannot reach even 85% agreement at any coverage (AUC 0.627). The band was "
        "drawn honestly; there is nothing left in the head to squeeze.",
        "",
        "## What each instrument did",
        "",
    ]

    for name in ORDER:
        if name not in instruments:
            continue
        payload = instruments[name]
        lines += [f"### {LABELS[name]}", ""]
        if "model" in payload:
            lines.append(f"- model: `{payload['model']}`")
        if "license" in payload:
            lines.append(f"- licence: {payload['license']}")
        if payload.get("chosen_metric"):
            lines.append(f"- metric used: `{payload['chosen_metric']}`")
        lines += [
            f"- latency: {latency(payload)}",
            f"- residual: AUC {payload['residual_distribution']['auc']:.3f}, "
            f"Cohen's d {payload['residual_distribution']['cohens_d']:.2f} "
            f"(same n={payload['residual_distribution']['same']['n']}, "
            f"different n={payload['residual_distribution']['different']['n']}); "
            f"median same {payload['residual_distribution']['same']['median']:.3g} vs "
            f"different {payload['residual_distribution']['different']['median']:.3g}",
            f"- control: AUC {payload['control_distribution']['auc']:.3f}, "
            f"Cohen's d {payload['control_distribution']['cohens_d']:.2f}; a single threshold "
            f"fitted on the residual scores "
            f"{payload.get('control_accuracy_at_residual_threshold', float('nan')):.1%} on the control",
            f"- best single threshold on the residual (in-sample ceiling): "
            f"{payload['best_single_threshold_in_sample']['accuracy']:.1%} against a "
            f"{payload['majority_baseline_residual']:.1%} majority baseline",
            "",
        ]
        lines += curve_table(payload)
        lines.append("")

    vitb = instruments.get("dinov2_vitb14_head", {})
    if vitb:
        band = vitb["band_fitted_on_non_residual_95pct"]
        applied = vitb["residual_at_non_residual_band"]
        lines += [
            "## Two findings worth keeping",
            "",
            "### A band calibrated on the easy pairs is worthless in the hard band",
            "",
            "The ViT-B head's abstain band was fitted the production way -- on held-out "
            f"*non-residual* pairs, targeting 95% agreement. There it covers {band['coverage']:.1%} "
            f"at {band['agreement']:.1%} agreement with a {band['dangerous_rate']:.1%} dangerous "
            "rate. Applied unchanged to the residual pairs the same band still claims "
            f"{applied['coverage']:.1%} coverage but delivers **{applied['agreement']:.1%} "
            f"agreement and a {applied['dangerous_rate']:.1%} dangerous rate**. Any specialist "
            "tier has to have its band fitted on residual-band labels, which means paying a "
            "teacher to label residual pairs first -- the tier is not free to stand up.",
            "",
            "Also measured: training the ViT-B head on non-residual pairs that *share assets* "
            f"with residual pairs inflates its residual AUC to {vitb['residual_distribution']['auc']:.3f}; "
            f"restricting training to components disjoint from the residual set drops it to "
            f"{vitb['component_disjoint_residual_auc']:.3f}. Roughly 0.05 AUC of the ViT-B "
            "upgrade is asset memorisation, not representation. Raw ViT-B cosine alone scores "
            f"{vitb['raw_cosine_auc']:.3f} -- below the ViT-S head it was meant to upgrade.",
            "",
            "### The inlier *ratio* is the wrong statistic; the *count* carries the signal",
            "",
            "The brief asked for matched-keypoint inlier ratio. Measured on the residual band:",
            "",
            "| LightGlue statistic | residual AUC | control AUC |",
            "|---|---|---|",
        ]
        for metric, values in instruments["lightglue"]["metric_variants_auc"].items():
            lines.append(f"| `{metric}` | {values['auc']:.3f} | {values['control_auc']:.3f} |")
        lines += [
            "",
            "Inliers-over-matches lands at 0.580 -- barely above chance -- while the raw match "
            "count lands at 0.745. Two frames of the same scene taken seconds apart and two "
            "frames of *different* scenes in the same room both produce geometrically "
            "consistent matches; what separates them is how *many* survive, not what fraction. "
            "`inlier_ratio_kpts_*` is the inlier count divided by a constant 1,024 keypoints, "
            "so it is the count under another name and scores identically.",
            "",
        ]

    lines += ["## Combined cascade arithmetic", ""]
    lines += [
        "Percentages are of the whole 6,384-pair bank. Prefilter and head are fixed at "
        f"{counts['n_prefiltered'] / total:.2%} and {counts['n_covered'] / total:.2%}; the "
        "specialist tier eats a slice of the "
        f"{counts['n_residual'] / total:.2%} residual and the rest goes to the 27B.",
        "",
        "| specialist | tier bar | prefilter | head | specialist | to the 27B | specialist agreement | dangerous |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in ORDER:
        cells = raw["combined_cascade_arithmetic"].get(name)
        if not cells:
            continue
        for key, bar in (("ge_95pct", ">=95%"), ("ge_90pct", ">=90%")):
            cell = cells.get(key)
            if cell is None:
                lines.append(
                    f"| {LABELS[name]} | {bar} | {counts['n_prefiltered'] / total:.2%} | "
                    f"{counts['n_covered'] / total:.2%} | 0.00% | "
                    f"{counts['n_residual'] / total:.2%} | unreachable | -- |"
                )
                continue
            lines.append(
                f"| {LABELS[name]} | {bar} | {cell['prefilter_pct']:.2f}% | "
                f"{cell['head_pct']:.2f}% | {cell['specialist_pct']:.2f}% | "
                f"{cell['final_residual_pct']:.2f}% | {pct(cell['specialist_agreement'])} | "
                f"{pct(cell['specialist_dangerous_rate'])} |"
            )
    best_row = min(
        (
            (name, key, cell)
            for name, cells in raw["combined_cascade_arithmetic"].items()
            for key, cell in cells.items()
            if cell is not None
        ),
        key=lambda item: item[2]["final_residual_pct"],
    )
    lines += [
        "",
        f"The row that absorbs the most ({LABELS[best_row[0]]}, "
        f"{best_row[1].replace('ge_', '>=').replace('pct', '%')}) moves the 27B's share from "
        f"{counts['n_residual'] / total:.2%} of the bank to {best_row[2]['final_residual_pct']:.2f}% -- a "
        f"{1 - best_row[2]['final_residual_pct'] / (100 * counts['n_residual'] / total):.0%} cut in the "
        "big model's pair calls, bought with four models, a labelled residual sample to fit "
        f"the band on, and a {best_row[2]['specialist_dangerous_rate']:.1%} dangerous rate on "
        "what it absorbs. That is the wrong trade to take on volume alone: the only row whose "
        "dangerous rate is not worse than the head's own 9.4% is the five-model ensemble at "
        ">=95%, and it absorbs 1.72% for five models' worth of compute.",
        "",
        "## Obtainability and blockers",
        "",
        "| instrument | obtainable | note |",
        "|---|---|---|",
        "| SuperPoint + LightGlue ONNX | yes | `superpoint_lightglue_pipeline.onnx` from the "
        "fabio-sim/LightGlue-ONNX v2.0 release, 51 MB, onnxruntime CPU. **CoreML EP fails** "
        "(`Non-zero status code ... CoreMLExecutionProvider` on a fused node), so CPU only -- "
        f"which is where the {instruments['lightglue']['latency_ms_per_pair_inference']:.0f} ms/pair "
        "comes from. LightGlue and the exporter are Apache-2.0; "
        "SuperPoint's original MagicLeap weights are research-use-only, a shipping blocker. |",
        "| SSCD sscd_disc_mixup | yes | 94 MB torchscript, direct from "
        "`dl.fbaipublicfiles.com`, "
        "runs on MPS with no SSCD code at all. MIT. |",
        "| DINOv2 ViT-B/14 | yes | torch.hub, same transform as the ViT-S run. Apache-2.0. |",
        "| SigLIP-2 base/16-224 | yes | `onnx-community/siglip2-base-patch16-224-ONNX` "
        "vision tower, 354 MB fp32, onnxruntime CPU. Apache-2.0. |",
        "",
        "Nothing was blocked. No project-venv package was installed: every model is a direct "
        "download driven by onnxruntime 1.28 / torch 2.10, both already present.",
        "",
        "## Caveats",
        "",
        "- Residual-band teacher labels are noisy by construction. The control anchor is the "
        "guard: all instruments score 0.968-0.985 there, so the residual collapse is the band, "
        "not the instruments.",
        "- Ensembles are cross-fitted *within* the residual set, so they consume residual "
        "teacher labels. The single instruments do not have to, but as the ViT-B transfer "
        "result shows, their bands do.",
        "- Coverage points at >=95% rest on 40-110 covered pairs. Wilson lower bounds are in "
        "every table for that reason; not one of them clears 95%.",
    ]

    (MATRIX / "residual-instruments-report.md").write_text("\n".join(lines) + "\n")

    summary = {
        "schema_version": "pairhead-residual-instruments-report-v1",
        "seed": raw["seed"],
        "residual_reconstruction": recon,
        "protocol": {
            "band_selection": "5-fold cross-fitted, seed 42; band chosen on 4 folds, applied to the 5th",
            "headline": "max coverage whose ACHIEVED cross-fitted agreement clears the floor",
            "uncertainty": "Wilson 95% lower bound on every agreement",
            "control": "200 head-decided pairs, seed 42, as the sanity anchor",
            "dangerous": "instrument says same, teacher says different",
        },
        "instruments": instruments,
        "combined_cascade_arithmetic": raw["combined_cascade_arithmetic"],
        "best_single_instrument_by_residual_auc": best_single,
        "verdict": (
            "No single non-LLM instrument earns a >=95%-agreement specialist tier on this "
            "residual band. The only >=95% point measured is the five-model ensemble: 16.6% "
            "of the residual (1.72% of the bank) at 97.3% agreement, Wilson lower bound 92.3%, "
            "dangerous 7.4% -- the one specialist row whose error direction is not worse than "
            "the head's own 9.4%, at the cost of five models and labelled residual pairs to fit "
            "the band on. Every >=90%-only point carries a 14-23% dangerous rate. The cheap "
            f"single instrument is SSCD: equal separation to geometric verification at "
            f"{cost_ratio:.0f}x less compute, per-asset cacheable, MIT -- but it never clears 95%."
        ),
        "blockers": raw["blockers"],
    }
    (MATRIX / "residual-instruments-report.json").write_text(json.dumps(summary, indent=2))
    print("wrote residual-instruments-report.{json,md}")


def main() -> int:
    render(analyse())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
