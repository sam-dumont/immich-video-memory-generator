"""Pipeline orchestration for the generate command.

Bridges CLI to SmartPipeline + generate_memory: runs the editorial route over
the assets the CLI fetched, then generates the final video from its cut.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning
from immich_memories.cli._editorial_context import (
    build_editorial_context,
    narrow_to_special_event,
)
from immich_memories.cli._helpers import console, print_error, print_success, print_warning
from immich_memories.cli._run_inputs import ResolvedRunInputs
from immich_memories.cli._run_summary import render_run_summary
from immich_memories.cli._run_timeline import configure_timeline, final_timeline
from immich_memories.operations.run_index import run_id_for_attempt
from immich_memories.operations.storyboard import read_storyboard
from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rich.progress import TaskID

    from immich_memories.analysis.editorial_planner import EditorialSelection
    from immich_memories.analysis.smart_pipeline import PipelineResult
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config
    from immich_memories.processing.output_canvas import OutputCanvas
    from immich_memories.processing.timeline_budget import TimelinePlan


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


def _configure_output_canvas(
    *,
    clips: list,
    photo_assets: list | None,
    config: Config,
    output_resolution: str | None,
    output_orientation: str | None,
) -> OutputCanvas:
    """Resolve the one pixel canvas this run renders to."""
    from immich_memories.processing.output_canvas import resolve_output_canvas

    planning_sources = [*clips, *(photo_assets or [])]
    return resolve_output_canvas(
        resolution=output_resolution,
        orientation=output_orientation,
        configured_resolution=config.output.resolution_tuple,
        clips=planning_sources,
    )


def _stops_before_rendering(*, dry_run: bool, no_render: bool) -> bool:
    """Whether this run ends at the plan instead of producing a file.

    Dry-run normally returns before selection. No-render reaches this boundary
    after the same story-first selection that a rendered memory uses.
    """
    return dry_run or no_render


def _finish_without_rendering(
    *,
    pipeline_result: PipelineResult,
    timeline_plan: TimelinePlan,
    assets: list,
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

    Selection has completed through the production story-first route; only the
    encode is missing.
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
        live_photo_candidates=sum(1 for photo in (photo_assets or []) if photo.live_photo_video_id),
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


def _finish_preparation(
    *,
    context,
    assets,
    photos,
    output_canvas,
    output_path,
    config,
    music,
    no_music,
    should_upload,
    album_name,
) -> tuple[Path, bool, str | None]:
    """Describe discovered inputs without making a different, approximate selection."""
    import click

    from immich_memories.cli._generation_preview import music_policy

    store = config.editorial.resolve_annotation_database(config.cache.cache_path)
    click.echo("Dry-run preparation (selection was not run; no video will be created)")
    click.echo(f"Memory: {context.product}")
    click.echo(f"Date range: {context.label}")
    click.echo(f"Candidates: {len(assets)} video, {len(photos)} photo")
    click.echo(f"Target duration: {context.target_seconds:.1f}s")
    readiness = (
        "store available; coverage checked at selection"
        if store.is_file()
        else "preparation required"
    )
    click.echo(f"Annotations: {readiness}")
    click.echo("Selection: pending (use --no-render to run story-first selection)")
    click.echo(
        f"Canvas: {output_canvas.width}x{output_canvas.height} ({output_canvas.orientation})"
    )
    click.echo(f"Music: {music_policy(config=config, music=music, no_music=no_music)}")
    click.echo(f"Output (planned): {output_path}")
    click.echo(f"Upload: {'planned' if should_upload else 'disabled'}")
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


class _SourceProgressReporter:
    """A source stage owns the whole bar: counted when it reports numbers, a spinner when not."""

    def __init__(self, progress: ProgressDisplay, task: TaskID) -> None:
        self._progress = progress
        self._task = task
        self._mode: str | tuple | None = None

    def __call__(self, status: dict) -> None:
        if status.get("indeterminate"):
            self._unbounded_stage(status)
            return
        if "total_items" in status:
            self._counted_stage(status)
            return
        pct = status.get("overall_progress", 0)
        phase_name = status.get("current_phase", "")
        self._progress.update(
            self._task,
            completed=int(pct * 20),
            description=f"Analyzing: {phase_name}",
        )

    def _unbounded_stage(self, status: dict) -> None:
        if self._mode != "unbounded":
            self._progress.reset(self._task, total=None)
            self._mode = "unbounded"
        self._progress.update(self._task, description=status["phase_label"])
        if status.get("status") == "complete":
            self._progress.reset(self._task, total=100)

    def _counted_stage(self, status: dict) -> None:
        """Reset on a new stage even when it has the same number of items."""
        total = int(status["total_items"])
        identity = status.get("stage_identity", (status.get("current_phase"), total))
        if self._mode != identity:
            self._progress.reset(self._task, total=total)
            self._mode = identity
        description = status["phase_label"]
        if remaining := status.get("remaining_label"):
            description += f" · {remaining}"
        self._progress.update(
            self._task, completed=int(status["current_index"]), description=description
        )


def run_pipeline_and_generate(
    *,
    assets: list,
    photo_assets: list | None = None,
    include_photos: bool = False,
    use_live_photos: bool = True,
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
    date_ranges: tuple[DateRange, ...] | list[DateRange] | None = None,
    upload_to_immich: bool,
    album: str | None,
    memory_preset_params: dict | None = None,
    source: str = "manual",
    memory_key: str | None = None,
    memory_category: str | None = None,
    automation_attempt_id: str | None = None,
    dry_run: bool = False,
    no_render: bool = False,
    accept_any_provenance: bool = False,
    owner_required_asset_ids: tuple[str, ...] = (),
    owner_excluded_asset_ids: tuple[str, ...] = (),
) -> tuple[Path, bool, str | None]:
    """Run smart pipeline analysis + video generation.

    Returns (result_path, should_upload, album_name).
    """
    from immich_memories.analysis.editorial_runtime import build_smart_pipeline
    from immich_memories.analysis.smart_pipeline import PipelineConfig
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.generate import GenerationParams, assets_to_clips, generate_memory
    from immich_memories.operations.phases import OperationalPhase
    from immich_memories.tracking.models import normalize_memory_people

    assets, photo_assets = narrow_to_special_event(
        memory_type=memory_type,
        assets=assets,
        photo_assets=photo_assets,
        memory_preset_params=memory_preset_params,
    )

    resolved = ResolvedRunInputs.from_arguments(
        include_photos=include_photos,
        photo_assets=photo_assets,
        dry_run=dry_run,
        automation_attempt_id=automation_attempt_id,
        upload_to_immich=upload_to_immich,
        config=config,
        person_names=person_names,
        music=music,
        memory_preset_params=memory_preset_params,
    )

    clips = assets_to_clips(assets)
    if not assets and not resolved.has_photos:
        print_error("No usable content (no video clips or photos)")
        sys.exit(1)

    duration = _resolve_requested_duration(
        duration,
        memory_type=memory_type,
        clips=clips,
        photos=resolved.photo_assets,
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
        resolved.attempt_id,
        progress,
        task,
    )
    phases.emit(OperationalPhase.DISCOVERY, len(clips), len(clips), "Discovery complete")
    phases.emit(OperationalPhase.DOWNLOAD, 0, len(clips), "Preparing source downloads")

    pipeline_config = PipelineConfig(hdr_only=False)
    output_canvas = _configure_output_canvas(
        clips=clips,
        photo_assets=resolved.photo_assets,
        config=config,
        output_resolution=output_resolution,
        output_orientation=output_orientation,
    )
    timeline_plan, planning_titles = configure_timeline(
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

    editorial_context = build_editorial_context(
        resolved=resolved,
        config=config,
        memory_type=memory_type,
        memory_key=memory_key,
        output_stem=output_path.stem,
        assets=assets,
        date_range=date_range,
        date_ranges=date_ranges,
        duration=duration,
        transition=transition,
        title_override=title_override,
        person_names=person_names,
        accept_any_provenance=accept_any_provenance,
        owner_required_asset_ids=owner_required_asset_ids,
        owner_excluded_asset_ids=owner_excluded_asset_ids,
    )
    if dry_run:
        return _finish_preparation(
            context=editorial_context,
            assets=assets,
            photos=(resolved.photo_assets or []) if include_photos else [],
            output_canvas=output_canvas,
            output_path=output_path,
            config=config,
            music=music,
            no_music=no_music,
            should_upload=resolved.should_upload,
            album_name=album or config.upload.album_name,
        )

    thumbnail_cache = ThumbnailCache(
        cache_dir=config.cache.cache_path / "thumbnails",
        max_size_mb=config.cache.thumbnail_cache_max_size_mb,
    )
    thumbnail_cache.begin_run()
    pipeline = build_smart_pipeline(
        client=client,
        thumbnail_cache=thumbnail_cache,
        config=pipeline_config,
        app_config=config,
        editorial_context=editorial_context,
        dry_run=False,
    )

    phases.emit(
        OperationalPhase.SELECTION,
        0,
        len(assets) + len(photo_assets or ()),
        "Preparing canonical editorial evidence",
    )
    source_photos = (photo_assets or []) if include_photos else []
    all_candidates, pipeline_result = pipeline.run_editorial_source(
        [*assets, *source_photos],
        progress_callback=_SourceProgressReporter(progress, task),
        include_live_photos=use_live_photos and config.analysis.include_live_photos,
    )
    _analysis_time = _time.monotonic() - _pipeline_start
    selected_clips = pipeline_result.selected_clips
    clip_segments = pipeline_result.clip_segments

    if not selected_clips:
        print_error("Pipeline selected no clips")
        sys.exit(1)

    timing_binding = pipeline_result.stats.get("editorial_render_timing")
    timeline_plan = final_timeline(
        timeline_plan,
        timing_binding=timing_binding,
        selected_clips=selected_clips,
        clip_segments=clip_segments,
        planning_titles=planning_titles,
        memory_type=memory_type,
        transition=transition,
        config=config,
    )

    output_path = _name_after_recipe(
        output_path,
        selected_clips=selected_clips,
        clip_segments=clip_segments,
        editorial_selections=pipeline_result.editorial_selections,
        memory_type=memory_type,
        date_range=date_range,
        target_duration=timeline_plan.target_duration,
    )

    print_success(f"Selected {len(selected_clips)} clips for final video")
    duration_realization = pipeline_result.stats.get("editorial_duration_realization")
    if duration_warning := editorial_duration_warning(duration_realization):
        print_warning(duration_warning)

    should_upload = resolved.should_upload
    album_name = album or config.upload.album_name
    person_name = resolved.person_name

    if _stops_before_rendering(dry_run=dry_run, no_render=no_render):
        return _finish_without_rendering(
            pipeline_result=pipeline_result,
            timeline_plan=timeline_plan,
            assets=assets,
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
        music_path=resolved.music_path,
        music_volume=music_volume,
        no_music=no_music,
        upload_enabled=should_upload,
        upload_album=album_name,
        clip_segments=clip_segments,
        editorial_selections=pipeline_result.editorial_selections,
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
        editorial_render_timing=timing_binding,
        editorial_duration_realization=duration_realization,
        editorial_attempt_dir=_attempt_dir_of(pipeline_result),
        progress_callback=gen_progress,
        phase_callback=generation_phase,
        completed_operational_phase=OperationalPhase.SELECTION,
        memory_preset_params=resolved.preset_params,
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

    # The live area is done: stop it before the block prints, or its redraw tears
    # the summary into fragments and repeats the last task line (#846).
    progress.stop()
    attempt_dir = _attempt_dir_of(pipeline_result)
    console.print(
        render_run_summary(
            total_seconds=_total_time,
            analysis_seconds=_analysis_time,
            generation_seconds=_gen_time,
            eligible=len(all_candidates),
            planned=len(selected_clips),
            counters=llm_metrics.active(),
            preparation_tier=config.editorial.preparation.tier,
            storyboard=read_storyboard(attempt_dir) if attempt_dir else None,
            run_id=run_id_for_attempt(attempt_dir) if attempt_dir else None,
        ),
        highlight=False,
        soft_wrap=True,
    )

    _send_notification(config, memory_type, "completed", _total_time, str(result_path))

    return result_path, should_upload, album_name


def _attempt_dir_of(pipeline_result: Any) -> Path | None:
    recorded = pipeline_result.stats.get("editorial_attempt_directory")
    return Path(recorded) if recorded else None


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


def _name_after_recipe(
    output_path: Path,
    *,
    selected_clips: list,
    clip_segments: dict,
    editorial_selections: tuple[EditorialSelection, ...] = (),
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
    rendering = tuple(
        (selection.asset_id, selection.render_mode, selection.render_frame_seconds)
        for selection in editorial_selections
    )

    digest = recipe_hash(
        memory_type=memory_type,
        date_start=date_range.start.date() if date_range else None,
        date_end=date_range.end.date() if date_range else None,
        target_duration=target_duration,
        clips=clips,
        extras={"editorial_rendering": rendering} if rendering else None,
    )
    return apply_recipe_hash(output_path, digest)
