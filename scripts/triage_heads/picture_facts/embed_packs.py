#!/usr/bin/env python3
"""Pooled DINOv2 packs for a folder of JPEGs, byte-compatible with the shipped triage cache.

Preprocessing, pooling and the encoder key are copied from the recovered
`scripts/triage_heads/embed.py` (git 956a67cc) so a pack computed here is interchangeable
with the ones already banked in `~/.immich-memories-distill/oi-v2/embeddings.db` and with
what `immich_memories.triage.encoder` produces at serving time.

    python embed_packs.py --images <dir> --out packs.npz [--provider cpu|coreml|auto]

Writes `ids` (str array) and `packs` ([n, 2304] float32), plus a timing record.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

PREPROCESS_VERSION = "dinov2-s14-224-crop-v1"
LAYOUT_VERSION = "cls+mean+quad2x2/pca256/fp16"
MODEL_ID = "facebook/dinov2-small"
MODEL_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
TOKEN_DIM = 384
PATCH_GRID = 16
PACK_DIM = 6 * TOKEN_DIM
RESIZE_SHORT_SIDE = 256
CROP_SIZE = 224
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess_image_bytes(payload: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(payload)) as handle:
        image = handle.convert("RGB")
    width, height = image.size
    if width <= height:
        resized = (RESIZE_SHORT_SIDE, round(height * RESIZE_SHORT_SIDE / width))
    else:
        resized = (round(width * RESIZE_SHORT_SIDE / height), RESIZE_SHORT_SIDE)
    image = image.resize(resized, Image.Resampling.BICUBIC)
    left = (resized[0] - CROP_SIZE) // 2
    top = (resized[1] - CROP_SIZE) // 2
    image = image.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    normalized = (pixels - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)


def pool_token_pack(tokens: np.ndarray) -> np.ndarray:
    array = np.asarray(tokens, dtype=np.float32)
    cls = array[:, 0, :]
    patches = array[:, 1:, :].reshape(-1, PATCH_GRID, PATCH_GRID, TOKEN_DIM)
    pooled = [cls, patches.mean(axis=(1, 2))]
    half = PATCH_GRID // 2
    pooled.extend(
        quadrant.mean(axis=(1, 2))
        for quadrant in (
            patches[:, :half, :half, :],
            patches[:, :half, half:, :],
            patches[:, half:, :half, :],
            patches[:, half:, half:, :],
        )
    )
    return np.concatenate(pooled, axis=1).astype(np.float32, copy=False)


def encoder_key(onnx_path: Path) -> str:
    with onnx_path.open("rb") as handle:
        weights = hashlib.file_digest(handle, "sha256").hexdigest()
    digest = hashlib.sha256()
    for field in (
        f"{MODEL_ID}@{MODEL_REVISION}",
        weights,
        PREPROCESS_VERSION,
        LAYOUT_VERSION,
    ):
        digest.update(field.encode())
    return digest.hexdigest()


def create_session(model_path: Path, provider: str):
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    if provider == "cpu":
        providers = ["CPUExecutionProvider"]
    elif provider == "coreml":
        providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    else:
        providers = (
            ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            if "CoreMLExecutionProvider" in available
            else ["CPUExecutionProvider"]
        )
    options = ort.SessionOptions()
    # TRIAGE_THREADS stands in for a smaller machine: a 4-core NAS is the CPU tier's gate.
    options.intra_op_num_threads = int(
        os.environ.get("TRIAGE_THREADS") or max(1, min(8, (os.cpu_count() or 2) - 1))
    )
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=options, providers=providers)


def token_output(outputs) -> np.ndarray:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[1:] == (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM):
            return array.astype(np.float32, copy=False)
    raise ValueError("ONNX export did not return [batch, 257, 384] tokens")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--images", nargs="+", type=Path, required=True)
    parser.add_argument("--suffix", default=".jpg")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--onnx", type=Path, default=Path("~/.immich-memories/models/triage/dinov2-small.onnx")
    )
    parser.add_argument("--provider", default="cpu")
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--ids", type=Path, default=None, help="newline-separated ids to keep")
    args = parser.parse_args(argv)

    onnx = args.onnx.expanduser()
    keep: set[str] | None = None
    if args.ids:
        keep = {line.strip() for line in args.ids.read_text().splitlines() if line.strip()}

    files: dict[str, Path] = {}
    for directory in args.images:
        for path in sorted(directory.rglob(f"*{args.suffix}")):
            image_id = path.name[: -len(args.suffix)]
            if keep is not None and image_id not in keep:
                continue
            files.setdefault(image_id, path)
    ids = sorted(files)
    print(f"{len(ids)} images, provider={args.provider}", flush=True)

    session = create_session(onnx, args.provider)
    input_name = session.get_inputs()[0].name
    packs = np.zeros((len(ids), PACK_DIM), dtype=np.float32)
    decode_seconds = 0.0
    forward_seconds = 0.0
    started = time.monotonic()
    row = 0
    for start in range(0, len(ids), args.batch):
        chunk = ids[start : start + args.batch]
        t0 = time.monotonic()
        batch = np.stack([preprocess_image_bytes(files[i].read_bytes()) for i in chunk])
        t1 = time.monotonic()
        tokens = token_output(session.run(None, {input_name: batch}))
        t2 = time.monotonic()
        decode_seconds += t1 - t0
        forward_seconds += t2 - t1
        packs[row : row + len(chunk)] = pool_token_pack(tokens)
        row += len(chunk)
        if start and start % (args.batch * 40) == 0:
            done = start + len(chunk)
            rate = (time.monotonic() - started) / done
            print(f"  {done}/{len(ids)} {rate * 1000:.1f} ms/img", flush=True)
    elapsed = time.monotonic() - started

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, ids=np.asarray(ids), packs=packs)
    record = {
        "n": len(ids),
        "provider": args.provider,
        "batch": args.batch,
        "encoder_key": encoder_key(onnx),
        "ms_per_image_total": round(elapsed * 1000 / max(1, len(ids)), 3),
        "ms_per_image_decode": round(decode_seconds * 1000 / max(1, len(ids)), 3),
        "ms_per_image_forward": round(forward_seconds * 1000 / max(1, len(ids)), 3),
        "seconds": round(elapsed, 1),
    }
    args.out.with_suffix(".timing.json").write_text(json.dumps(record, indent=1))
    print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
