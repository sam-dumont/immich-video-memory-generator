"""Build the CUDA image's read-only model directory from the application's pins."""

from __future__ import annotations

import sys
from pathlib import Path

from immich_memories.analysis.editorial_laya_reader import unpack_checkpoint
from immich_memories.analysis.editorial_preparation_detectors import DETECTOR_SNAPSHOTS
from immich_memories.pinned_models import (
    CAPTION_MODEL,
    CAPTION_PROJECTOR,
    ENCODER,
    LAYA_AUDIENCE_ONNX,
    LAYA_MAX_BYTES,
    MARQO_ONNX,
    fetch_pinned_model,
)


def build_bundle(destination: Path) -> None:
    """Fetch all small-model weights at image build time, checking each release digest."""
    from huggingface_hub import hf_hub_download

    artifacts = {
        "dinov2-small.onnx": ENCODER,
        "nsfw-marqo-384.onnx": MARQO_ONNX,
        "laya.tar.gz": LAYA_AUDIENCE_ONNX,
        "captioner/model.gguf": CAPTION_MODEL,
        "captioner/mmproj.gguf": CAPTION_PROJECTOR,
    }
    for name, pin in artifacts.items():
        fetch_pinned_model(
            url=pin.url,
            destination=destination / name,
            sha256=pin.sha256,
            max_bytes=LAYA_MAX_BYTES,
        )
    archive = destination / "laya.tar.gz"
    unpack_checkpoint(archive, destination / "laya")
    archive.unlink()
    for repo, revision, files in DETECTOR_SNAPSHOTS:
        for name in files:
            hf_hub_download(
                repo, name, revision=revision, cache_dir=str(destination / "huggingface")
            )


if __name__ == "__main__":
    build_bundle(Path(sys.argv[1]))
