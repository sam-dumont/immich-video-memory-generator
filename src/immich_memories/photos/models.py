"""Photo support data models.

Contains enums and dataclasses for photo-to-video animation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class AnimationMode(StrEnum):
    """Photo animation mode for converting still images to video clips."""

    KEN_BURNS = "ken_burns"
    FACE_ZOOM = "face_zoom"
    BLUR_BG = "blur_bg"
    COLLAGE = "collage"
    SPLIT = "split"
    AUTO = "auto"


@dataclass
class PhotoClipInfo:
    """A single photo converted to a video clip."""

    asset_id: str
    source_path: Path
    output_path: Path
    width: int
    height: int
    duration: float
    animation_mode: AnimationMode
    score: float

    # Optional face bounding box (x, y, w, h) normalized 0-1
    face_bbox: tuple[float, float, float, float] | None = None
    # GPS coordinates
    latitude: float | None = None
    longitude: float | None = None
    # Original capture date (ISO string)
    date: str | None = None

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height


@dataclass
class PhotoGroup:
    """A group of photos to be animated together (single or series)."""

    asset_ids: list[str] = field(default_factory=list)
    animation_mode: AnimationMode = AnimationMode.AUTO

    @property
    def is_series(self) -> bool:
        return len(self.asset_ids) > 1
