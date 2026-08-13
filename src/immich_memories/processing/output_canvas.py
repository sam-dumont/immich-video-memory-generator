"""Resolve the single pixel canvas used by one generation run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams

Orientation = Literal["landscape", "portrait", "square"]

_LANDSCAPE_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}


class _SizedClip(Protocol):
    """The metadata needed to resolve a canvas without probing media files."""

    width: int
    height: int


@dataclass(frozen=True, slots=True)
class OutputCanvas:
    """One immutable output canvas shared by every rendering phase."""

    width: int
    height: int
    orientation: Orientation


def _orientation_for(clips: Sequence[_SizedClip], fallback: Orientation) -> Orientation:
    portrait = sum(1 for clip in clips if clip.height > clip.width)
    landscape = sum(1 for clip in clips if clip.width >= clip.height)
    if portrait > landscape:
        return "portrait"
    if landscape > portrait:
        return "landscape"
    return fallback


def _tier_for_dimensions(width: int, height: int) -> str:
    max_dimension = max(width, height)
    if max_dimension >= 2160:
        return "4k"
    if max_dimension >= 1080:
        return "1080p"
    return "720p"


def _auto_tier(clips: Sequence[_SizedClip], configured_resolution: tuple[int, int]) -> str:
    tiers = [
        _tier_for_dimensions(clip.width, clip.height)
        for clip in clips
        if clip.width > 0 and clip.height > 0
    ]
    if not tiers:
        return _tier_for_dimensions(*configured_resolution)

    for tier in ("4k", "1080p", "720p"):
        if tiers.count(tier) > len(tiers) / 2:
            return tier
    for tier in ("4k", "1080p", "720p"):
        if tier in tiers:
            return tier
    return "720p"


def _orient(width: int, height: int, orientation: Orientation) -> OutputCanvas:
    long_edge = max(width, height)
    short_edge = min(width, height)
    if orientation == "portrait":
        return OutputCanvas(short_edge, long_edge, orientation)
    if orientation == "square":
        return OutputCanvas(short_edge, short_edge, orientation)
    return OutputCanvas(long_edge, short_edge, orientation)


def resolve_output_canvas(
    *,
    resolution: str | None,
    orientation: str | None,
    configured_resolution: tuple[int, int],
    clips: Sequence[_SizedClip],
) -> OutputCanvas:
    """Resolve resolution tier and orientation once from metadata.

    Explicit command values are authoritative. ``resolution="auto"`` chooses
    the source tier, while an omitted orientation follows the dominant source
    orientation and falls back to the configured canvas on ties.
    """
    configured_orientation: Orientation = (
        "portrait" if configured_resolution[1] > configured_resolution[0] else "landscape"
    )
    effective_orientation: Orientation
    if orientation in {"landscape", "portrait", "square"}:
        effective_orientation = orientation  # type: ignore[assignment]
    else:
        effective_orientation = _orientation_for(clips, configured_orientation)

    if resolution == "auto":
        tier = _auto_tier(clips, configured_resolution)
        width, height = _LANDSCAPE_RESOLUTIONS[tier]
    elif resolution in _LANDSCAPE_RESOLUTIONS:
        width, height = _LANDSCAPE_RESOLUTIONS[resolution]
    else:
        width, height = configured_resolution

    return _orient(width, height, effective_orientation)


def resolve_generation_canvas(params: GenerationParams) -> OutputCanvas:
    """Return and memoize the canvas for one generation request."""
    if params.output_canvas is None:
        params.output_canvas = resolve_output_canvas(
            resolution=params.output_resolution,
            orientation=params.output_orientation,
            configured_resolution=params.config.output.resolution_tuple,
            clips=params.clips,
        )
    return params.output_canvas
