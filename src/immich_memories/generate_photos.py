"""Photo rendering, budget allocation, and clip merging for generate pipeline."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    TitleScreenSettings,
)

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.generate import GenerationParams

logger = logging.getLogger(__name__)


def _add_photos_if_enabled(
    assembly_clips: list[AssemblyClip],
    params: GenerationParams,
    run_output_dir: Path,
) -> list[AssemblyClip]:
    """Add photo clips to assembly if photo support is enabled."""
    if not params.include_photos or not params.photo_assets:
        return assembly_clips

    # Pre-selected path: skip scoring, render only selected photos
    if params.selected_photo_ids is not None:
        from immich_memories.photos.photo_pipeline import render_photo_clips

        selected_assets = [a for a in params.photo_assets if a.id in params.selected_photo_ids]
        if not selected_assets:
            return assembly_clips

        photo_dir = run_output_dir / "photos"
        photo_dir.mkdir(exist_ok=True)

        download_fn = params.client.download_asset if params.client else None
        thumbnail_fn = params.client.get_asset_thumbnail if params.client else None
        if not download_fn:
            logger.warning("No Immich client — cannot download photos")
            return assembly_clips

        target_w, target_h = _detect_photo_resolution(params)
        photo_clips = render_photo_clips(
            assets=selected_assets,
            config=params.config.photos,
            target_w=target_w,
            target_h=target_h,
            work_dir=photo_dir,
            download_fn=download_fn,
            video_clip_count=len(assembly_clips),
            thumbnail_fn=thumbnail_fn,
        )
        return _merge_by_date(assembly_clips, photo_clips)

    # Fallback: full scoring + budget at generation time (CLI path)
    effective_duration = params.target_duration_seconds
    if effective_duration is None:
        effective_duration = sum(c.duration for c in assembly_clips) * 1.25

    video_clips, photo_clips = _apply_unified_budget(
        assembly_clips, params, run_output_dir, target_override=effective_duration
    )

    return _merge_by_date(video_clips, photo_clips)


def _detect_photo_resolution(params: GenerationParams) -> tuple[int, int]:
    """Detect the correct resolution for photo rendering.

    WHY: config.output.resolution_tuple always returns landscape (1920x1080).
    But if the majority of video clips are portrait, the assembly pipeline
    will swap to portrait (1080x1920). Photos must match or they get
    double-blur-backgrounded — once by the renderer, once by the assembler.
    """
    target_w, target_h = params.config.output.resolution_tuple
    portrait_count = sum(1 for c in params.clips if c.height > c.width)
    if portrait_count > len(params.clips) // 2 and target_w > target_h:
        target_w, target_h = target_h, target_w
        logger.info(f"Photos: detected portrait orientation, rendering to {target_w}x{target_h}")
    return target_w, target_h


def _render_photos(
    params: GenerationParams, output_dir: Path, video_clip_count: int
) -> list[AssemblyClip]:
    """Render photo assets as animated video clips for assembly."""
    from immich_memories.photos.photo_pipeline import render_photo_clips

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    target_w, target_h = _detect_photo_resolution(params)
    download_fn = params.client.download_asset if params.client else None
    thumbnail_fn = params.client.get_asset_thumbnail if params.client else None
    if not download_fn:
        logger.warning("No Immich client — cannot download photos")
        return []

    return render_photo_clips(
        assets=params.photo_assets or [],
        config=params.config.photos,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=download_fn,
        video_clip_count=video_clip_count,
        thumbnail_fn=thumbnail_fn,
    )


def _apply_unified_budget(
    assembly_clips: list[AssemblyClip],
    params: GenerationParams,
    output_dir: Path,
    target_override: float | None = None,
) -> tuple[list[AssemblyClip], list[AssemblyClip]]:
    """Apply unified budget: score photos, select within budget, render selected.

    Returns (filtered_video_clips, rendered_photo_clips).
    """
    from immich_memories.analysis.unified_budget import BudgetCandidate
    from immich_memories.photos.photo_pipeline import render_photo_clips, score_and_select_photos

    target = target_override or params.target_duration_seconds
    assert target is not None
    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    download_fn = params.client.download_asset if params.client else None
    thumbnail_fn = params.client.get_asset_thumbnail if params.client else None
    if not download_fn:
        logger.warning("No Immich client — cannot download photos")
        return assembly_clips, []

    # Build video candidates from assembly clips
    video_candidates = [
        BudgetCandidate(
            asset_id=c.asset_id,
            duration=c.duration,
            score=0.5,  # Videos already selected by SmartPipeline — uniform base
            candidate_type="video",
            date=_parse_clip_date(c.date),
            is_favorite=False,
        )
        for c in assembly_clips
    ]

    title_settings = _build_title_settings_for_overhead(params)
    clip_dates = [c.date or "" for c in assembly_clips]

    photo_result = score_and_select_photos(
        photo_assets=params.photo_assets or [],
        video_candidates=video_candidates,
        config=params.config,
        target_duration=target,
        work_dir=photo_dir,
        download_fn=download_fn,
        thumbnail_fn=thumbnail_fn,
        title_settings=title_settings,
        clip_dates=clip_dates,
        memory_type=params.memory_type,
        transition_duration=params.transition_duration,
    )

    selection = photo_result.selection

    logger.info(f"Unified budget: target={target:.0f}s, content={selection.content_duration:.1f}s")

    # Filter video clips to kept set
    filtered_videos = [c for c in assembly_clips if c.asset_id in selection.kept_video_ids]

    # Render only selected photos
    selected_photo_ids_set = set(selection.selected_photo_ids)
    selected_assets = [
        asset for asset, _ in photo_result.scored_photos if asset.id in selected_photo_ids_set
    ]

    target_w, target_h = _detect_photo_resolution(params)
    photo_clips = render_photo_clips(
        assets=selected_assets,
        config=params.config.photos,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=download_fn,
        video_clip_count=len(filtered_videos),
        thumbnail_fn=thumbnail_fn,
    )

    logger.info(
        f"Unified selection: {len(filtered_videos)} videos + "
        f"{len(photo_clips)} photos = {selection.content_duration:.0f}s content"
    )

    return filtered_videos, photo_clips


def _parse_clip_date(date_str: str | None) -> datetime:
    """Parse a date string from AssemblyClip into datetime."""
    from datetime import UTC

    if not date_str:
        return datetime(2000, 1, 1, tzinfo=UTC)
    try:
        return datetime.fromisoformat(date_str).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return datetime(2000, 1, 1, tzinfo=UTC)


def _build_title_settings_for_overhead(params: GenerationParams):
    """Build minimal TitleScreenSettings for overhead estimation."""
    if not params.config.title_screens.enabled:
        return None
    return TitleScreenSettings(
        enabled=True,
        title_duration=params.config.title_screens.title_duration,
        month_divider_duration=params.config.title_screens.month_divider_duration,
        ending_duration=params.config.title_screens.ending_duration,
        show_month_dividers=params.config.title_screens.show_month_dividers,
        month_divider_threshold=params.config.title_screens.month_divider_threshold,
    )


def _merge_by_date(
    video_clips: list[AssemblyClip], photo_clips: list[AssemblyClip]
) -> list[AssemblyClip]:
    """Interleave video and photo clips by date, videos first for ties."""
    all_clips = video_clips + photo_clips
    all_clips.sort(key=lambda c: c.date or "")
    return all_clips


def _interleave_clip_types(
    clips: list[AssemblyClip],
    max_consecutive: int = 2,
) -> list[AssemblyClip]:
    """Break up consecutive same-type clips by swapping with nearest different type.

    Preserves approximate chronological order while ensuring no more than
    max_consecutive clips of the same type (photo vs video) appear in a row.
    Uses a forward pass for head/middle runs and a tail fix for end runs.
    """
    if len(clips) <= max_consecutive:
        return clips

    result = clips.copy()

    # Forward pass: fix runs in the head and middle
    for i in range(max_consecutive, len(result)):
        current = result[i].is_photo
        if all(result[i - k].is_photo == current for k in range(1, max_consecutive + 1)):
            for j in range(i + 1, len(result)):
                if result[j].is_photo != current:
                    result[i], result[j] = result[j], result[i]
                    break

    # Tail fix: if the last N clips are all the same type and N > max,
    # pull a different-type clip from earlier into the tail to break it
    tail_type = result[-1].is_photo
    tail_run = 0
    for k in range(len(result) - 1, -1, -1):
        if result[k].is_photo == tail_type:
            tail_run += 1
        else:
            break

    if tail_run > max_consecutive:
        swap_from = len(result) - tail_run
        for j in range(swap_from, -1, -1):
            if result[j].is_photo != tail_type:
                clip = result.pop(j)
                insert_at = len(result) - max_consecutive
                result.insert(insert_at, clip)
                break

    return result


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
