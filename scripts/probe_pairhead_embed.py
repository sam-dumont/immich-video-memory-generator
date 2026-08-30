#!/usr/bin/env python3
"""Embed every asset in the pairwise-head dataset with DINOv2 ViT-S/14.

Phase A / step 1 of the pairwise-head prototype: turns each asset referenced in
``pairs.jsonl`` into a single 384-d, L2-normalized global embedding. Feature
engineering, the leakage-free split, and classifier training happen in
``probe_pairhead_cascade.py`` (reads ``embeddings.npy`` + ``ids.json`` back in).

Images and ids are read from and written to ``~/.immich-memories-matrix`` only.
The shared thumbnail cache (``~/.immich-memories/cache/thumbnails``) is
read-only: assets missing from the flat ``previews/`` dump are read from there
but nothing is ever written back to it.

torchvision is not installed in this environment, so the standard DINOv2 eval
transform (resize short side to 256, center-crop 224, ImageNet normalize) is
reimplemented directly on PIL + numpy instead of importing it.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
RESIZE_SHORT_SIDE = 256
CROP_SIZE = 224
EMBED_DIM = 384

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
DEFAULT_CACHE_DIR = Path.home() / ".immich-memories" / "cache" / "thumbnails"


@dataclass(frozen=True)
class EmbedRun:
    asset_count: int
    device: str
    model_source: str
    fallback_reason: str | None
    elapsed_seconds: float
    ms_per_image: float
    batch_size: int


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
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Embed only the first N asset ids (debug/timing runs only).",
    )
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    if not _within_matrix(args.cache_dir) and args.cache_dir != DEFAULT_CACHE_DIR:
        parser.error("--cache-dir must be the shared thumbnail cache or inside ~/.immich-memories-matrix")
    return args


def load_asset_ids(matrix_dir: Path) -> list[str]:
    """Union of asset ids referenced by any pair, sorted for a deterministic row order."""
    ids: set[str] = set()
    with (matrix_dir / "pairs.jsonl").open() as fh:
        for line in fh:
            record = json.loads(line)
            ids.add(record["a"])
            ids.add(record["b"])
    return sorted(ids)


def resolve_image_path(asset_id: str, previews_dir: Path, cache_dir: Path) -> Path:
    """Flat matrix preview first, then the shared (read-only) thumbnail cache."""
    preview = previews_dir / f"{asset_id}.jpg"
    if preview.exists():
        return preview
    cached = cache_dir / asset_id[:2] / f"{asset_id}_preview.jpg"
    if cached.exists():
        return cached
    raise FileNotFoundError(f"no preview image for asset {asset_id}")


def load_and_transform(path: Path) -> torch.Tensor:
    """Resize-256 / center-crop-224 / ImageNet-normalize, CHW float32."""
    with Image.open(path) as handle:
        image = handle.convert("RGB")
    width, height = image.size
    if width <= height:
        new_width, new_height = RESIZE_SHORT_SIDE, round(height * RESIZE_SHORT_SIDE / width)
    else:
        new_width, new_height = round(width * RESIZE_SHORT_SIDE / height), RESIZE_SHORT_SIDE
    image = image.resize((new_width, new_height), Image.Resampling.BICUBIC)
    left = (new_width - CROP_SIZE) // 2
    top = (new_height - CROP_SIZE) // 2
    image = image.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - IMAGENET_MEAN) / IMAGENET_STD
    return torch.from_numpy(array.transpose(2, 0, 1)).float()


def load_model() -> tuple[torch.nn.Module, str, str | None]:
    """DINOv2 via torch.hub; falls back to the HF timm equivalent if that fails."""
    try:
        model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
        return model.eval(), "torch.hub:facebookresearch/dinov2:dinov2_vits14", None
    except Exception as hub_error:  # noqa: BLE001 - genuinely any failure should trigger the fallback
        try:
            import timm
        except ImportError as import_error:
            raise RuntimeError(
                f"DINOv2 hub load failed ({hub_error!r}) and timm is not installed for the fallback"
            ) from import_error
        model = timm.create_model("vit_small_patch14_dinov2", pretrained=True, num_classes=0)
        reason = f"torch.hub load failed ({hub_error!r}); used timm vit_small_patch14_dinov2 instead"
        return model.eval(), "timm:vit_small_patch14_dinov2", reason


def _embed_on_device(
    model: torch.nn.Module,
    device: str,
    asset_ids: list[str],
    previews_dir: Path,
    cache_dir: Path,
    batch_size: int,
) -> tuple[np.ndarray, float]:
    model = model.to(device)
    embeddings = np.empty((len(asset_ids), EMBED_DIM), dtype=np.float32)
    started = time.monotonic()
    with torch.no_grad():
        for start in range(0, len(asset_ids), batch_size):
            chunk = asset_ids[start : start + batch_size]
            tensors = [
                load_and_transform(resolve_image_path(asset_id, previews_dir, cache_dir))
                for asset_id in chunk
            ]
            batch = torch.stack(tensors).to(device)
            output = model(batch)
            output = output / output.norm(dim=1, keepdim=True)
            embeddings[start : start + len(chunk)] = output.to("cpu").numpy()
    elapsed = time.monotonic() - started
    return embeddings, elapsed


def embed_all(
    asset_ids: list[str],
    previews_dir: Path,
    cache_dir: Path,
    batch_size: int,
) -> tuple[np.ndarray, EmbedRun]:
    """Embed every asset id; L2-normalized rows, aligned to ``asset_ids`` order."""
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, model_source, fallback_reason = load_model()
    try:
        embeddings, elapsed = _embed_on_device(
            model, device, asset_ids, previews_dir, cache_dir, batch_size
        )
    except RuntimeError as mps_error:
        if device != "mps":
            raise
        device = "cpu"
        fallback_reason = (
            f"{fallback_reason}; " if fallback_reason else ""
        ) + f"MPS forward pass failed ({mps_error!r}); fell back to CPU"
        print(fallback_reason, flush=True)
        embeddings, elapsed = _embed_on_device(
            model, device, asset_ids, previews_dir, cache_dir, batch_size
        )
    run = EmbedRun(
        asset_count=len(asset_ids),
        device=device,
        model_source=model_source,
        fallback_reason=fallback_reason,
        elapsed_seconds=elapsed,
        ms_per_image=1000.0 * elapsed / len(asset_ids),
        batch_size=batch_size,
    )
    return embeddings, run


def main() -> int:
    args = _arguments()
    asset_ids = load_asset_ids(args.matrix_dir)
    if args.limit is not None:
        asset_ids = asset_ids[: args.limit]
    print(f"embedding {len(asset_ids)} assets", flush=True)

    embeddings, run = embed_all(asset_ids, args.matrix_dir / "previews", args.cache_dir, args.batch_size)

    np.save(args.matrix_dir / "embeddings.npy", embeddings)
    (args.matrix_dir / "ids.json").write_text(json.dumps(asset_ids))
    (args.matrix_dir / "embed_meta.json").write_text(json.dumps(asdict(run), indent=2))

    print(
        f"device={run.device} model={run.model_source} "
        f"{run.ms_per_image:.2f} ms/image, {run.elapsed_seconds:.1f}s total",
        flush=True,
    )
    if run.fallback_reason:
        print(f"fallback: {run.fallback_reason}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
