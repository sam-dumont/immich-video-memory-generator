"""Step 4: Preview & Export page."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import run, ui

from immich_memories.security import sanitize_error_message, sanitize_filename
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def render_step4() -> None:
    """Render Step 4: Preview & Export."""
    state = get_app_state()

    # Get selected clips
    selected_clips = state.get_selected_clips()

    if not selected_clips:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("No clips selected. Go back to select clips.").classes("text-yellow-700")

        def go_back():
            state.step = 2
            ui.navigate.to("/step2")

        ui.button("Back to Clip Review", on_click=go_back, icon="arrow_back")
        return

    # Calculate total duration
    total_duration = sum(
        end - start
        for clip in selected_clips
        for start, end in [state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5))]
    )

    options = state.generation_options

    # ========================================================================
    # Summary
    # ========================================================================
    ui.label("Summary").classes("text-xl font-semibold mb-4")

    with ui.row().classes("w-full gap-8 mb-4"):
        with ui.column().classes("items-center"):
            ui.label("Clips").classes("text-sm text-gray-500")
            ui.label(str(len(selected_clips))).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Duration").classes("text-sm text-gray-500")
            ui.label(format_duration(total_duration)).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Format").classes("text-sm text-gray-500")
            ui.label(options.get("format", "MP4")).classes("text-2xl font-bold")

    ui.separator().classes("my-4")

    # ========================================================================
    # Output Settings
    # ========================================================================
    ui.label("Output").classes("text-xl font-semibold")

    # Output directory
    output_dir = Path.home() / "Videos" / "Memories"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate default filename
    date_range = state.date_range
    person = state.selected_person
    person_name = person.name if person else "everyone"

    if date_range and hasattr(date_range, "is_calendar_year") and date_range.is_calendar_year:
        date_slug = str(date_range.start.year)
    elif date_range:
        date_slug = f"{date_range.start.strftime('%Y%m%d')}-{date_range.end.strftime('%Y%m%d')}"
    else:
        date_slug = "memories"

    default_filename = f"{person_name}_{date_slug}_memories.mp4"

    # Filename input
    filename_input = ui.input(
        "Output filename",
        value=default_filename,
    ).classes("w-full max-w-md")

    ui.label(f"Will be saved to: {output_dir / default_filename}").classes(
        "text-sm text-gray-500"
    ).bind_text_from(filename_input, "value", lambda v: f"Will be saved to: {output_dir / v}")

    ui.separator().classes("my-6")

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
            status_label = ui.label("Starting...").classes("text-sm")
            run_id_label = ui.label("").classes("text-sm text-gray-500")

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

            # Initialize run tracking
            run_id = generate_run_id()
            run_tracker = RunTracker(run_id)
            run_id_label.set_text(f"Run ID: {run_id}")

            # Create versioned output directory
            person_slug = sanitize_filename(
                person_name.lower().replace(" ", "_") if person_name else "all"
            )
            run_output_dir = output_dir / f"{person_slug}_{date_slug}_{run_id}"
            run_output_dir.mkdir(parents=True, exist_ok=True)
            output_filename = sanitize_filename(filename_input.value)
            output_path = run_output_dir / output_filename

            # Start run tracking
            run_tracker.start_run(
                person_name=person.name if person else None,
                person_id=person.id if person else None,
                date_range=date_range,
                target_duration_minutes=int(total_duration / 60) if total_duration > 0 else 10,
            )

            # Initialize client and cache
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

                # Download video (blocking I/O → run in thread)
                video_path = await run.io_bound(video_cache.download_or_get, client, clip.asset)
                if not video_path or not video_path.exists():
                    logger.warning(f"Failed to download {clip.asset.id}, skipping")
                    continue

                # Get segment times
                start_time, end_time = state.clip_segments.get(
                    clip.asset.id, (0.0, clip.duration_seconds or 5.0)
                )
                segment_duration = end_time - start_time

                # Extract segment (blocking FFmpeg → run in thread)
                # Stream copy is fine here — assembly re-encodes with proper fps/timebase normalization
                status_label.set_text(f"Extracting segment: {clip_name}")
                try:
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
                    logger.warning(f"Failed to extract segment from {clip.asset.id}: {e}")
                    continue

                # run.io_bound() already yields to the event loop

            run_tracker.complete_phase(items_processed=len(assembly_clips))

            if not assembly_clips:
                ui.notify("No clips could be processed!", type="negative")
                run_tracker.fail_run("No clips could be processed", errors_count=1)
                return

            # Phase 2: Assemble video
            run_tracker.start_phase("assembly", len(assembly_clips))
            status_label.set_text("Assembling final video...")
            progress_bar.value = 0.7

            # Map UI options to AssemblySettings
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

            # Create title screen settings if enabled
            title_screen_settings = None
            if config.title_screens.enabled:
                from immich_memories.processing.assembly import TitleScreenSettings

                title_start_date = date_range.start if date_range else None
                title_end_date = date_range.end if date_range else None

                title_person_name = None
                if person and person.name:
                    if config.title_screens.use_first_name_only:
                        title_person_name = person.name.split()[0]
                    else:
                        title_person_name = person.name

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
                    show_month_dividers=config.title_screens.show_month_dividers,
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

            # Run assembly in thread (blocking FFmpeg operations)
            result_path = await run.io_bound(
                assembler.assemble_with_titles,
                assembly_clips,
                output_path,
            )

            run_tracker.complete_phase(items_processed=len(assembly_clips))

            # Phase 3: Add music if requested
            music_source = gen_options.get("music_source", "None")

            if music_source == "AI Generated (MusicGen)":
                run_tracker.start_phase("music", 1)
                status_label.set_text("Generating AI music...")
                progress_bar.value = 0.85

                try:
                    from immich_memories.audio.mixer import (
                        DuckingConfig,
                        MixConfig,
                        mix_audio_with_ducking,
                    )
                    from immich_memories.audio.music_generator import (
                        MusicGenClientConfig,
                        VideoTimeline,
                        generate_music_for_video,
                    )

                    # Build timeline
                    clip_data: list[tuple[float, str, int | None]] = [
                        (
                            clip.duration,
                            clip.llm_emotion or "calm",
                            int(clip.date.split("-")[1]) if clip.date else None,
                        )
                        for clip in assembly_clips
                    ]
                    timeline = VideoTimeline.from_clips(
                        clips=clip_data,
                        title_duration=(
                            config.title_screens.title_duration
                            if config.title_screens.enabled
                            else 0
                        ),
                        ending_duration=(
                            config.title_screens.ending_duration
                            if config.title_screens.enabled
                            else 0
                        ),
                    )

                    musicgen_config = MusicGenClientConfig.from_app_config(config.musicgen)
                    musicgen_config.num_versions = gen_options.get("musicgen_versions", 3)

                    music_output_dir = run_output_dir / "music"
                    music_output_dir.mkdir(exist_ok=True)

                    def music_progress(version_idx, status, progress, detail):
                        pct = 0.85 + (version_idx / musicgen_config.num_versions) * 0.1
                        progress_bar.value = pct
                        status_label.set_text(f"Generating music v{version_idx + 1}: {status}")

                    music_result = await generate_music_for_video(
                        timeline=timeline,
                        output_dir=music_output_dir,
                        config=musicgen_config,
                        progress_callback=music_progress,
                    )

                    music_result.selected_version = 0
                    selected_music = music_result.selected

                    if selected_music:
                        status_label.set_text("Mixing audio...")
                        progress_bar.value = 0.96

                        music_volume = gen_options.get("music_volume", 0.3)
                        final_path = result_path.with_suffix(".with_music.mp4")
                        mix_config = MixConfig(
                            ducking=DuckingConfig(
                                music_volume_db=-20 + (music_volume * 20),
                            ),
                        )

                        await run.io_bound(
                            mix_audio_with_ducking,
                            video_path=result_path,
                            music_path=selected_music.full_mix,
                            output_path=final_path,
                            config=mix_config,
                        )

                        result_path.unlink()
                        final_path.rename(result_path)
                        music_result.cleanup_unselected()

                    run_tracker.complete_phase(items_processed=1)

                except Exception as e:
                    logger.warning(f"Music generation failed: {e}")
                    ui.notify(
                        f"Music generation failed: {sanitize_error_message(str(e))}. Video saved without music.",
                        type="warning",
                    )
                    run_tracker.complete_phase(items_processed=0)

            elif music_source == "Upload file" and gen_options.get("music_file"):
                run_tracker.start_phase("music", 1)
                status_label.set_text("Adding music...")
                progress_bar.value = 0.9

                try:
                    import tempfile

                    from immich_memories.audio.mixer import (
                        DuckingConfig,
                        MixConfig,
                        mix_audio_with_ducking,
                    )

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                        tmp.write(gen_options["music_file"])
                        tmp_music_path = Path(tmp.name)

                    music_volume = gen_options.get("music_volume", 0.3)
                    final_path = result_path.with_suffix(".with_music.mp4")
                    mix_config = MixConfig(
                        ducking=DuckingConfig(
                            music_volume_db=-20 + (music_volume * 20),
                        ),
                    )

                    await run.io_bound(
                        mix_audio_with_ducking,
                        video_path=result_path,
                        music_path=tmp_music_path,
                        output_path=final_path,
                        config=mix_config,
                    )

                    tmp_music_path.unlink()
                    result_path.unlink()
                    final_path.rename(result_path)

                    run_tracker.complete_phase(items_processed=1)

                except Exception as e:
                    logger.warning(f"Music mixing failed: {e}")
                    ui.notify(
                        f"Music mixing failed: {sanitize_error_message(str(e))}. Video saved without music.",
                        type="warning",
                    )
                    run_tracker.complete_phase(items_processed=0)

            progress_bar.value = 1.0
            status_label.set_text("Complete!")
            state.output_path = result_path

            # Complete run
            run_tracker.complete_run(
                output_path=result_path,
                clips_analyzed=total_clips,
                clips_selected=len(assembly_clips),
            )

            # Cleanup temporary segment files
            for assembled_clip in assembly_clips:
                try:
                    if assembled_clip.path.exists() and "tmp" in str(assembled_clip.path).lower():
                        assembled_clip.path.unlink()
                except Exception:
                    pass

            ui.notify("Video generated successfully!", type="positive")

            # Show output
            output_container.clear()
            with output_container:
                ui.separator().classes("my-4")
                ui.label("Output").classes("text-xl font-semibold")
                ui.label(f"Saved to: {result_path}").classes("text-sm text-gray-500")

                # Video player
                if result_path.exists():
                    video_url = nicegui_app.add_media_file(local_file=result_path)
                    ui.video(video_url).classes("w-full max-w-2xl").style(
                        "max-height: 60vh; object-fit: contain"
                    )

        except Exception as e:
            logger.exception("Video generation failed")
            safe_msg = sanitize_error_message(str(e))
            ui.notify(f"Generation failed: {safe_msg}", type="negative")
            progress_container.clear()
            with progress_container:
                ui.label(f"Generation failed: {safe_msg}").classes("text-red-600")

    ui.button(
        "Generate Video",
        on_click=generate_video,
        icon="movie",
    ).props("color=primary").classes("w-full")

    # Show existing output if available
    if state.output_path and Path(state.output_path).exists():
        ui.separator().classes("my-4")
        ui.label("Output").classes("text-xl font-semibold")
        ui.label(f"Saved to: {state.output_path}").classes("text-sm text-gray-500")

        video_url = nicegui_app.add_media_file(local_file=Path(state.output_path))
        ui.video(video_url).classes("w-full max-w-2xl").style(
            "max-height: 60vh; object-fit: contain"
        )

    ui.separator().classes("my-6")

    # ========================================================================
    # Navigation
    # ========================================================================
    with ui.row().classes("w-full gap-4"):

        def go_back():
            state.step = 3
            ui.navigate.to("/step3")

        def start_new():
            # Reset session state
            state.reset_clips()
            state.step = 1
            ui.navigate.to("/")

        ui.button("Back to Generation Options", on_click=go_back, icon="arrow_back").props(
            "outline"
        )
        ui.button("Start New Project", on_click=start_new, icon="refresh").props("outline")
