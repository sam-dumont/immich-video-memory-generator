"""Video generation logic for Step 4 export.

Extracted from step4_export.py to keep file length under 500 lines.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import run, ui

from immich_memories.security import sanitize_error_message
from immich_memories.ui.components import (
    im_info_card,
    im_section_header,
    im_separator,
)

logger = logging.getLogger(__name__)


async def _extract_clips(
    selected_clips, state, client, video_cache, output_dir, progress_bar, status_label
):
    """Phase 1: Download and extract clip segments."""
    from immich_memories.processing.assembly import AssemblyClip
    from immich_memories.processing.clips import extract_clip
    from immich_memories.ui.pages.step4_export import _download_and_merge_burst

    assembly_clips: list[AssemblyClip] = []
    total_clips = len(selected_clips)

    for i, clip in enumerate(selected_clips):
        progress_bar.value = (i / total_clips) * 0.7
        clip_name = clip.asset.original_file_name or clip.asset.id[:8]
        status_label.set_text(f"Downloading: {clip_name}")

        try:
            if clip.live_burst_video_ids and clip.live_burst_trim_points:
                video_path = await run.io_bound(
                    _download_and_merge_burst, client, video_cache, clip, output_dir
                )
            else:
                video_path = await run.io_bound(video_cache.download_or_get, client, clip.asset)

            if not video_path or not video_path.exists():
                logger.warning(f"Failed to download {clip.asset.id}, skipping")
                continue

            start_time, end_time = state.clip_segments.get(
                clip.asset.id, (0.0, clip.duration_seconds or 5.0)
            )

            status_label.set_text(f"Extracting segment: {clip_name}")
            segment_path = await run.io_bound(
                extract_clip, video_path, start_time=start_time, end_time=end_time
            )

            exif = clip.asset.exif_info
            assembly_clips.append(
                AssemblyClip(
                    path=segment_path,
                    duration=end_time - start_time,
                    date=clip.asset.file_created_at.strftime("%Y-%m-%d"),
                    asset_id=clip.asset.id,
                    rotation_override=state.clip_rotations.get(clip.asset.id),
                    llm_emotion=clip.llm_emotion,
                    latitude=exif.latitude if exif else None,
                    longitude=exif.longitude if exif else None,
                    location_name=_clip_location_name(exif),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to process {clip.asset.id}: {e}")
            continue

    return assembly_clips


async def _apply_music(
    state,
    config,
    result_path,
    assembly_clips,
    run_output_dir,
    run_tracker,
    progress_bar,
    status_label,
):
    """Phase 3: Apply music if requested."""
    gen_options = state.generation_options
    music_source = gen_options.get("music_source", "None")

    if music_source == "AI Generated":
        from immich_memories.ui.pages._step4_music import apply_ai_music

        await apply_ai_music(
            result_path,
            assembly_clips,
            gen_options,
            config,
            run_output_dir,
            run_tracker,
            progress_bar,
            status_label,
            memory_type=state.memory_type,
        )
    elif music_source == "Upload file" and gen_options.get("music_file"):
        from immich_memories.ui.pages._step4_music import apply_uploaded_music

        await apply_uploaded_music(
            result_path, gen_options, run_tracker, progress_bar, status_label
        )


async def run_generation(
    state,
    selected_clips,
    total_duration: float,
    output_dir: Path,
    output_path: Path,
    filename_input,
    progress_container,
    output_container,
) -> None:
    """Execute the full video generation pipeline."""
    from immich_memories.security import sanitize_filename

    progress_container.clear()
    with progress_container:
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_bar.style("--q-linear-progress-color: var(--im-primary)")
        status_label = ui.label("Starting...").classes("text-sm").style("color: var(--im-text)")
        run_id_label = ui.label("").classes("text-sm").style("color: var(--im-text-secondary)")

    try:
        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.config import get_config
        from immich_memories.tracking import RunTracker, generate_run_id

        config = get_config()
        person = state.selected_person
        date_range = state.date_range

        run_id = generate_run_id()
        run_tracker = RunTracker(run_id)
        run_id_label.set_text(f"Run ID: {run_id}")

        dir_slug = filename_input.value.removesuffix(".mp4")
        run_output_dir = output_dir / f"{dir_slug}_{run_id}"
        run_output_dir.mkdir(parents=True, exist_ok=True)
        result_output_path = run_output_dir / sanitize_filename(filename_input.value)

        run_tracker.start_run(
            person_name=person.name if person else None,
            person_id=person.id if person else None,
            date_range=date_range,
            target_duration_minutes=int(total_duration / 60) if total_duration > 0 else 10,
        )

        client = SyncImmichClient(base_url=state.immich_url, api_key=state.immich_api_key)
        video_cache = VideoDownloadCache(
            cache_dir=config.cache.video_cache_path,
            max_size_gb=config.cache.video_cache_max_size_gb,
            max_age_days=config.cache.video_cache_max_age_days,
        )

        # Phase 1: Download and extract
        run_tracker.start_phase("clip_extraction", len(selected_clips))
        assembly_clips = await _extract_clips(
            selected_clips, state, client, video_cache, output_dir, progress_bar, status_label
        )
        run_tracker.complete_phase(items_processed=len(assembly_clips))

        if not assembly_clips:
            ui.notify("No clips could be processed!", type="negative")
            run_tracker.fail_run("No clips could be processed", errors_count=1)
            return

        # Phase 2: Assemble
        run_tracker.start_phase("assembly", len(assembly_clips))
        status_label.set_text("Assembling final video...")
        progress_bar.value = 0.7
        _settings, assembler = _build_assembly_settings(state, config, assembly_clips)
        result_path = await run.io_bound(
            assembler.assemble_with_titles, assembly_clips, result_output_path
        )
        run_tracker.complete_phase(items_processed=len(assembly_clips))

        # Phase 3: Music
        await _apply_music(
            state,
            config,
            result_path,
            assembly_clips,
            run_output_dir,
            run_tracker,
            progress_bar,
            status_label,
        )

        # Phase 4: Upload
        if state.upload_enabled:
            from immich_memories.ui.pages._step4_upload import upload_to_immich

            await upload_to_immich(result_path, state, progress_bar, status_label)

        progress_bar.value = 1.0
        status_label.set_text("Complete!")
        state.output_path = result_path
        run_tracker.complete_run(
            output_path=result_path,
            clips_analyzed=len(selected_clips),
            clips_selected=len(assembly_clips),
        )

        _cleanup_temp_clips(assembly_clips)
        _show_output(output_container, result_path)

    except Exception as e:
        logger.exception("Video generation failed")
        safe_msg = sanitize_error_message(str(e))
        ui.notify(f"Generation failed: {safe_msg}", type="negative")
        progress_container.clear()
        with progress_container:
            im_info_card(f"Generation failed: {safe_msg}", variant="error")


def _cleanup_temp_clips(assembly_clips) -> None:
    """Remove temporary intermediate clip files."""
    for clip in assembly_clips:
        try:
            if clip.path.exists() and "tmp" in str(clip.path).lower():
                clip.path.unlink()
        except Exception:
            pass


def _show_output(output_container, result_path: Path) -> None:
    """Display the generated video in the output container."""
    ui.notify("Video generated successfully!", type="positive")
    output_container.clear()
    with output_container:
        im_separator()
        im_section_header("Output", icon="check_circle")
        ui.label(f"Saved to: {result_path}").classes("text-sm").style(
            "color: var(--im-text-secondary)"
        )
        if result_path.exists():
            video_url = nicegui_app.add_media_file(local_file=result_path)
            with (
                ui.element("div")
                .classes("rounded-xl overflow-hidden mt-4")
                .style("background: #000")
            ):
                ui.video(video_url).classes("w-full max-w-2xl").style(
                    "max-height: 60vh; object-fit: contain"
                )


def _build_assembly_settings(state, config, assembly_clips):
    """Build AssemblySettings and VideoAssembler from state and config."""
    from immich_memories.processing.assembly import (
        AssemblySettings,
        TransitionType,
        VideoAssembler,
    )
    from immich_memories.ui.filename_builder import (
        build_title_person_name,
        get_divider_mode,
    )

    gen_options = state.generation_options
    person = state.selected_person
    date_range = state.date_range

    transition_map = {
        "Smart (mix of fades & cuts)": TransitionType.SMART,
        "Crossfade": TransitionType.CROSSFADE,
        "Cut": TransitionType.CUT,
        "None": TransitionType.NONE,
    }
    transition_type = transition_map.get(
        gen_options.get("transition", "Smart (mix of fades & cuts)"),
        TransitionType.SMART,
    )

    resolution_map = {"4K": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
    resolution_str = gen_options.get("resolution", "Auto (match clips)")
    auto_resolution = resolution_str == "Auto (match clips)"
    target_resolution = resolution_map.get(resolution_str) if not auto_resolution else None

    title_screen_settings = None
    if config.title_screens.enabled:
        from immich_memories.processing.assembly import TitleScreenSettings

        title_start_date = date_range.start if date_range else None
        title_end_date = date_range.end if date_range else None

        title_person_name = build_title_person_name(
            memory_type=state.memory_type,
            preset_params=state.memory_preset_params,
            person_name=person.name if person else None,
            use_first_name_only=config.title_screens.use_first_name_only,
        )

        divider_mode = get_divider_mode(
            memory_type=state.memory_type,
            date_start=date_range.start if date_range else None,
            date_end=date_range.end if date_range else None,
        )
        if not config.title_screens.show_month_dividers:
            divider_mode = "none"

        # Trip-specific title settings: map intro + location dividers
        trip_locations = None
        trip_title_text = None
        if state.memory_type == "trip":
            trip_locations = _extract_trip_locations(state, assembly_clips)
            trip_title_text = _generate_trip_title_text(state)

        title_screen_settings = TitleScreenSettings(
            enabled=True,
            person_name=title_person_name,
            start_date=title_start_date,
            end_date=title_end_date,
            locale=config.title_screens.locale,
            style_mode=config.title_screens.style_mode,
            title_duration=config.title_screens.title_duration,
            month_divider_duration=config.title_screens.month_divider_duration,
            ending_duration=config.title_screens.ending_duration,
            show_month_dividers=divider_mode == "month",
            divider_mode=divider_mode,
            month_divider_threshold=config.title_screens.month_divider_threshold,
            use_first_name_only=config.title_screens.use_first_name_only,
            memory_type=state.memory_type,
            trip_locations=trip_locations,
            trip_title_text=trip_title_text,
            home_lat=state.memory_preset_params.get("home_lat"),
            home_lon=state.memory_preset_params.get("home_lon"),
        )

        # Use LLM-generated title if available
        if state.title_suggestion_title:
            title_screen_settings.title_override = state.title_suggestion_title
            title_screen_settings.subtitle_override = state.title_suggestion_subtitle

    settings = AssemblySettings(
        transition=transition_type,
        transition_duration=0.5,
        output_crf=config.output.crf,
        auto_resolution=auto_resolution,
        target_resolution=target_resolution,
        title_screens=title_screen_settings,
        debug_preserve_intermediates=gen_options.get("keep_intermediates", False),
        privacy_mode=state.demo_mode,
    )
    return settings, VideoAssembler(settings)


def _clip_location_name(exif) -> str | None:
    """Extract a location name from EXIF data."""
    if not exif:
        return None
    city = exif.city
    country = exif.country
    if city and country:
        return f"{city}, {country}"
    return country or city


def _extract_trip_locations(state, assembly_clips) -> list[tuple[float, float]]:
    """Extract unique GPS locations from assembly clips for map pins."""
    seen: set[tuple[float, float]] = set()
    locations: list[tuple[float, float]] = []
    for clip in assembly_clips:
        if clip.latitude is not None and clip.longitude is not None:
            # Round to ~1km precision to deduplicate nearby points
            key = (round(clip.latitude, 2), round(clip.longitude, 2))
            if key not in seen:
                seen.add(key)
                locations.append((clip.latitude, clip.longitude))
    return locations


def _generate_trip_title_text(state) -> str | None:
    """Generate trip title text from state preset params."""
    from immich_memories.titles._trip_titles import generate_trip_title

    params = state.memory_preset_params
    location_name = params.get("location_name")
    trip_start = params.get("trip_start")
    trip_end = params.get("trip_end")

    if not location_name or not trip_start or not trip_end:
        return None

    return generate_trip_title(location_name, trip_start, trip_end)
