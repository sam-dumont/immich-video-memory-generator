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


def _detect_photo_resolution(params: GenerationParams) -> tuple[int, int]:
    """Return the same memoized canvas used by final assembly."""
    from immich_memories.processing.output_canvas import resolve_generation_canvas

    canvas = resolve_generation_canvas(params)
    return canvas.width, canvas.height


def _render_photo_as_clip(
    clip: VideoClipInfo,
    params: GenerationParams,
    output_dir: Path,
) -> AssemblyClip | None:
    """Download and render a photo as an animated video clip for assembly.

    Uses the same rendering pipeline as photo_pipeline._render_single_photo:
    downloads from Immich, prepares the source (HEIC decode, gain map),
    then streams Ken Burns frames to FFmpeg.
    """
    from immich_memories.photos.photo_pipeline import _render_single_photo

    if not params.client:
        logger.warning("No Immich client — cannot render photo clip")
        return None

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    target_w, target_h = _detect_photo_resolution(params)
    photo_config = params.config.photos

    result = _render_single_photo(
        asset=clip.asset,
        config=photo_config,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=params.client.download_asset,
    )
    if result is None:
        logger.warning(f"Failed to render photo {clip.asset.id}")
    return result
