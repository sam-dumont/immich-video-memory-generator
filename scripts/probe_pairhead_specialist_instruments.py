#!/usr/bin/env python3
"""Specialist (non-LLM) instruments on the pairwise cascade's residual band.

Question: the cascade in probe_pairhead_levers.py abstains on ~10% of pairs at
its loose 95% operating point. Can a specialised non-LLM instrument decide any
of them, or does that band have to go to the big model?

Four instruments are measured, each reducing a pair to one scalar where higher
means "same picture":

  lightglue -- SuperPoint + LightGlue geometric verification, run from the
               fabio-sim/LightGlue-ONNX v2.0 combined pipeline under
               onnxruntime. Reports raw match count, RANSAC (MAGSAC) inlier
               counts under both a fundamental matrix and a homography, and
               the inlier ratios over both denominators.
  sscd      -- Meta's SSCD copy-detection embedding (sscd_disc_mixup
               torchscript, MIT), cosine similarity. Both preprocessings the
               SSCD README recommends are measured.
  vitb      -- DINOv2 ViT-B/14 over the whole bank, same transform as the
               ViT-S run, so probe_pairhead_specialist_report.py can fit a
               logistic head on it.
  siglip2   -- SigLIP-2 base/16-224 vision tower (ONNX), cosine similarity.

Stages write id-bearing intermediates into the matrix dir; only
probe_pairhead_specialist_report.py's markdown is guaranteed id-free.

Reads images from the flat previews dump, falling back to the shared thumbnail
cache read-only. ``topup`` re-reads anything missing from Immich, because that
cache is evicted over time and skipping the assets it has lost would silently
shrink the residual set instead of measuring it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_pairhead_cascade as cascade  # noqa: E402
import probe_pairhead_levers as levers  # noqa: E402

MATRIX_DIR = levers.MATRIX_DIR
PREVIEWS_DIR = MATRIX_DIR / "previews"
THUMBNAIL_CACHE = Path.home() / ".immich-memories" / "cache" / "thumbnails"

SEED = 42
CONTROL_SAMPLE = 200
FOLDS = 5

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

LIGHTGLUE_MODEL = "superpoint_lightglue_pipeline.onnx"
LIGHTGLUE_SIDE = 512
LIGHTGLUE_KEYPOINTS = 1024
RANSAC_PIXELS = 3.0
MIN_MATCHES_FOR_RANSAC = 8

SSCD_MODEL = "sscd_disc_mixup.torchscript.pt"
SSCD_SKEW = 320
SSCD_SMALL_EDGE = 288

SIGLIP_MODEL = "siglip2_vision.onnx"
SIGLIP_SIDE = 224

VITB_DIM = 768
BATCH = 32


# --- image access -------------------------------------------------------------


def resolve_image_path(asset_id: str) -> Path:
    preview = PREVIEWS_DIR / f"{asset_id}.jpg"
    if preview.exists():
        return preview
    cached = THUMBNAIL_CACHE / asset_id[:2] / f"{asset_id}_preview.jpg"
    if cached.exists():
        return cached
    raise FileNotFoundError(f"no preview image for asset {asset_id}")


def load_rgb(asset_id: str):
    from PIL import Image

    with Image.open(resolve_image_path(asset_id)) as handle:
        return handle.convert("RGB")


def letterbox_gray(image, side: int) -> np.ndarray:
    """Aspect-preserving resize onto a square canvas, luminance, float32 in [0,1].

    A plain square resize would distort a portrait and a landscape frame of the
    same scene differently -- exactly the deformation geometric verification is
    supposed to survive -- so pad rather than stretch.
    """
    from PIL import Image

    width, height = image.size
    scale = side / max(width, height)
    new_width, new_height = max(1, round(width * scale)), max(1, round(height * scale))
    resized = image.resize((new_width, new_height), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    gray = array @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    canvas = np.zeros((side, side), dtype=np.float32)
    top, left = (side - new_height) // 2, (side - new_width) // 2
    canvas[top : top + new_height, left : left + new_width] = gray
    return canvas


def to_chw_imagenet(image_array: np.ndarray) -> np.ndarray:
    array = image_array.astype(np.float32) / 255.0
    return ((array - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)


# --- stage: preview top-up ----------------------------------------------------


async def _fetch_previews(missing: list[str], concurrency: int = 8) -> tuple[int, int]:
    import httpx
    import yaml

    config = yaml.safe_load((Path.home() / ".immich-memories" / "config.yaml").read_text())
    immich = config["immich"]
    semaphore = asyncio.Semaphore(concurrency)
    ok = failed = 0

    async def one(client, asset_id: str) -> bool:
        async with semaphore:
            try:
                response = await client.get(
                    f"/api/assets/{asset_id}/thumbnail", params={"size": "preview"}
                )
            except httpx.HTTPError:
                return False
            if response.status_code != 200:
                return False
            (PREVIEWS_DIR / f"{asset_id}.jpg").write_bytes(response.content)
            return True

    async with httpx.AsyncClient(
        base_url=immich["url"],
        headers={"x-api-key": immich["api_key"], "Accept": "application/octet-stream"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        for coro in asyncio.as_completed([one(client, i) for i in missing]):
            success = await coro
            ok += success
            failed += not success
    return ok, failed


def stage_topup() -> None:
    ids: list[str] = json.loads((MATRIX_DIR / "ids.json").read_text())
    missing = []
    for asset_id in ids:
        try:
            resolve_image_path(asset_id)
        except FileNotFoundError:
            missing.append(asset_id)
    print(f"{len(missing)} of {len(ids)} bank assets need a preview")
    if not missing:
        return
    started = time.perf_counter()
    ok, failed = asyncio.run(_fetch_previews(missing))
    elapsed = time.perf_counter() - started
    (MATRIX_DIR / "preview-topup.json").write_text(
        json.dumps(
            {
                "requested": len(missing),
                "fetched": ok,
                "failed": failed,
                "elapsed_seconds": elapsed,
                "source": "immich GET /api/assets/{id}/thumbnail?size=preview",
            }
        )
    )
    print(f"fetched {ok}, failed {failed}, {elapsed:.0f}s")


# --- stage: residual-band reconstruction --------------------------------------


def _crossfit_head_probabilities(
    rng: np.random.Generator,
    features: np.ndarray,
    labels: np.ndarray,
    pair_component: np.ndarray,
    best_c: float,
) -> np.ndarray:
    """Out-of-fold head probabilities, folds grouped by connected component.

    The single lever-3 model scores its own training pairs, so replaying it over
    the whole bank would understate the abstention band by exactly the pairs it
    memorised. Grouping folds by component keeps an asset's pairs together.

    Takes the caller's generator rather than seeding its own: the control sample
    is drawn from the same stream afterwards, so the draw order is part of what
    seed 42 pins down.
    """
    components = np.unique(pair_component)
    rng.shuffle(components)
    fold_of_component = {c: i % FOLDS for i, c in enumerate(components.tolist())}
    fold = np.array([fold_of_component[c] for c in pair_component])

    probabilities = np.zeros(len(labels), dtype=np.float64)
    for k in range(FOLDS):
        held = fold == k
        inner_cal = (~held) & (fold == (k + 1) % FOLDS)
        inner_train = (~held) & ~inner_cal
        model = cascade.train_and_calibrate(
            features[inner_train], labels[inner_train],
            features[inner_cal], labels[inner_cal], best_c,
        )
        probabilities[held] = model.predict_proba(features[held])[:, 1]
    return probabilities


def stage_residual() -> None:
    started = time.perf_counter()
    rng = np.random.default_rng(SEED)

    bank = levers.load_bank()
    pairs, labels, split_arr = bank["pairs"], bank["labels"], bank["split_arr"]
    extra = levers.build_extra_features(pairs, levers.load_timestamps(), levers.load_hashes())
    features = np.concatenate([bank["features"], extra], axis=1)

    masks = {name: split_arr == name for name in cascade.SPLIT_TARGETS}
    best_c, _ = cascade.choose_c(features[masks["train"]], labels[masks["train"]])
    in_sample_model = cascade.train_and_calibrate(
        features[masks["train"]], labels[masks["train"]],
        features[masks["cal"]], labels[masks["cal"]], best_c,
    )
    p_in_sample = in_sample_model.predict_proba(features)[:, 1]

    component_of = cascade.connected_components(pairs, bank["id_to_row"])
    pair_component = np.array([component_of[bank["id_to_row"][p.a]] for p in pairs])
    p_oof = _crossfit_head_probabilities(rng, features, labels, pair_component, best_c)

    distance = levers.raw_cosine_distance(pairs, bank["id_to_row"], bank["embeddings"])
    tau_far = levers.find_tau_far(distance, labels)
    loose = json.loads((MATRIX_DIR / "levers-report.json").read_text())
    cell = loose["lever3"]["asymmetric_band"]["headline_by_agreement_target"]["95pct"]["cell"]
    t_same, t_diff = cell["t_same"], cell["t_diff"]

    counts = {}
    for name, probabilities in (("in_sample", p_in_sample), ("oof", p_oof)):
        prefiltered = distance > tau_far
        covered = (~prefiltered) & (
            (probabilities >= t_same) | (probabilities <= t_diff)
        )
        residual = (~prefiltered) & ~covered
        counts[name] = {
            "n_prefiltered": int(prefiltered.sum()),
            "n_covered": int(covered.sum()),
            "n_residual": int(residual.sum()),
            "residual_frac": float(residual.mean()),
            "residual_same_frac": float(labels[residual].mean()),
            "covered_agreement": float(
                np.mean((probabilities[covered] >= t_same) == labels[covered])
            ),
        }

    prefiltered = distance > tau_far
    covered = (~prefiltered) & ((p_oof >= t_same) | (p_oof <= t_diff))
    residual = (~prefiltered) & ~covered
    covered_index = np.flatnonzero(covered)
    control_index = np.sort(
        rng.choice(covered_index, size=min(CONTROL_SAMPLE, len(covered_index)), replace=False)
    )

    def rows(index: np.ndarray, group: str) -> list[dict]:
        return [
            {
                "i": int(j),
                "a": pairs[j].a,
                "b": pairs[j].b,
                "teacher_same": bool(labels[j]),
                "p_head": float(p_oof[j]),
                "p_head_in_sample": float(p_in_sample[j]),
                "cos_distance": float(distance[j]),
                "split": str(split_arr[j]),
                "group": group,
                "head_call": None if group == "residual" else bool(p_oof[j] >= t_same),
            }
            for j in index.tolist()
        ]

    (MATRIX_DIR / "residual-set.json").write_text(
        json.dumps(
            {
                "schema_version": "pairhead-residual-v1",
                "seed": SEED,
                "tau_far": float(tau_far),
                "t_same": t_same,
                "t_diff": t_diff,
                "chosen_c": best_c,
                "counts": counts,
                "primary_probability_source": "oof",
                "residual": rows(np.flatnonzero(residual), "residual"),
                "control": rows(control_index, "control"),
                "wall_time_seconds": time.perf_counter() - started,
            }
        )
    )
    print(f"residual {counts['oof']['n_residual']} (oof) / {counts['in_sample']['n_residual']} "
          f"(in-sample), control {len(control_index)}, {time.perf_counter() - started:.0f}s")


def load_residual_set() -> dict:
    return json.loads((MATRIX_DIR / "residual-set.json").read_text())


def measured_rows() -> list[dict]:
    data = load_residual_set()
    return data["residual"] + data["control"]


# --- stage: LightGlue ---------------------------------------------------------


def _geometric_metrics(kpts_a: np.ndarray, kpts_b: np.ndarray) -> dict:
    import cv2

    out = {"n_inliers_f": 0, "n_inliers_h": 0}
    if len(kpts_a) < MIN_MATCHES_FOR_RANSAC:
        return out
    src = kpts_a.astype(np.float32).reshape(-1, 1, 2)
    dst = kpts_b.astype(np.float32).reshape(-1, 1, 2)
    try:
        _, mask = cv2.findFundamentalMat(src, dst, cv2.USAC_MAGSAC, RANSAC_PIXELS, 0.999, 10000)
        if mask is not None:
            out["n_inliers_f"] = int(mask.sum())
    except cv2.error:
        pass
    try:
        _, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, RANSAC_PIXELS, maxIters=10000)
        if mask is not None:
            out["n_inliers_h"] = int(mask.sum())
    except cv2.error:
        pass
    return out


def stage_lightglue(models_dir: Path) -> None:
    import onnxruntime as ort

    started = time.perf_counter()
    rows = measured_rows()
    # CoreML EP fails on this graph (fused node returns a non-zero status), so CPU only.
    session = ort.InferenceSession(
        str(models_dir / LIGHTGLUE_MODEL), ort.SessionOptions(), providers=["CPUExecutionProvider"]
    )

    cache: dict[str, np.ndarray] = {}

    def gray(asset_id: str) -> np.ndarray:
        if asset_id not in cache:
            cache[asset_id] = letterbox_gray(load_rgb(asset_id), LIGHTGLUE_SIDE)
        return cache[asset_id]

    results = []
    infer_seconds = 0.0
    for index, row in enumerate(rows):
        images = np.stack([gray(row["a"]), gray(row["b"])])[:, None].astype(np.float32)
        t0 = time.perf_counter()
        keypoints, matches, mscores = session.run(None, {"images": images})
        infer_seconds += time.perf_counter() - t0
        metrics = _geometric_metrics(keypoints[0][matches[:, 1]], keypoints[1][matches[:, 2]])
        n_matches = int(len(matches))
        results.append(
            {
                "i": row["i"],
                "group": row["group"],
                "teacher_same": row["teacher_same"],
                "n_matches": n_matches,
                "n_strong_matches": int((mscores >= 0.5).sum()),
                "mscore_mean": float(mscores.mean()) if n_matches else 0.0,
                "mscore_sum": float(mscores.sum()) if n_matches else 0.0,
                **metrics,
                "inlier_ratio_kpts_f": metrics["n_inliers_f"] / LIGHTGLUE_KEYPOINTS,
                "inlier_ratio_kpts_h": metrics["n_inliers_h"] / LIGHTGLUE_KEYPOINTS,
                "inlier_ratio_matches_f": (
                    metrics["n_inliers_f"] / n_matches if n_matches else 0.0
                ),
                "inlier_ratio_matches_h": (
                    metrics["n_inliers_h"] / n_matches if n_matches else 0.0
                ),
                "match_ratio": n_matches / LIGHTGLUE_KEYPOINTS,
            }
        )
        if (index + 1) % 100 == 0:
            print(f"  {index + 1}/{len(rows)}  {time.perf_counter() - started:.0f}s", flush=True)

    (MATRIX_DIR / "residual-lightglue.json").write_text(
        json.dumps(
            {
                "schema_version": "pairhead-lightglue-v1",
                "model": f"fabio-sim/LightGlue-ONNX v2.0 {LIGHTGLUE_MODEL}",
                "provider": "CPUExecutionProvider",
                "image_side": LIGHTGLUE_SIDE,
                "max_keypoints": LIGHTGLUE_KEYPOINTS,
                "ransac": {"method": "USAC_MAGSAC", "px": RANSAC_PIXELS},
                "n_pairs": len(rows),
                "inference_ms_per_pair": infer_seconds / len(rows) * 1000,
                "total_ms_per_pair": (time.perf_counter() - started) / len(rows) * 1000,
                "results": results,
            }
        )
    )
    print(f"lightglue: {infer_seconds / len(rows) * 1000:.0f} ms/pair over {len(rows)} pairs")


# --- stage: SSCD --------------------------------------------------------------


def _sscd_skew(asset_id: str) -> np.ndarray:
    from PIL import Image

    image = load_rgb(asset_id).resize((SSCD_SKEW, SSCD_SKEW), Image.Resampling.BILINEAR)
    return to_chw_imagenet(np.asarray(image))


def _sscd_small(asset_id: str) -> np.ndarray:
    from PIL import Image

    image = load_rgb(asset_id)
    width, height = image.size
    if width <= height:
        size = (SSCD_SMALL_EDGE, round(height * SSCD_SMALL_EDGE / width))
    else:
        size = (round(width * SSCD_SMALL_EDGE / height), SSCD_SMALL_EDGE)
    return to_chw_imagenet(np.asarray(image.resize(size, Image.Resampling.BILINEAR)))


def stage_sscd(models_dir: Path) -> None:
    import torch

    started = time.perf_counter()
    rows = measured_rows()
    ids = sorted({row[key] for row in rows for key in ("a", "b")})
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = torch.jit.load(str(models_dir / SSCD_MODEL)).to(device).eval()

    def normalise(tensor) -> np.ndarray:
        return torch.nn.functional.normalize(tensor, dim=1).cpu().numpy().astype(np.float32)

    def sync() -> None:
        # MPS dispatch is async: without this the timer measures enqueue, not work.
        if device == "mps":
            torch.mps.synchronize()

    skew: dict[str, np.ndarray] = {}
    skew_seconds = 0.0
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        batch = torch.from_numpy(np.stack([_sscd_skew(i) for i in chunk])).to(device)
        sync()
        t0 = time.perf_counter()
        with torch.no_grad():
            vectors = model(batch)
        sync()
        skew_seconds += time.perf_counter() - t0
        skew.update(zip(chunk, normalise(vectors), strict=True))

    # The SSCD README's other recommendation preserves aspect, so it cannot batch.
    small: dict[str, np.ndarray] = {}
    small_seconds = 0.0
    for asset_id in ids:
        batch = torch.from_numpy(_sscd_small(asset_id)[None]).to(device)
        sync()
        t0 = time.perf_counter()
        with torch.no_grad():
            vector = model(batch)
        sync()
        small_seconds += time.perf_counter() - t0
        small[asset_id] = normalise(vector)[0]

    (MATRIX_DIR / "residual-sscd.json").write_text(
        json.dumps(
            {
                "schema_version": "pairhead-sscd-v1",
                "model": f"facebookresearch/sscd-copy-detection {SSCD_MODEL}",
                "license": "MIT (SSCD codebase, Meta Platforms)",
                "device": device,
                "embedding_dim": int(next(iter(skew.values())).shape[0]),
                "n_pairs": len(rows),
                "n_assets": len(ids),
                "skew320_ms_per_image": skew_seconds / len(ids) * 1000,
                "small288_ms_per_image": small_seconds / len(ids) * 1000,
                "results": [
                    {
                        "i": row["i"],
                        "group": row["group"],
                        "teacher_same": row["teacher_same"],
                        "sscd_cos_skew320": float(skew[row["a"]] @ skew[row["b"]]),
                        "sscd_cos_small288": float(small[row["a"]] @ small[row["b"]]),
                    }
                    for row in rows
                ],
            }
        )
    )
    print(f"sscd: {skew_seconds / len(ids) * 1000:.1f} ms/img skew320, "
          f"{time.perf_counter() - started:.0f}s")


# --- stage: DINOv2 ViT-B/14 ---------------------------------------------------


def stage_vitb() -> None:
    import torch

    import probe_pairhead_embed as embed

    ids: list[str] = json.loads((MATRIX_DIR / "ids.json").read_text())
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vitb14").eval().to(device)

    out = np.empty((len(ids), VITB_DIM), dtype=np.float32)
    started = time.perf_counter()
    with torch.no_grad():
        for start in range(0, len(ids), BATCH):
            chunk = ids[start : start + BATCH]
            tensors = [
                embed.load_and_transform(resolve_image_path(asset_id)) for asset_id in chunk
            ]
            vectors = model(torch.stack(tensors).to(device))
            vectors = vectors / vectors.norm(dim=1, keepdim=True)
            out[start : start + len(chunk)] = vectors.to("cpu").numpy()
    elapsed = time.perf_counter() - started

    np.save(MATRIX_DIR / "embeddings-vitb14.npy", out)
    (MATRIX_DIR / "embed_meta_vitb14.json").write_text(
        json.dumps(
            {
                "asset_count": len(ids),
                "device": device,
                "model_source": "torch.hub:facebookresearch/dinov2:dinov2_vitb14",
                "elapsed_seconds": elapsed,
                "ms_per_image": 1000.0 * elapsed / len(ids),
                "batch_size": BATCH,
                "embed_dim": VITB_DIM,
            }
        )
    )
    print(f"vitb: {len(ids)} assets, {1000 * elapsed / len(ids):.1f} ms/img")


# --- stage: SigLIP-2 ----------------------------------------------------------


def stage_siglip(models_dir: Path) -> None:
    import onnxruntime as ort
    from PIL import Image

    started = time.perf_counter()
    rows = measured_rows()
    ids = sorted({row[key] for row in rows for key in ("a", "b")})
    session = ort.InferenceSession(
        str(models_dir / SIGLIP_MODEL), ort.SessionOptions(), providers=["CPUExecutionProvider"]
    )

    def preprocess(asset_id: str) -> np.ndarray:
        image = load_rgb(asset_id).resize((SIGLIP_SIDE, SIGLIP_SIDE), Image.Resampling.BILINEAR)
        array = np.asarray(image, dtype=np.float32) / 255.0
        return ((array - 0.5) / 0.5).transpose(2, 0, 1)

    vectors: dict[str, np.ndarray] = {}
    infer_seconds = 0.0
    for start in range(0, len(ids), 16):
        chunk = ids[start : start + 16]
        batch = np.stack([preprocess(i) for i in chunk]).astype(np.float32)
        t0 = time.perf_counter()
        _, pooled = session.run(None, {"pixel_values": batch})
        infer_seconds += time.perf_counter() - t0
        pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        vectors.update(zip(chunk, pooled.astype(np.float32), strict=True))

    (MATRIX_DIR / "residual-siglip2.json").write_text(
        json.dumps(
            {
                "schema_version": "pairhead-siglip2-v1",
                "model": f"onnx-community/siglip2-base-patch16-224-ONNX {SIGLIP_MODEL} (fp32)",
                "license": "Apache-2.0 (SigLIP-2)",
                "provider": "CPUExecutionProvider",
                "n_assets": len(ids),
                "ms_per_image": infer_seconds / len(ids) * 1000,
                "results": [
                    {
                        "i": row["i"],
                        "group": row["group"],
                        "teacher_same": row["teacher_same"],
                        "siglip2_cos": float(vectors[row["a"]] @ vectors[row["b"]]),
                    }
                    for row in rows
                ],
            }
        )
    )
    print(f"siglip2: {infer_seconds / len(ids) * 1000:.1f} ms/img, "
          f"{time.perf_counter() - started:.0f}s")


STAGES = {
    "topup": lambda models: stage_topup(),
    "residual": lambda models: stage_residual(),
    "lightglue": stage_lightglue,
    "sscd": stage_sscd,
    "vitb": lambda models: stage_vitb(),
    "siglip": stage_siglip,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stages", nargs="+", choices=[*STAGES, "all"])
    parser.add_argument(
        "--models-dir",
        type=Path,
        required=True,
        help="Directory holding the downloaded ONNX/torchscript weights.",
    )
    args = parser.parse_args()
    names = list(STAGES) if "all" in args.stages else args.stages
    for name in names:
        print(f"== {name}", flush=True)
        STAGES[name](args.models_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
