"""Triage heads: cheap per-picture categorization that nominates, never vetoes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# The one place the encoder artifact's host is named; `models fetch` verifies the
# digest pinned in triage/encoder.py whatever this points at.
DINOV2_SMALL_ONNX_URL = (
    "https://github.com/sam-dumont/immich-video-memory-generator/"
    "releases/download/models-v1/dinov2-small-478164cd.onnx"
)


class TriageConfig(BaseModel):
    """Encoder artifact and device used by editorial preparation."""

    encoder: str = Field(
        default="~/.immich-memories/models/triage/dinov2-small.onnx",
        description=(
            "DINOv2-small ONNX export (88 MB, digest-pinned in code); not vendored — "
            "place it here or point at your copy"
        ),
    )
    encoder_url: str = Field(
        default=DINOV2_SMALL_ONNX_URL,
        description="Where `models fetch` downloads the pinned encoder from",
    )
    provider: Literal["auto", "cpu", "cuda", "coreml"] = Field(
        default="auto",
        description=(
            "ONNX Runtime execution provider for the encoder. `auto` takes CUDA where it is "
            "present and CPU otherwise, and never takes CoreML: measured on this export CoreML "
            "runs 6-8x slower than the CPU provider and holds 9x the memory. Name it to re-test it"
        ),
    )

    @property
    def encoder_path(self) -> Path:
        return Path(self.encoder).expanduser()
