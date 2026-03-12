"""Step 4: Preview & Export page with themed components."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import run, ui

from immich_memories.security import sanitize_error_message, sanitize_filename
from immich_memories.ui.components import (
    im_button,
    im_card,
    im_info_card,
    im_section_header,
    im_separator,
    im_stat_card,
)
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def _download_and_merge_burst(client, video_cache, clip, output_dir: Path) -> Path | None:
    """Download all live photo burst videos and merge into one file."""
    import subprocess

    from immich_memories.processing.live_photo_merger import (
        build_merge_command,
        filter_valid_clips,
    )

    burst_ids = clip.live_burst_video_ids
    trim_points = clip.live_burst_trim_points

    # Check for cached merged file first
    merge_dir = output_dir / ".live_merges"
    merge_dir.mkdir(parents=True, exist_ok=True)
    merged_path = merge_dir / f"{clip.asset.id}_merged.mp4"
    if merged_path.exists() and merged_path.stat().st_size > 1000:
        return merged_path

    # Download each video component directly by ID
    cache_dir = video_cache.cache_dir
    clip_paths: list[Path] = []
    for vid in burst_ids:
        subdir = vid[:2] if len(vid) >= 2 else "00"
        dest = cache_dir / subdir / f"{vid}.MOV"
        if dest.exists() and dest.stat().st_size > 0:
            clip_paths.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            client.download_asset(vid, dest)
            if dest.exists() and dest.stat().st_size > 0:
                clip_paths.append(dest)
            else:
                logger.warning(f"Burst video empty: {vid}")
        except Exception:
            logger.warning(f"Failed to download burst video {vid}", exc_info=True)

    if not clip_paths or len(clip_paths) != len(trim_points):
        return video_cache.download_or_get(client, clip.asset)

    cmd = build_merge_command(clip_paths, trim_points, merged_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and merged_path.exists():
            logger.info(f"Merged {len(clip_paths)} live photos into {merged_path}")
            return merged_path
        logger.warning(f"Live photo merge failed: {result.stderr[-200:]}")

        # Retry: filter out clips with no valid video frames and re-merge
        valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)
        if valid_paths and len(valid_paths) < len(clip_paths):
            logger.info(f"Retrying merge with {len(valid_paths)}/{len(clip_paths)} valid clips")
            if merged_path.exists():
                merged_path.unlink()
            retry_cmd = build_merge_command(valid_paths, valid_trims, merged_path)
            retry_result = subprocess.run(retry_cmd, capture_output=True, text=True, timeout=120)
            if retry_result.returncode == 0 and merged_path.exists():
                logger.info(f"Retry merge succeeded with {len(valid_paths)} clips")
                return merged_path
            logger.warning(f"Retry merge also failed: {retry_result.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"Live photo merge error: {e}")

    return video_cache.download_or_get(client, clip.asset)


def render_step4() -> None:
    """Render Step 4: Preview & Export."""
    state = get_app_state()

    selected_clips = state.get_selected_clips()

    if not selected_clips:
        im_info_card("No clips selected. Go back to select clips.", variant="warning")

        def go_back():
            state.step = 2
            ui.navigate.to("/step2")

        im_button("Back to Clip Review", variant="secondary", on_click=go_back, icon="arrow_back")
        return

    total_duration = sum(
        end - start
        for clip in selected_clips
        for start, end in [state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5))]
    )

    options = state.generation_options

    # ========================================================================
    # Summary
    # ========================================================================
    im_section_header("Summary", icon="summarize")

    with ui.element("div").classes("grid grid-cols-3 gap-4 mb-4"):
        im_stat_card("Clips", str(len(selected_clips)), icon="movie")
        im_stat_card("Duration", format_duration(total_duration), icon="timer")
        im_stat_card("Format", options.get("format", "MP4"), icon="video_file")

    im_separator()

    # ========================================================================
    # Output Settings
    # ========================================================================
    im_section_header("Output", icon="folder")

    output_dir = Path.home() / "Videos" / "Memories"
    output_dir.mkdir(parents=True, exist_ok=True)

    from immich_memories.ui.filename_builder import (
        build_output_filename,
        build_title_person_name,
        get_divider_mode,
    )

    date_range = state.date_range
    person = state.selected_person

    default_filename = build_output_filename(
        memory_type=state.memory_type,
        preset_params=state.memory_preset_params,
        person_name=person.name if person else None,
        date_start=date_range.start if date_range else None,
        date_end=date_range.end if date_range else None,
    )

    with im_card() as card:
        card.classes("p-5")
        filename_input = ui.input("Output filename", value=default_filename).classes(
            "w-full max-w-md"
        )
        ui.label(f"Will be saved to: {output_dir / default_filename}").classes("text-sm").style(
            "color: var(--im-text-secondary)"
        ).bind_text_from(filename_input, "value", lambda v: f"Will be saved to: {output_dir / v}")

    im_separator()

    # ========================================================================
    # Generate Button and Progress
    # ========================================================================
    progress_container = ui.column().classes("w-full")
    output_container = ui.column().classes("w-full")

    async def generate_video():
        """Generate the video compilation."""
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
            from immich_memories.processing.assembly import (
                AssemblyClip,
                AssemblySettings,
                TransitionType,
                VideoAssembler,
            )
            from immich_memories.processing.clips import extract_clip
            from immich_memories.tracking import RunTracker, generate_run_id

            config = get_config()

            run_id = generate_run_id()
            run_tracker = RunTracker(run_id)
            run_id_label.set_text(f"Run ID: {run_id}")

            # Use filename (without .mp4) as directory slug
            dir_slug = default_filename.removesuffix(".mp4")
            run_output_dir = output_dir / f"{dir_slug}_{run_id}"
            run_output_dir.mkdir(parents=True, exist_ok=True)
            output_filename = sanitize_filename(filename_input.value)
            output_path = run_output_dir / output_filename

            run_tracker.start_run(
                person_name=person.name if person else None,
                person_id=person.id if person else None,
                date_range=date_range,
                target_duration_minutes=int(total_duration / 60) if total_duration > 0 else 10,
            )

            client = SyncImmichClient(
                base_url=state.immich_url,
                api_key=state.immich_api_key,
            )
            video_cache = VideoDownloadCache(
                cache_dir=config.cache.video_cache_path,
                max_size_gb=config.cache.video_cache_max_size_gb,
                max_age_days=config.cache.video_cache_max_age_days,
            )

            assembly_clips: list[AssemblyClip] = []
            total_clips = len(selected_clips)

            # Phase 1: Download and extract segments
            run_tracker.start_phase("clip_extraction", total_clips)

            for i, clip in enumerate(selected_clips):
                progress_frac = (i / total_clips) * 0.7
                clip_name = clip.asset.original_file_name or clip.asset.id[:8]
                status_label.set_text(f"Downloading: {clip_name}")
                progress_bar.value = progress_frac

                try:
                    # Live Photo burst: download all videos and merge
                    if clip.live_burst_video_ids and clip.live_burst_trim_points:
                        video_path = await run.io_bound(
                            _download_and_merge_burst,
                            client,
                            video_cache,
                            clip,
                            output_dir,
                        )
                    else:
                        video_path = await run.io_bound(
                            video_cache.download_or_get,
                            client,
                            clip.asset,
                        )

                    if not video_path or not video_path.exists():
                        logger.warning(f"Failed to download {clip.asset.id}, skipping")
                        continue

                    start_time, end_time = state.clip_segments.get(
                        clip.asset.id, (0.0, clip.duration_seconds or 5.0)
                    )
                    segment_duration = end_time - start_time

                    status_label.set_text(f"Extracting segment: {clip_name}")
                    segment_path = await run.io_bound(
                        extract_clip,
                        video_path,
                        start_time=start_time,
                        end_time=end_time,
                    )
                    rotation_override = state.clip_rotations.get(clip.asset.id)

                    assembly_clips.append(
                        AssemblyClip(
                            path=segment_path,
                            duration=segment_duration,
                            date=clip.asset.file_created_at.strftime("%Y-%m-%d"),
                            asset_id=clip.asset.id,
                            rotation_override=rotation_override,
                            llm_emotion=clip.llm_emotion,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to process {clip.asset.id}: {e}")
                    continue

            run_tracker.complete_phase(items_processed=len(assembly_clips))

            if not assembly_clips:
                ui.notify("No clips could be processed!", type="negative")
                run_tracker.fail_run("No clips could be processed", errors_count=1)
                return

            # Phase 2: Assemble video
            run_tracker.start_phase("assembly", len(assembly_clips))
            status_label.set_text("Assembling final video...")
            progress_bar.value = 0.7

            gen_options = state.generation_options

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

            resolution_map = {
                "4K": (3840, 2160),
                "1080p": (1920, 1080),
                "720p": (1280, 720),
            }
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
                # Respect config toggle: if dividers disabled globally, force "none"
                if not config.title_screens.show_month_dividers:
                    divider_mode = "none"

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
                )

            settings = AssemblySettings(
                transition=transition_type,
                transition_duration=0.5,
                output_crf=config.output.crf,
                auto_resolution=auto_resolution,
                target_resolution=target_resolution,
                title_screens=title_screen_settings,
                debug_preserve_intermediates=gen_options.get("keep_intermediates", False),
            )
            assembler = VideoAssembler(settings)

            result_path = await run.io_bound(
                assembler.assemble_with_titles,
                assembly_clips,
                output_path,
            )

            run_tracker.complete_phase(items_processed=len(assembly_clips))

            # Phase 3: Add music if requested
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
                    result_path,
                    gen_options,
                    run_tracker,
                    progress_bar,
                    status_label,
                )

            progress_bar.value = 1.0
            status_label.set_text("Complete!")
            state.output_path = result_path

            run_tracker.complete_run(
                output_path=result_path,
                clips_analyzed=total_clips,
                clips_selected=len(assembly_clips),
            )

            for assembled_clip in assembly_clips:
                try:
                    if assembled_clip.path.exists() and "tmp" in str(assembled_clip.path).lower():
                        assembled_clip.path.unlink()
                except Exception:
                    pass

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

        except Exception as e:
            logger.exception("Video generation failed")
            safe_msg = sanitize_error_message(str(e))
            ui.notify(f"Generation failed: {safe_msg}", type="negative")
            progress_container.clear()
            with progress_container:
                im_info_card(f"Generation failed: {safe_msg}", variant="error")

    im_button("Generate Video", variant="primary", on_click=generate_video, icon="movie").classes(
        "w-full"
    )

    # Show existing output
    if state.output_path and Path(state.output_path).exists():
        im_separator()
        im_section_header("Output", icon="check_circle")
        ui.label(f"Saved to: {state.output_path}").classes("text-sm").style(
            "color: var(--im-text-secondary)"
        )

        video_url = nicegui_app.add_media_file(local_file=Path(state.output_path))
        with ui.element("div").classes("rounded-xl overflow-hidden mt-4").style("background: #000"):
            ui.video(video_url).classes("w-full max-w-2xl").style(
                "max-height: 60vh; object-fit: contain"
            )

    im_separator()

    # ========================================================================
    # Navigation
    # ========================================================================
    with ui.row().classes("w-full gap-4"):

        def go_back():
            state.step = 3
            ui.navigate.to("/step3")

        def start_new():
            state.reset_clips()
            state.step = 1
            ui.navigate.to("/")

        im_button(
            "Back to Generation Options", variant="secondary", on_click=go_back, icon="arrow_back"
        )
        im_button("Start New Project", variant="secondary", on_click=start_new, icon="refresh")
