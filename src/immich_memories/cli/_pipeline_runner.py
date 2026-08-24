"""Pipeline orchestration for the generate command.

Bridges CLI to SmartPipeline + generate_memory: fetches assets from
Immich, runs analysis, and generates the final video.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis import llm_metrics
from immich_memories.cli._helpers import console, print_error, print_success
from immich_memories.cli._run_summary import render_run_summary
from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import (
        PipelineConfig,
        PipelineResult,
        SmartPipeline,
    )
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config
    from immich_memories.processing.assembly_config import TitleScreenSettings
    from immich_memories.processing.output_canvas import OutputCanvas
    from immich_memories.processing.timeline_budget import TimelinePlan


def _configure_timeline(
    pipeline_config: PipelineConfig,
    *,
    clips: list,
    photo_assets: list | None,
    output_path: Path,
    config: Config,
    memory_type: str | None,
    person_names: list[str],
    date_range: DateRange,
    memory_preset_params: dict | None,
    duration: float,
    transition: str,
) -> tuple[TimelinePlan, TitleScreenSettings | None]:
    """Resolve one plan and apply its strict content budget to selection."""
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import _build_title_settings
    from immich_memories.processing.timeline_budget import plan_timeline

    planning_params = GenerationParams(
        clips=clips,
        output_path=output_path,
        config=config,
        memory_type=memory_type,
        person_name=person_names[0] if person_names else None,
        date_start=date_range.start,
        date_end=date_range.end,
        memory_preset_params=memory_preset_params or {},
    )
    planning_titles = _build_title_settings(planning_params, config, [])
    planning_sources = [*clips, *(list(photo_assets) if photo_assets else [])]
    timeline = plan_timeline(
        planning_sources,
        planning_titles,
        duration,
        memory_type,
        expected_clip_duration=pipeline_config.avg_clip_duration,
        transition_mode=transition,
        transition_duration=config.defaults.transition_duration,
    )
    pipeline_config.target_duration_seconds = timeline.content_budget
    pipeline_config.target_clips = max(
        1,
        math.ceil(timeline.content_budget / pipeline_config.avg_clip_duration),
    )
    return timeline, planning_titles


def _resolve_requested_duration(
    requested_duration: float | None,
    *,
    memory_type: str | None,
    clips: list,
    photos: list | None,
    config: Config,
) -> float:
    """Resolve Auto only after the CLI has discovered usable media."""
    if requested_duration is not None:
        return float(requested_duration)
    if memory_type not in ("trip", "album"):
        raise ValueError(
            "Automatic media-aware duration is currently available for trips and albums only"
        )

    from immich_memories.planning.auto_duration import resolve_trip_auto_duration

    title_config = config.title_screens
    title_duration = title_config.title_duration if title_config.enabled else 0.0
    ending_duration = title_config.ending_duration if title_config.enabled else 0.0
    result = resolve_trip_auto_duration(
        clips,
        photos or [],
        avg_clip_duration=config.analysis.optimal_clip_duration,
        photo_duration=config.photos.duration,
        title_duration=title_duration,
        ending_duration=ending_duration,
    )
    logger.info(
        "%s Auto duration: %.0fs from %d active days (editorial %.0fs, capacity %.0fs)",
        memory_type.capitalize(),
        result.total_seconds,
        result.active_days,
        result.editorial_seconds,
        result.diverse_capacity_seconds,
    )
    return result.total_seconds


def _planning_analysis(
    pipeline: SmartPipeline,
    clips: list,
    progress_callback,
    *,
    dry_run: bool,
) -> list:
    """Choose the cached-only planning path or normal source analysis."""
    if dry_run:
        return pipeline.run_planning_analysis(clips, progress_callback=progress_callback)
    return pipeline.run_analysis(clips, progress_callback=progress_callback)


def _configure_output_canvas(
    pipeline_config: PipelineConfig,
    *,
    clips: list,
    photo_assets: list | None,
    config: Config,
    output_resolution: str | None,
    output_orientation: str | None,
) -> OutputCanvas:
    """Resolve one canvas and align selection quality gates with it."""
    from immich_memories.processing.output_canvas import resolve_output_canvas

    planning_sources = [*clips, *(photo_assets or [])]
    canvas = resolve_output_canvas(
        resolution=output_resolution,
        orientation=output_orientation,
        configured_resolution=config.output.resolution_tuple,
        clips=planning_sources,
    )
    # PipelineConfig expresses output resolution as the short-edge tier.
    pipeline_config.output_resolution = min(canvas.width, canvas.height)
    return canvas


def _stops_before_rendering(*, dry_run: bool, no_render: bool) -> bool:
    """Whether this run ends at the plan instead of producing a file.

    Two callers, one boundary. --dry-run has always stopped here as a side
    effect of being cheap; --no-render asks for the same stop directly, having
    run the real analysis and the verify pass on the way.
    """
    return dry_run or no_render


def _finish_without_rendering(
    *,
    pipeline_result: PipelineResult,
    timeline_plan: TimelinePlan,
    assets: list,
    live_photo_clips: list | None,
    photo_assets: list | None,
    config: Config,
    output_canvas: OutputCanvas,
    output_path: Path,
    memory_type: str | None,
    date_range: DateRange,
    should_upload: bool,
    album_name: str | None,
    music: str | None,
    no_music: bool,
    progress,
    task,
) -> tuple[Path, bool, str | None]:
    """Print the resolved plan and return without crossing the render boundary.

    Reached two ways, and the difference matters. --dry-run gets here having
    used only cached analysis and skipped the verify pass, so its selection is
    a cheap approximation. --no-render gets here having run the real thing;
    only the encode is missing.
    """
    from immich_memories.api.models import AssetType
    from immich_memories.cli._generation_preview import (
        GenerationPreview,
        music_policy,
        print_generation_preview,
    )

    selected_clips = pipeline_result.selected_clips
    selected_photos = sum(clip.asset.type == AssetType.IMAGE for clip in selected_clips)
    preview = GenerationPreview(
        memory_type=memory_type or "custom",
        date_range=date_range.description,
        video_candidates=len(assets),
        live_photo_candidates=len(live_photo_clips or []),
        photo_candidates=len(photo_assets or []),
        selected_videos=len(selected_clips) - selected_photos,
        selected_photos=selected_photos,
        selected_duration=sum(end - start for start, end in pipeline_result.clip_segments.values()),
        timeline=timeline_plan,
        canvas=output_canvas,
        output_path=output_path,
        upload_intent=should_upload,
        music_policy=music_policy(config=config, music=music, no_music=no_music),
    )
    print_generation_preview(preview)
    progress.update(task, completed=100)
    return output_path, should_upload, album_name


class _AttemptPhaseReporter:
    """Share semantic phase messages with CLI and an optional automation attempt."""

    def __init__(self, config: Config, attempt_id: str | None, progress, task) -> None:
        from immich_memories.automation.state_store import AutomationStateStore

        self._attempt_id = attempt_id
        self._store = AutomationStateStore(config.cache.database_path) if attempt_id else None
        self._progress = progress
        self._task = task
        self._started = time.monotonic()

    def emit(self, phase, current: int, total: int, message: str) -> None:
        from immich_memories.operations.phases import PhaseEvent

        now = time.monotonic()
        event = PhaseEvent(phase, current, total, message, now - self._started)
        self._started = now
        if self._store is not None and self._attempt_id is not None:
            try:
                self._store.update_phase(self._attempt_id, event)
            except (KeyError, OSError, RuntimeError, sqlite3.Error):
                logging.getLogger(__name__).warning(
                    "Could not persist operational phase %s", phase.value
                )
        self._progress.update(self._task, description=event.message)


@llm_metrics.collects
def run_pipeline_and_generate(
    *,
    assets: list,
    live_photo_clips: list | None = None,
    photo_assets: list | None = None,
    include_photos: bool = False,
    analysis_depth: str = "auto",
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    duration: float | None,
    transition: str,
    music: str | None,
    music_volume: float = 0.5,
    no_music: bool = False,
    output_path: Path,
    output_resolution: str | None = None,
    output_orientation: str | None = None,
    scale_mode: str | None = None,
    output_format: str | None = None,
    add_date_overlay: bool = False,
    add_place_overlay: bool = False,
    debug_preserve_intermediates: bool = False,
    privacy_mode: bool = False,
    title_override: str | None = None,
    subtitle_override: str | None = None,
    llm_title: bool = False,
    memory_type: str | None,
    person_names: list[str],
    date_range: DateRange,
    upload_to_immich: bool,
    album: str | None,
    memory_preset_params: dict | None = None,
    source: str = "manual",
    memory_key: str | None = None,
    memory_category: str | None = None,
    automation_attempt_id: str | None = None,
    dry_run: bool = False,
    no_render: bool = False,
) -> tuple[Path, bool, str | None]:
    """Run smart pipeline analysis + video generation.

    Returns (result_path, should_upload, album_name).
    """
    from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.generate import GenerationParams, assets_to_clips, generate_memory
    from immich_memories.operations.phases import OperationalPhase
    from immich_memories.tracking.models import normalize_memory_people

    clips = assets_to_clips(assets)
    if live_photo_clips:
        clips.extend(live_photo_clips)
    has_photos = include_photos and photo_assets
    if not clips and not has_photos:
        print_error("No usable content (no video clips or photos)")
        sys.exit(1)

    duration = _resolve_requested_duration(
        duration,
        memory_type=memory_type,
        clips=clips,
        photos=photo_assets if include_photos else None,
        config=config,
    )

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
    phases = _AttemptPhaseReporter(
        config,
        None if dry_run else automation_attempt_id,
        progress,
        task,
    )
    phases.emit(OperationalPhase.DISCOVERY, len(clips), len(clips), "Discovery complete")
    phases.emit(OperationalPhase.DOWNLOAD, 0, len(clips), "Preparing source downloads")

    pipeline_config = PipelineConfig.from_app_config(
        config,
        hdr_only=False,
        prioritize_favorites=True,
        analysis_depth=analysis_depth,
    )
    output_canvas = _configure_output_canvas(
        pipeline_config,
        clips=clips,
        photo_assets=photo_assets if include_photos else None,
        config=config,
        output_resolution=output_resolution,
        output_orientation=output_orientation,
    )
    timeline_plan, planning_titles = _configure_timeline(
        pipeline_config,
        clips=clips,
        photo_assets=photo_assets,
        output_path=output_path,
        config=config,
        memory_type=memory_type,
        person_names=person_names,
        date_range=date_range,
        memory_preset_params=memory_preset_params,
        duration=duration,
        transition=transition,
    )
    _runner_logger.info(
        "Selection timeline: %.1fs content + %.1fs titles - %.1fs transition overlap = %.1fs target",
        timeline_plan.content_budget,
        timeline_plan.title_budget,
        timeline_plan.transition_budget,
        timeline_plan.target_duration,
    )

    analysis_cache = VideoAnalysisCache(db_path=config.cache.database_path)
    thumbnail_cache = ThumbnailCache(
        cache_dir=config.cache.cache_path / "thumbnails",
        max_size_mb=config.cache.thumbnail_cache_max_size_mb,
    )
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
    phases.emit(OperationalPhase.ANALYSIS, 0, len(clips), "Analyzing clips")
    analyzed_videos = _planning_analysis(
        pipeline,
        clips,
        pipeline_progress,
        dry_run=dry_run,
    )

    # Merge photos into the unified selection pool (if enabled)
    all_candidates = _merge_photos_into_pool(
        analyzed_videos,
        live_photo_clips=live_photo_clips,
        photo_assets=photo_assets,
        include_photos=include_photos,
        config=config,
        client=client,
        work_dir=output_path.parent,
        provider_circuit=pipeline.provider_circuit,
        dry_run=dry_run,
        thumbnail_cache=thumbnail_cache,
    )

    all_candidates = _drop_reencoded_sources(all_candidates, config=config)
    all_candidates = _apply_subject_policy(
        all_candidates,
        config=config,
        content_budget_seconds=timeline_plan.content_budget,
        photo_assets=photo_assets,
    )

    # Phase 4: Unified selection (videos + photos compete together)
    phases.emit(OperationalPhase.SELECTION, 0, len(all_candidates), "Selecting clips")
    pipeline_result = pipeline.run_selection(all_candidates, verify=not dry_run)
    _analysis_time = _time.monotonic() - _pipeline_start
    selected_clips = pipeline_result.selected_clips
    clip_segments = pipeline_result.clip_segments

    if not selected_clips:
        print_error("Pipeline selected no clips")
        sys.exit(1)

    from immich_memories.processing.timeline_budget import finalize_selected_timeline

    selected_duration = sum(end - start for start, end in clip_segments.values())
    timeline_plan = finalize_selected_timeline(
        timeline_plan,
        selected_clips,
        selected_duration=selected_duration,
        title_settings=planning_titles,
        memory_type=memory_type,
        transition_mode=transition,
        transition_duration=config.defaults.transition_duration,
    )
    if timeline_plan.divider_policy in {"all", "none"}:
        _runner_logger.info(
            "Final timeline: month dividers=%s (%d/%d), %.1fs estimated, %.1fs soft maximum",
            timeline_plan.divider_policy,
            timeline_plan.max_dividers,
            timeline_plan.eligible_dividers,
            min(selected_duration, timeline_plan.content_budget)
            + timeline_plan.title_budget
            - timeline_plan.transition_budget,
            timeline_plan.soft_max_duration,
        )
    else:
        _runner_logger.info(
            "Final timeline: %.1fs content + %.1fs titles (%d dividers capped)",
            selected_duration,
            timeline_plan.title_budget,
            timeline_plan.max_dividers,
        )

    output_path = _name_after_recipe(
        output_path,
        selected_clips=selected_clips,
        clip_segments=clip_segments,
        memory_type=memory_type,
        date_range=date_range,
        target_duration=timeline_plan.target_duration,
    )

    print_success(f"Selected {len(selected_clips)} clips for final video")

    should_upload = upload_to_immich or config.upload.enabled
    album_name = album or config.upload.album_name
    person_name = person_names[0] if person_names else None

    if _stops_before_rendering(dry_run=dry_run, no_render=no_render):
        return _finish_without_rendering(
            pipeline_result=pipeline_result,
            timeline_plan=timeline_plan,
            assets=assets,
            live_photo_clips=live_photo_clips,
            photo_assets=photo_assets,
            config=config,
            output_canvas=output_canvas,
            output_path=output_path,
            memory_type=memory_type,
            date_range=date_range,
            should_upload=should_upload,
            album_name=album_name,
            music=music,
            no_music=no_music,
            progress=progress,
            task=task,
        )

    def gen_progress(phase: str, frac: float, msg: str) -> None:
        scaled = 20 + int(frac * 80)
        progress.update(task, completed=scaled, description=msg)

    def generation_phase(event) -> None:
        progress.update(task, description=event.message)

    from immich_memories.cli._llm_title import resolve_cli_title

    resolved_title, resolved_subtitle = resolve_cli_title(
        enabled=llm_title,
        title_override=title_override,
        subtitle_override=subtitle_override,
        clips=selected_clips,
        config=config,
        memory_type=memory_type,
        date_range=date_range,
        person_name=person_name,
    )

    # WHY: Photos are now in selected_clips as IMAGE-type assets.
    # generate.py's _extract_clips will detect IMAGE type and render them.
    # Setting include_photos=False prevents the old _add_photos_if_enabled path.
    gen_params = GenerationParams(
        clips=selected_clips,
        output_path=output_path,
        config=config,
        client=client,
        transition=transition,
        transition_duration=config.defaults.transition_duration,
        output_resolution=output_resolution,
        output_orientation=output_orientation,
        output_canvas=output_canvas,
        scale_mode=scale_mode,
        output_format=output_format,
        add_date_overlay=add_date_overlay,
        add_place_overlay=add_place_overlay,
        debug_preserve_intermediates=debug_preserve_intermediates,
        privacy_mode=privacy_mode,
        title=resolved_title,
        subtitle=resolved_subtitle,
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
        source=source,
        memory_key_override=memory_key,
        memory_category=memory_category,
        memory_people=normalize_memory_people(person_names),
        automation_attempt_id=automation_attempt_id,
        include_photos=False,
        photo_assets=None,
        target_duration_seconds=duration,
        timeline_plan=timeline_plan,
        progress_callback=gen_progress,
        phase_callback=generation_phase,
        completed_operational_phase=OperationalPhase.SELECTION,
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

    console.print(
        render_run_summary(
            total_seconds=_total_time,
            analysis_seconds=_analysis_time,
            generation_seconds=_gen_time,
            eligible=len(all_candidates),
            deeply_analyzed=pipeline.last_deep_analysis_count,
            planned=len(selected_clips),
            counters=llm_metrics.active(),
        )
    )

    _send_notification(config, memory_type, "completed", _total_time, str(result_path))

    return result_path, should_upload, album_name


def _send_notification(
    config: Config,
    memory_type: str | None,
    status: str,
    duration: float,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    """Send notification if configured (best-effort, never raises)."""
    notif = config.notifications
    if not notif.enabled or not notif.urls:
        return
    if (status == "completed" and not notif.on_success) or (
        status == "failed" and not notif.on_failure
    ):
        return
    try:
        from immich_memories.automation.notifications import notify_job_complete

        notify_job_complete(
            memory_type=memory_type or "unknown",
            status=status,
            duration_seconds=duration,
            output_path=output_path,
            error=error,
            urls=notif.urls,
            db_path=config.cache.database_path,
            attach_thumbnail=notif.attach_thumbnail,
            cooldown_hours=notif.cooldown_hours,
        )
    except (OSError, RuntimeError):
        logging.getLogger(__name__).debug("Notification failed", exc_info=True)


def _merge_photos_into_pool(
    analyzed_videos: list,
    *,
    live_photo_clips: list | None = None,
    photo_assets: list | None,
    include_photos: bool,
    config: Config,
    client: SyncImmichClient,
    work_dir: Path,
    provider_circuit=None,
    dry_run: bool = False,
    thumbnail_cache=None,
) -> list:
    """Score photos and merge them as ClipWithSegment into the video pool.

    Returns the combined list of video + photo candidates for unified selection.
    When photos are disabled or absent, returns the video list unchanged.
    """
    if not include_photos or not photo_assets:
        return analyzed_videos

    import logging

    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.analysis.source_filter import from_the_camera_roll
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.photos.photo_pipeline import (
        score_photos,
        video_count_for_photo_budget,
    )
    from immich_memories.photos.scoring import score_photo

    _logger = logging.getLogger(__name__)

    # Before anything is fetched or scored: this is the pool both the CLI and
    # the UI actually build, and the rule used to live only on the path
    # neither of them takes.
    photo_assets = from_the_camera_roll(photo_assets, config)
    if not photo_assets:
        return analyzed_videos

    photo_assets = _drop_photos_already_shown_as_motion(
        photo_assets,
        analyzed_videos,
        config=config,
        client=client,
        thumbnail_cache=thumbnail_cache,
    )
    if not photo_assets:
        return analyzed_videos

    photo_duration = config.photos.duration
    if dry_run:
        scored = [(asset, score_photo(asset, config.photos)) for asset in photo_assets]
    else:
        photo_dir = work_dir / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        scored = score_photos(
            assets=photo_assets,
            config=config.photos,
            video_clip_count=video_count_for_photo_budget(
                len(analyzed_videos), len(live_photo_clips or [])
            ),
            work_dir=photo_dir,
            download_fn=client.download_asset,
            db_path=config.cache.database_path,
            app_config=config,
            thumbnail_fn=client.get_asset_thumbnail,
            provider_circuit=provider_circuit,
            thumbnail_cache=thumbnail_cache,
        )

    # What the VLM said about each photo, so the holistic review can read a
    # photograph the way it reads a video. Without it every still arrived as a
    # bare line and survived on the rule that protects unanalysed material.
    from immich_memories.analysis.cache_projection import apply_semantic_payload
    from immich_memories.photos.scoring import semantic_payloads_for

    payloads = semantic_payloads_for(
        config.cache.database_path, [asset.id for asset, _ in scored], config.llm.model
    )

    photo_candidates = []
    for asset, photo_score in scored:
        clip = VideoClipInfo(
            asset=asset,
            duration_seconds=photo_duration,
            width=asset.width,
            height=asset.height,
        )
        apply_semantic_payload(clip, payloads.get(asset.id))
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


def _drop_reencoded_sources(candidates: list, *, config: Config) -> list:
    """Drop messaging re-encodes: small, and with no camera EXIF to vouch for them."""
    from immich_memories.analysis.source_quality import is_usable_source

    floor = config.analysis.min_source_short_side
    if floor <= 0:
        return candidates

    kept = [
        c
        for c in candidates
        if is_usable_source(
            width=c.clip.width or c.clip.asset.width or 0,
            height=c.clip.height or c.clip.asset.height or 0,
            has_camera_exif=_has_camera_exif(c.clip.asset),
            min_short_side=floor,
        )
    ]
    if len(kept) < len(candidates):
        logger.info(
            "Source quality: dropped %d clips under %dp with no camera EXIF",
            len(candidates) - len(kept),
            floor,
        )
    return kept


def _has_camera_exif(asset) -> bool:
    exif = getattr(asset, "exif_info", None)
    return bool(exif and (exif.make or exif.model))


def _name_after_recipe(
    output_path: Path,
    *,
    selected_clips: list,
    clip_segments: dict,
    memory_type: str | None,
    date_range,
    target_duration: float,
) -> Path:
    """Name the output after its recipe so an identical rerun replaces it.

    The name can only be finalised here: the CLI builds it before analysis, and
    the clips that define the edit are not known until selection has run.
    """
    from immich_memories.filename_builder import apply_recipe_hash, recipe_hash

    clips = []
    for clip in selected_clips:
        asset_id = clip.asset.id
        start, end = clip_segments.get(asset_id, (0.0, 0.0))
        clips.append((asset_id, start, end))

    digest = recipe_hash(
        memory_type=memory_type,
        date_start=date_range.start.date() if date_range else None,
        date_end=date_range.end.date() if date_range else None,
        target_duration=target_duration,
        clips=clips,
    )
    return apply_recipe_hash(output_path, digest)


def _apply_subject_policy(
    candidates: list,
    *,
    config: Config,
    content_budget_seconds: float,
    photo_assets: list | None = None,
) -> list:
    """Prefer clips of people, and ration animals and objects by share of runtime."""
    if not config.analysis.subject_policy_enabled:
        return candidates

    from immich_memories.analysis.subject_policy import filter_candidates_by_subject

    return filter_candidates_by_subject(
        candidates,
        animal_ratio=config.analysis.max_animal_ratio,
        object_ratio=config.analysis.max_object_ratio,
        content_budget_seconds=content_budget_seconds,
        photo_asset_ids={a.id for a in photo_assets or []},
    )


def _drop_photos_already_shown_as_motion(
    photo_assets: list,
    analyzed_videos: list,
    *,
    config: Config,
    client: SyncImmichClient,
    thumbnail_cache=None,
) -> list:
    """Remove photos a selected clip from the same moment already shows.

    Runs before scoring so the removed photos never reach the LLM.
    """
    from immich_memories.photos.moment_suppression import filter_photos_covered_by_motion

    motion_clips = [c.clip for c in analyzed_videos if getattr(c, "clip", None) is not None]
    if not motion_clips:
        return photo_assets

    return filter_photos_covered_by_motion(
        photo_assets,
        motion_clips,
        config=config.photos,
        thumbnail_cache=thumbnail_cache,
        thumbnail_fn=client.get_asset_thumbnail,
    )


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
