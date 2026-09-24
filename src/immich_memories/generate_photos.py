"""Photo rendering, budget allocation, and clip merging for generate pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    AssemblyClip,
)

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.generate import GenerationParams

logger = logging.getLogger(__name__)


def detect_photo_resolution(params: GenerationParams) -> tuple[int, int]:
    """Return the same memoized canvas used by final assembly."""
    from immich_memories.processing.output_canvas import resolve_generation_canvas

    canvas = resolve_generation_canvas(params)
    return canvas.width, canvas.height


def render_photo_as_clip(
    clip: VideoClipInfo,
    params: GenerationParams,
    output_dir: Path,
    *,
    source_path: Path | None = None,
    duration_seconds: float | None = None,
) -> AssemblyClip | None:
    """Download and render a photo as an animated video clip for assembly.

    Uses the same rendering pipeline as photo_pipeline.render_single_photo:
    downloads from Immich, prepares the source (HEIC decode, gain map),
    then streams Ken Burns frames to FFmpeg.
    """
    from immich_memories.photos.photo_pipeline import render_single_photo

    if not params.client and source_path is None:
        logger.warning("No Immich client — cannot render photo clip")
        return None

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    target_w, target_h = detect_photo_resolution(params)
    photo_config = params.config.photos
    if duration_seconds is not None:
        photo_config = photo_config.model_copy(update={"duration": duration_seconds})

    result = render_single_photo(
        asset=clip.asset,
        config=photo_config,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=params.client.download_asset if params.client else None,
        source_path=source_path,
    )
    if result is None:
        logger.warning(f"Failed to render photo {clip.asset.id}")
    return result


def _render_video_frame_as_clip(
    clip: VideoClipInfo,
    params: GenerationParams,
    output_dir: Path,
    *,
    video_path: Path,
    frame_seconds: float,
) -> AssemblyClip | None:
    """Sample one chosen video frame and render it through the photo pipeline."""
    from immich_memories.processing.frame_sampling import extract_frame_at

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)
    target_w, _target_h = detect_photo_resolution(params)
    frame_path = extract_frame_at(
        video_path,
        timestamp=frame_seconds,
        width=clip.width or target_w,
        output_path=photo_dir / f"{clip.asset.id}_editorial-frame.jpg",
    )
    if frame_path is None:
        logger.warning("Failed to sample editorial still for %s", clip.asset.id)
        return None
    segment = params.clip_segments.get(clip.asset.id)
    duration_seconds = None if segment is None else segment[1] - segment[0]
    return render_photo_as_clip(
        clip,
        params,
        output_dir,
        source_path=frame_path,
        duration_seconds=duration_seconds,
    )
