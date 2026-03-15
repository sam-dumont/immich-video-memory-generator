"""Build AssemblyContext from settings and clips.

Resolves target resolution, HDR type, pixel format, and colorspace
by analyzing the input clips before assembly begins.
"""

from __future__ import annotations

import logging
import sys

from immich_memories.config import get_config
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)
from immich_memories.processing.ffmpeg_prober import VideoProber
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.hdr_utilities import (
    _detect_color_primaries,
    _get_clip_hdr_types,
    _get_colorspace_filter,
    _get_dominant_hdr_type,
)

logger = logging.getLogger(__name__)


def resolve_target_resolution(
    settings: AssemblySettings,
    prober: VideoProber,
    clips: list[AssemblyClip],
) -> tuple[int, int]:
    """Resolve target resolution from settings, auto-detection, or config default."""
    if settings.target_resolution:
        target_w, target_h = settings.target_resolution
        logger.info(f"Using specified resolution {target_w}x{target_h}")
        target_w, target_h = _swap_if_portrait(prober, clips, target_w, target_h)
    elif settings.auto_resolution:
        target_w, target_h = prober.detect_best_resolution(clips)
    else:
        config = get_config()
        target_w, target_h = config.output.resolution_tuple
        logger.info(f"Using config resolution {target_w}x{target_h}")
        target_w, target_h = _swap_if_portrait(prober, clips, target_w, target_h)
    return target_w, target_h


def _swap_if_portrait(
    prober: VideoProber,
    clips: list[AssemblyClip],
    target_w: int,
    target_h: int,
) -> tuple[int, int]:
    """Swap width/height if majority of clips are portrait."""
    portrait = sum(bool((r := prober.get_video_resolution(c.path)) and r[1] > r[0]) for c in clips)
    if portrait > len(clips) // 2 and target_w > target_h:
        target_w, target_h = target_h, target_w
        logger.info(f"Detected portrait orientation, swapping to {target_w}x{target_h}")
    return target_w, target_h


def create_assembly_context(
    settings: AssemblySettings,
    prober: VideoProber,
    clips: list[AssemblyClip],
    target_w: int | None = None,
    target_h: int | None = None,
) -> AssemblyContext:
    """Create an AssemblyContext with resolved HDR, pixel format, and colorspace."""
    if target_w is None or target_h is None:
        target_w, target_h = resolve_target_resolution(settings, prober, clips)

    pix_fmt = (
        ("p010le" if sys.platform == "darwin" else "yuv420p10le")
        if settings.preserve_hdr
        else "yuv420p"
    )
    target_fps = 60
    hdr_type = _get_dominant_hdr_type(clips) if settings.preserve_hdr else "hlg"

    clip_hdr_types = _get_clip_hdr_types(clips) if settings.preserve_hdr else [None] * len(clips)
    clip_primaries: list[str | None] = []
    if settings.preserve_hdr:
        for clip in clips:
            clip_primaries.append(_detect_color_primaries(clip.path))
    else:
        clip_primaries = [None] * len(clips)

    unique_types = {t for t in clip_hdr_types if t is not None}
    if len(unique_types) > 1:
        logger.warning(
            f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
        )

    colorspace_filter = _get_colorspace_filter(hdr_type) if settings.preserve_hdr else ""

    return AssemblyContext(
        target_w=target_w,
        target_h=target_h,
        pix_fmt=pix_fmt,
        hdr_type=hdr_type,
        clip_hdr_types=clip_hdr_types,
        clip_primaries=clip_primaries,
        colorspace_filter=colorspace_filter,
        target_fps=target_fps,
        fade_duration=settings.transition_duration,
    )
