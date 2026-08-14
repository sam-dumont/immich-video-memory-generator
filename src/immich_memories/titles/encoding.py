"""Video encoding utilities and resolution helpers for title screens.

This module provides:
- Encoding arguments derived from the resolved output plan
- Resolution lookups for different orientations (landscape, portrait, square)
- HLG/HDR colorspace metadata for concat compatibility
"""

from __future__ import annotations

from immich_memories.processing.clip_encoder import encoder_args_for_plan
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.hdr_utilities import (
    _get_colorspace_filter,
    _get_hdr_conversion_filter,
)


def standalone_title_encoding_plan() -> EncodingPlan:
    """Return the explicit SDR/H.264 contract for standalone title commands."""
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-preset", "fast", "-crf", "17"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
        crf=17,
    )


def title_encoder_args(plan: EncodingPlan) -> list[str]:
    """Build title-video FFmpeg arguments from an already-resolved plan."""
    return encoder_args_for_plan(plan)


def title_color_filter(plan: EncodingPlan, *, rgb_input: bool = True) -> str:
    """Convert rendered SDR/RGB title pixels to the plan's exact transfer."""
    target = plan.target_transfer.value if plan.hdr else "sdr"
    parts: list[str] = []
    if rgb_input:
        # WHY: HDR Taichi frames arrive as rgb48le. Preserve their bit depth
        # and chroma until zscale applies the SDR-to-HLG/PQ transfer; an 8-bit
        # yuv420p intermediate would create banding in a nominal Main10 title.
        parts.append("format=yuv444p16le" if plan.hdr else "format=yuv420p")
    conversion = _get_hdr_conversion_filter(
        "sdr",
        target,
        source_primaries="bt709",
        required=True,
    )
    if conversion:
        parts.append(conversion.removeprefix(","))
    parts.extend(
        (
            _get_colorspace_filter(target).removeprefix(","),
            f"format={plan.pixel_format}",
        )
    )
    return ",".join(parts)


# Standard resolutions for each orientation
ORIENTATION_RESOLUTIONS: dict[str, dict[str, tuple[int, int]]] = {
    "landscape": {
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
    },
    "portrait": {
        "720p": (720, 1280),
        "1080p": (1080, 1920),
        "4k": (2160, 3840),
    },
    "square": {
        "720p": (720, 720),
        "1080p": (1080, 1080),
        "4k": (2160, 2160),
    },
}


def get_resolution_for_orientation(
    orientation: str,
    resolution: str = "1080p",
) -> tuple[int, int]:
    """Get the appropriate resolution for an orientation.

    Args:
        orientation: One of "landscape", "portrait", "square".
        resolution: One of "720p", "1080p", "4k".

    Returns:
        Tuple of (width, height) for the given orientation and resolution.
    """
    if orientation not in ORIENTATION_RESOLUTIONS:
        orientation = "landscape"
    if resolution not in ORIENTATION_RESOLUTIONS[orientation]:
        resolution = "1080p"
    return ORIENTATION_RESOLUTIONS[orientation][resolution]
