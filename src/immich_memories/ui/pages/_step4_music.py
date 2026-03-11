"""Music generation and mixing helpers for Step 4 export.

Extracted from step4_export.py for maintainability.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import run, ui

from immich_memories.security import sanitize_error_message

logger = logging.getLogger(__name__)


async def apply_ai_music(
    result_path: Path,
    assembly_clips: list,
    gen_options: dict,
    config: object,
    run_output_dir: Path,
    run_tracker: object,
    progress_bar: object,
    status_label: object,
) -> None:
    """Generate AI music with MusicGen and mix it into the video.

    If music was pre-generated in Step 3 (music preview), uses that
    instead of regenerating. Otherwise generates fresh.

    Args:
        result_path: Path to the assembled video file.
        assembly_clips: List of AssemblyClip objects.
        gen_options: Generation options dict from UI state.
        config: App config object.
        run_output_dir: Output directory for this run.
        run_tracker: RunTracker instance.
        progress_bar: NiceGUI progress bar element.
        status_label: NiceGUI status label element.
    """
    run_tracker.start_phase("music", 1)
    progress_bar.value = 0.85

    try:
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            mix_audio_with_ducking,
        )

        # Check if music was pre-generated in Step 3
        from immich_memories.ui.state import get_app_state

        state = get_app_state()
        music_result = state.music_preview_result

        if music_result and music_result.versions and music_result.selected is not None:
            status_label.set_text("Using pre-generated music...")
            logger.info("Using music pre-generated in Step 3")
            selected_music = music_result.selected
        else:
            # Generate music fresh (no preview was done)
            status_label.set_text("Generating AI music...")
            from immich_memories.audio.music_generator import (
                MusicGenClientConfig,
                VideoTimeline,
                generate_music_for_video,
            )

            clip_data: list[tuple[float, str, int | None]] = [
                (
                    clip.duration,
                    clip.llm_emotion or "calm",
                    clip.date.month
                    if hasattr(clip.date, "month")
                    else (int(clip.date.split("-")[1]) if clip.date else None),
                )
                for clip in assembly_clips
            ]
            timeline = VideoTimeline.from_clips(
                clips=clip_data,
                title_duration=(
                    config.title_screens.title_duration if config.title_screens.enabled else 0
                ),
                ending_duration=(
                    config.title_screens.ending_duration if config.title_screens.enabled else 0
                ),
            )

            musicgen_config = MusicGenClientConfig.from_app_config(config.musicgen)
            musicgen_config.num_versions = 1

            music_output_dir = run_output_dir / "music"
            music_output_dir.mkdir(exist_ok=True)

            def music_progress(version_idx, status, progress, detail):
                pct = 0.85 + (progress / 100.0) * 0.1
                progress_bar.value = min(pct, 0.95)
                status_label.set_text(f"Music: {status} ({int(progress)}%)")

            music_result = await generate_music_for_video(
                timeline=timeline,
                output_dir=music_output_dir,
                config=musicgen_config,
                progress_callback=music_progress,
                app_config=config,
            )

            music_result.selected_version = 0
            selected_music = music_result.selected

        if selected_music:
            status_label.set_text("Mixing audio...")
            progress_bar.value = 0.96

            music_volume = gen_options.get("music_volume", 0.5)
            final_path = result_path.with_suffix(".with_music.mp4")
            mix_config = MixConfig(
                ducking=DuckingConfig(
                    music_volume_db=-20 + (music_volume * 20),
                ),
            )

            # Use 4-stem ducking when stems are available (drums stay constant)
            stems = selected_music.stems
            if stems and stems.drums and stems.bass and stems.vocals and stems.other:
                from immich_memories.audio.mixer_helpers import (
                    mix_audio_with_4stem_ducking,
                )

                await run.io_bound(
                    mix_audio_with_4stem_ducking,
                    video_path=result_path,
                    drums_path=stems.drums,
                    bass_path=stems.bass,
                    vocals_path=stems.vocals,
                    other_path=stems.other,
                    output_path=final_path,
                    config=mix_config,
                )
            else:
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


async def apply_uploaded_music(
    result_path: Path,
    gen_options: dict,
    run_tracker: object,
    progress_bar: object,
    status_label: object,
) -> None:
    """Mix an uploaded music file into the video.

    Args:
        result_path: Path to the assembled video file.
        gen_options: Generation options dict from UI state.
        run_tracker: RunTracker instance.
        progress_bar: NiceGUI progress bar element.
        status_label: NiceGUI status label element.
    """
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

        music_volume = gen_options.get("music_volume", 0.5)
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
