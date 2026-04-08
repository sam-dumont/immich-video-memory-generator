"""Pipeline orchestration for the generate command.

Bridges CLI to SmartPipeline + generate_memory: fetches assets from
Immich, runs analysis, and generates the final video.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.cli._helpers import print_error, print_success
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config


def run_pipeline_and_generate(
    *,
    assets: list,
    live_photo_clips: list | None = None,
    photo_assets: list | None = None,
    include_photos: bool = False,
    analysis_depth: str = "fast",
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    duration: float,
    transition: str,
    music: str | None,
    music_volume: float = 0.5,
    no_music: bool = False,
    output_path: Path,
    output_resolution: str | None = None,
    scale_mode: str | None = None,
    output_format: str | None = None,
    add_date_overlay: bool = False,
    debug_preserve_intermediates: bool = False,
    privacy_mode: bool = False,
    title_override: str | None = None,
    subtitle_override: str | None = None,
    memory_type: str | None,
    person_names: list[str],
    date_range: DateRange,
    upload_to_immich: bool,
    album: str | None,
    memory_preset_params: dict | None = None,
) -> tuple[Path, bool, str | None]:
    """Run smart pipeline analysis + video generation.

    Returns (result_path, should_upload, album_name).
    """
    from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.generate import GenerationParams, assets_to_clips, generate_memory

    clips = assets_to_clips(assets)
    if live_photo_clips:
        clips.extend(live_photo_clips)
    has_photos = include_photos and photo_assets
    if not clips and not has_photos:
        print_error("No usable content (no video clips or photos)")
        sys.exit(1)

    import logging
    import time as _time

    _runner_logger = logging.getLogger(__name__)

    print_success(f"{len(clips)} clips ready for generation")

    # WHY: ONE unified task covers the entire pipeline (analysis → generation).
    # The adaptive ETA in LiveDisplay uses elapsed/percentage, so it
    # auto-adjusts whether analysis is cached (fast) or uncached (slow).
    # Analysis: 0-20%, Generation: 20-100%.
    # (Real timing data: analysis ~83s/22%, generation ~295s/78%)
    task = progress.add_task("Analyzing clips...", total=100)
    _pipeline_start = _time.monotonic()

    pipeline_config = PipelineConfig(
        hdr_only=False,
        prioritize_favorites=True,
        analysis_depth=analysis_depth,
    )
    target_seconds = duration
    pipeline_config.target_clips = max(
        10,
        int(target_seconds / pipeline_config.avg_clip_duration),
    )

    analysis_cache = VideoAnalysisCache(db_path=config.cache.database_path)
    thumbnail_cache = ThumbnailCache(cache_dir=config.cache.cache_path / "thumbnails")
    pipeline = SmartPipeline(
        client=client,
        analysis_cache=analysis_cache,
        thumbnail_cache=thumbnail_cache,
        config=pipeline_config,
        analysis_config=config.analysis,
        app_config=config,
    )

    def pipeline_progress(status: dict) -> None:
        pct = status.get("overall_progress", 0)
        phase_name = status.get("current_phase", "")
        progress.update(
            task,
            completed=int(pct * 20),
            description=f"Analyzing: {phase_name}",
        )

    # Phase 1-3: Analyze video clips
    analyzed_videos = pipeline.run_analysis(clips, progress_callback=pipeline_progress)

    # Merge photos into the unified selection pool (if enabled)
    all_candidates = _merge_photos_into_pool(
        analyzed_videos,
        photo_assets=photo_assets,
        include_photos=include_photos,
        config=config,
        client=client,
        work_dir=output_path.parent,
    )

    # Phase 4: Unified selection (videos + photos compete together)
    pipeline_result = pipeline.run_selection(all_candidates)
    _analysis_time = _time.monotonic() - _pipeline_start
    selected_clips = pipeline_result.selected_clips
    clip_segments = pipeline_result.clip_segments

    if not selected_clips:
        print_error("Pipeline selected no clips")
        sys.exit(1)

    print_success(f"Selected {len(selected_clips)} clips for final video")

    should_upload = upload_to_immich or config.upload.enabled
    album_name = album or config.upload.album_name
    person_name = person_names[0] if person_names else None

    def gen_progress(phase: str, frac: float, msg: str) -> None:
        scaled = 20 + int(frac * 80)
        progress.update(task, completed=scaled, description=msg)

    # WHY: Photos are now in selected_clips as IMAGE-type assets.
    # generate.py's _extract_clips will detect IMAGE type and render them.
    # Setting include_photos=False prevents the old _add_photos_if_enabled path.
    gen_params = GenerationParams(
        clips=selected_clips,
        output_path=output_path,
        config=config,
        client=client,
        transition=transition,
        output_resolution=output_resolution,
        scale_mode=scale_mode,
        output_format=output_format,
        add_date_overlay=add_date_overlay,
        debug_preserve_intermediates=debug_preserve_intermediates,
        privacy_mode=privacy_mode,
        title=title_override,
        subtitle=subtitle_override,
        music_path=Path(music) if music and music != "auto" else None,
        music_volume=music_volume,
        no_music=no_music,
        upload_enabled=should_upload,
        upload_album=album_name,
        clip_segments=clip_segments,
        memory_type=memory_type,
        person_name=person_name,
        date_start=date_range.start,
        date_end=date_range.end,
        include_photos=False,
        photo_assets=None,
        target_duration_seconds=duration,
        progress_callback=gen_progress,
        memory_preset_params=memory_preset_params or {},
    )

    result_path = generate_memory(gen_params)
    _total_time = _time.monotonic() - _pipeline_start
    _gen_time = _total_time - _analysis_time
    progress.update(task, completed=100)

    _runner_logger.info(
        "Full pipeline timing (%d clips, %.1fs total): "
        "analysis=%.1fs (%.0f%%), generation=%.1fs (%.0f%%)",
        len(selected_clips),
        _total_time,
        _analysis_time,
        _analysis_time / _total_time * 100 if _total_time > 0 else 0,
        _gen_time,
        _gen_time / _total_time * 100 if _total_time > 0 else 0,
    )

    return result_path, should_upload, album_name


def _merge_photos_into_pool(
    analyzed_videos: list,
    *,
    photo_assets: list | None,
    include_photos: bool,
    config: Config,
    client: SyncImmichClient,
    work_dir: Path,
) -> list:
    """Score photos and merge them as ClipWithSegment into the video pool.

    Returns the combined list of video + photo candidates for unified selection.
    When photos are disabled or absent, returns the video list unchanged.
    """
    if not include_photos or not photo_assets:
        return analyzed_videos

    import logging

    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.photos.photo_pipeline import score_photos

    _logger = logging.getLogger(__name__)

    photo_dir = work_dir / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)

    download_fn = client.download_asset
    thumbnail_fn = client.get_asset_thumbnail
    photo_duration = config.photos.duration

    scored = score_photos(
        assets=photo_assets,
        config=config.photos,
        video_clip_count=len(analyzed_videos),
        work_dir=photo_dir,
        download_fn=download_fn,
        db_path=config.cache.database_path,
        app_config=config,
        thumbnail_fn=thumbnail_fn,
    )

    photo_candidates = []
    for asset, photo_score in scored:
        clip = VideoClipInfo(
            asset=asset,
            duration_seconds=photo_duration,
            width=asset.width,
            height=asset.height,
        )
        photo_candidates.append(
            ClipWithSegment(
                clip=clip,
                start_time=0.0,
                end_time=photo_duration,
                score=photo_score,
            )
        )

    _logger.info(
        f"Unified pool: {len(analyzed_videos)} video + {len(photo_candidates)} photo candidates"
    )

    return analyzed_videos + photo_candidates


def fetch_videos_and_live_photos(
    *,
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    date_ranges: list[DateRange],
    person_ids: list[str],
    use_live_photos: bool,
) -> tuple[list, list]:
    """Fetch video assets and optionally live photo clips.

    Returns (assets, live_photo_clips).
    """
    task = progress.add_task("Fetching videos...", total=None)

    all_assets = []
    for dr in date_ranges:
        if len(person_ids) > 1:
            batch = client.get_videos_for_any_person(person_ids, dr)
        elif len(person_ids) == 1:
            batch = client.get_videos_for_person_and_date_range(person_ids[0], dr)
        else:
            batch = client.get_videos_for_date_range(dr)
        all_assets.extend(batch)

    # Deduplicate across date ranges
    seen: dict[str, object] = {}
    assets = []
    for a in all_assets:
        if a.id not in seen:
            seen[a.id] = True
            assets.append(a)

    progress.update(task, completed=True)
    print_success(f"Found {len(assets)} videos")

    live_photo_clips: list = []
    if use_live_photos:
        from immich_memories.analysis.live_photo_pipeline import fetch_live_photo_clips

        lp_task = progress.add_task("Fetching live photos...", total=None)
        all_lp_clips: list = []
        all_lp_video_ids: set[str] = set()
        for dr in date_ranges:
            lp_clips, lp_vid_ids = fetch_live_photo_clips(
                client,
                dr,
                person_id=person_ids[0] if len(person_ids) == 1 else None,
                person_ids=person_ids if len(person_ids) > 1 else None,
                config=config,
            )
            all_lp_clips.extend(lp_clips)
            all_lp_video_ids.update(lp_vid_ids)

        if all_lp_video_ids:
            assets = [a for a in assets if a.id not in all_lp_video_ids]
        live_photo_clips = all_lp_clips
        progress.update(lp_task, completed=True)
        if live_photo_clips:
            print_success(f"Found {len(live_photo_clips)} live photo clips")

    return assets, live_photo_clips
