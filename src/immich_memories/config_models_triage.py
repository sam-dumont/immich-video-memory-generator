"""Triage heads: cheap per-picture categorization that nominates, never vetoes."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

# The one place the encoder artifact's host is named; `models fetch` verifies the
# digest pinned in triage/encoder.py whatever this points at.
DINOV2_SMALL_ONNX_URL = (
    "https://github.com/sam-dumont/immich-video-memory-generator/"
    "releases/download/models-v1/dinov2-small-478164cd.onnx"
)


class TriageConfig(BaseModel):
    """Settings for the triage heads (frozen DINOv2 + linear heads over previews).

    Off by default while the first head ships: with `enabled: false` the
    pipeline is byte-identical to a build without the package. On, but with
    the encoder absent from disk, the run continues without head facts.
    """

    enabled: bool = Field(
        default=False,
        description="Categorize every preview with the triage heads before selection",
    )
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
    bundle: str = Field(
        default="",
        description="Head bundle (.npz) to serve; empty means the public bundle shipped in the package",
    )

    @property
    def encoder_path(self) -> Path:
        return Path(self.encoder).expanduser()

    @property
    def bundle_path(self) -> Path | None:
        return Path(self.bundle).expanduser() if self.bundle else None
