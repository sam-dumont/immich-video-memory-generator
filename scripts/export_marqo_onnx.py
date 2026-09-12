#!/usr/bin/env python3
"""Export the pinned Marqo NSFW detector to ONNX and prove it still decides the same.

This is the maintainer tool that produced the artifact the product ships:
`nsfw_marqo` runs the export through ONNX Runtime, and nothing in the installed
package imports torch, torchvision or timm. Rerun it only to re-cut the export,
then publish the file and set `MARQO_ONNX_SHA256` to the digest printed here.

It writes the graph, compares ONNX Runtime against torch on the images it is
pointed at, and refuses to leave a file behind if the two disagree about any
label. `--verify-pixels` feeds the graph the product's own torch-free transform
instead of timm's, which is the lane the shipped detector actually uses.

The torch family is deliberately not a declared dependency any more, so bring it
for the length of the export only:

    uv run --with torch --with torchvision --with timm --with onnxscript \
        python scripts/export_marqo_onnx.py --out /tmp/nsfw-marqo-384.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_preparation_detectors import (
    MARQO_REPO,
    MARQO_REVISION,
    decide,
    marqo_pixels,
)

DEFAULT_IMAGES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hdr_samples"
OPSET = 17


class TorchMarqo:
    """The timm/torch detector this export replaces, kept here as the reference."""

    def __init__(self, *, allow_downloads: bool, cache_dir: str | None) -> None:
        import timm
        import torch
        from huggingface_hub import hf_hub_download

        for filename in ("config.json", "model.safetensors"):
            hf_hub_download(
                MARQO_REPO,
                filename,
                revision=MARQO_REVISION,
                cache_dir=cache_dir,
                local_files_only=not allow_downloads,
            )
        self.torch = torch
        self.model = timm.create_model(
            f"hf_hub:{MARQO_REPO}@{MARQO_REVISION}", pretrained=True
        ).eval()
        config = timm.data.resolve_data_config({}, model=self.model)
        self.transform = timm.data.create_transform(**config, is_training=False)
        self.classes = self.model.pretrained_cfg["label_names"]

    def batch(self, images: list[Any]) -> Any:
        pixels = self.torch.stack([self.transform(image) for image in images])
        with self.torch.no_grad():
            return self.model(pixels).softmax(-1).numpy()


def load_images(directory: Path) -> list[Any]:
    from PIL import Image

    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not paths:
        raise SystemExit(f"no images in {directory}")
    return [Image.open(path).convert("RGB") for path in paths]


def export(detector: TorchMarqo, out: Path, images: list[Any]) -> None:
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
    detector: TorchMarqo, out: Path, images: list[Any], *, own_pixels: bool
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
        help="feed the ONNX graph the shipped torch-free transform instead of timm's",
    )
    options = parser.parse_args(argv)

    detector = TorchMarqo(
        allow_downloads=options.allow_downloads, cache_dir=options.cache_dir or None
    )
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
