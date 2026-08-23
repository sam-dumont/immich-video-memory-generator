"""Configuration models for what the finished video looks like.

Transitions and aspect handling, the encode target, title screens, and how a
still photograph becomes a moving clip.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from immich_memories.processing.encoding_plan import HdrMode

logger = logging.getLogger(__name__)


# The assembler fills an aspect mismatch two ways and no more: a blurred, zoomed
# copy of the frame behind the sharp one, or black bars. `smart_crop` and `fill`
# both silently rendered as black bars because no crop path exists for video.
# `fill` keeps that rendering under its real name; `smart_crop` moves to blur,
# which is what someone asking to keep faces in frame was actually after.
_LEGACY_SCALE_MODES = {"smart_crop": "blur", "fill": "fit"}


def normalize_scale_mode(value: str) -> str:
    """Map a retired scale mode onto an implemented one, saying so."""
    replacement = _LEGACY_SCALE_MODES.get(value)
    if replacement is None:
        return value
    logger.warning(
        "scale_mode '%s' no longer exists — no crop path was ever implemented for "
        "video, so it rendered as black bars. Using '%s'.",
        value,
        replacement,
    )
    return replacement


class DefaultsConfig(BaseModel):
    """Default settings for video generation."""

    scale_mode: Literal["fit", "blur"] = Field(
        default="blur",
        description="How to fill an aspect mismatch: 'blur' background or 'fit' (black bars)",
    )
    transition: Literal["cut", "crossfade", "smart", "none"] = "smart"
    transition_duration: float = Field(default=0.5, ge=0, le=2.0)

    @field_validator("scale_mode", mode="before")
    @classmethod
    def map_legacy_scale_mode(cls, v: str) -> str:
        """Keep configs written against the retired modes loading."""
        return normalize_scale_mode(v) if isinstance(v, str) else v


class OutputConfig(BaseModel):
    """Output settings."""

    directory: str = Field(default="~/Videos/Memories")
    format: Literal["mp4", "mov"] = "mp4"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    codec: Literal["h264", "h265", "prores"] = "h264"
    hdr_mode: HdrMode = HdrMode.AUTO
    quality: Literal["high", "medium", "low"] = "high"
    crf: int | None = Field(default=None, ge=0, le=51)

    @field_serializer("hdr_mode")
    def serialize_hdr_mode(self, value: HdrMode) -> str:
        """Serialize the HDR policy as a portable YAML/JSON string."""
        return value.value

    @property
    def effective_crf(self) -> int:
        """CRF derived from quality preset, or explicit override if set."""
        if self.crf is not None:
            return self.crf
        from immich_memories.processing.hdr_utilities import quality_to_crf

        return quality_to_crf(self.quality)

    @property
    def output_path(self) -> Path:
        """Get the expanded output directory path."""
        return Path(self.directory).expanduser()

    @property
    def resolution_tuple(self) -> tuple[int, int]:
        """Get resolution as (width, height) tuple for landscape orientation."""
        resolutions = {
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "4k": (3840, 2160),
        }
        return resolutions[self.resolution]


class TitleScreenConfig(BaseModel):
    """Settings for title screens, month dividers, and ending screens."""

    enabled: bool = Field(
        default=True,
        description="Enable title screens, month dividers, and ending screens",
    )

    # Timing
    title_duration: float = Field(
        default=3.5,
        ge=1.0,
        le=10.0,
        description="Duration of opening title screen in seconds",
    )
    month_divider_duration: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="Duration of month divider screens in seconds",
    )
    ending_duration: float = Field(
        default=7.0,
        ge=2.0,
        le=15.0,
        description="Duration of ending screen in seconds",
    )

    # Localization
    locale: Literal["en", "fr", "auto"] = Field(
        default="auto",
        description="Language for title text (en, fr, or auto-detect)",
    )

    # Visual style
    style_mode: Literal["auto", "random"] = Field(
        default="auto",
        description="Style selection mode (auto = mood-based, random = random selection)",
    )
    animated_background: bool = Field(
        default=True,
        description="Enable subtle background animations (gradient shift, color pulse)",
    )
    show_decorative_lines: bool = Field(
        default=False,
        description="Show decorative line accents on title screens",
    )

    # Month dividers
    show_month_dividers: bool = Field(
        default=True,
        description="Show month divider screens when video spans multiple months",
    )
    month_divider_threshold: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Minimum clips needed in a month to show its divider",
    )

    # Name display
    use_first_name_only: bool = Field(
        default=True,
        description="Use only the first name for titles (e.g., 'John' instead of 'John Smith')",
    )


class PhotoConfig(BaseModel):
    """Photo-to-video animation settings."""

    enabled: bool = Field(default=True, description="Include photos in memory videos")
    max_ratio: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Maximum fraction of clips that can be photos (0.50 = 50%)",
    )
    duration: float = Field(
        default=4.0,
        ge=1.0,
        le=10.0,
        description="Duration per single photo clip in seconds",
    )
    burst_window_seconds: float = Field(
        default=300.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Photos this close together and visually near-identical are one burst; "
            "only the best-scored frame is kept (0 disables burst de-duplication)"
        ),
    )
    burst_hash_threshold: int = Field(
        default=8,
        ge=0,
        le=64,
        description="Perceptual-hash bits two photos may differ by and still be one burst",
    )
    moment_gap_seconds: float = Field(
        default=120.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Time window for treating a photo and a video as the same moment. "
            "A photo this close to a visually matching video is dropped."
        ),
    )
    moment_hash_threshold: int = Field(
        default=10,
        ge=0,
        le=64,
        description=(
            "Perceptual-hash bits a photo may differ from a nearby video and "
            "still count as the same scene (0 = only identical framing)"
        ),
    )
    score_penalty: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Score reduction for photos vs videos (0.2 = photos score 80% of videos)",
    )
