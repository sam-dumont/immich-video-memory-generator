#!/usr/bin/env python3
"""Export the pinned Marqo NSFW detector to ONNX and prove it still decides the same.

The `nsfw_marqo` producer is the only seat that needs torch at run time, and on
Linux torch drags in fifteen CUDA wheels a CPU box never loads. It is a plain
timm vision transformer, so it exports. This writes the graph, then compares
ONNX Runtime against torch on the images it is pointed at and refuses to leave a
file behind if the two disagree about any label.

    uv run python scripts/export_marqo_onnx.py --out /tmp/nsfw-marqo-384.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_preparation_detectors import (
    MARQO_REPO,
    MARQO_REVISION,
    Marqo,
    decide,
)

DEFAULT_IMAGES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hdr_samples"
OPSET = 17
SIDE = 384


def load_images(directory: Path) -> list[Any]:
    from PIL import Image

    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not paths:
        raise SystemExit(f"no images in {directory}")
    return [Image.open(path).convert("RGB") for path in paths]


def marqo_pixels(images: Sequence[Any]) -> Any:
    """timm's resolved transform for this model, without timm or torch.

    Resize the short side to 384 bicubic, centre crop, scale to [0,1] and
    normalise by 0.5/0.5 — the config the pinned checkpoint carries. Written in
    numpy so the ONNX seat can drop the torch family entirely; `--verify-pixels`
    proves it against the timm pipeline before anyone relies on it.
    """
    import numpy as np
    from PIL import Image

    tensors = []
    for image in images:
        width, height = image.size
        scale = SIDE / min(width, height)
        resized = image.resize(
            (max(SIDE, round(width * scale)), max(SIDE, round(height * scale))),
            Image.Resampling.BICUBIC,
        )
        left = (resized.width - SIDE) // 2
        top = (resized.height - SIDE) // 2
        cropped = resized.crop((left, top, left + SIDE, top + SIDE))
        pixels = np.asarray(cropped, dtype=np.float32) / 255.0
        tensors.append(((pixels - 0.5) / 0.5).transpose(2, 0, 1))
    return np.ascontiguousarray(np.stack(tensors), dtype=np.float32)


def export(detector: Marqo, out: Path, images: list[Any]) -> None:
    torch = detector.torch
    example = torch.stack([detector.transform(images[0])])
    with torch.no_grad():
        torch.onnx.export(
            detector.model,
            (example,),
            str(out),
            input_names=["pixels"],
            output_names=["logits"],
            dynamic_axes={"pixels": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=OPSET,
            # One digest-verified file, the way the encoder ships; the dynamo
            # exporter otherwise leaves the weights in a `.onnx.data` sidecar.
            external_data=False,
        )


def agreement(
    detector: Marqo, out: Path, images: list[Any], *, own_pixels: bool
) -> tuple[float, list[str]]:
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    if own_pixels:
        pixels = marqo_pixels(images)
    else:
        pixels = detector.torch.stack([detector.transform(image) for image in images]).numpy()
    logits = session.run(None, {session.get_inputs()[0].name: pixels})[0]
    exp = np.exp(logits - logits.max(-1, keepdims=True))
    onnx_probabilities = exp / exp.sum(-1, keepdims=True)
    torch_probabilities = detector.batch(images)
    disagreements = []
    for row, (left, right) in enumerate(zip(onnx_probabilities, torch_probabilities, strict=True)):
        scores = [
            dict(zip(detector.classes, map(float, side), strict=True)) for side in (left, right)
        ]
        labels = [decide("nsfw_marqo", score)[0] for score in scores]
        if labels[0] != labels[1]:
            disagreements.append(f"image {row}: onnx {labels[0]} vs torch {labels[1]}")
    return float(np.abs(onnx_probabilities - torch_probabilities).max()), disagreements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--allow-downloads", action="store_true")
    parser.add_argument(
        "--verify-pixels",
        action="store_true",
        help="feed the ONNX graph the torch-free numpy transform instead of timm's",
    )
    options = parser.parse_args(argv)

    detector = Marqo(allow_downloads=options.allow_downloads, cache_dir=options.cache_dir or None)
    images = load_images(Path(options.images).expanduser())
    out = Path(options.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    export(detector, out, images)
    worst, disagreements = agreement(detector, out, images, own_pixels=options.verify_pixels)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{MARQO_REPO}@{MARQO_REVISION[:8]} -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256 {digest}")
    lane = "numpy pixels" if options.verify_pixels else "timm pixels"
    print(f"max |onnx - torch| probability {worst:.2e} over {len(images)} images ({lane})")
    if disagreements:
        out.unlink()
        print("LABELS DISAGREE — export removed:\n  " + "\n  ".join(disagreements))
        return 1
    print(f"every label agrees across {len(images)} images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
