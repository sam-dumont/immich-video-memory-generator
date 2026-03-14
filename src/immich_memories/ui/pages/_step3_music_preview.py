"""Music preview generation and playback UI for Step 3.

Allows users to generate AI music, preview the track, and optionally
regenerate before proceeding to video export.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from nicegui import ui

from immich_memories.config import get_config
from immich_memories.security import sanitize_error_message
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def _build_timeline(state):
    """Build a VideoTimeline from selected clips in state."""
    from immich_memories.audio.music_generator import VideoTimeline

    config = get_config()
    selected_clips = state.get_selected_clips()

    # Look up LLM emotions from the analysis cache for accurate mood prompts
    mood_cache: dict[str, str] = {}
    if state.analysis_cache:
        for clip in selected_clips:
            analysis = state.analysis_cache.get_analysis(clip.asset.id)
            if analysis and analysis.segments:
                for seg in analysis.segments:
                    if seg.llm_emotion:
                        mood_cache[clip.asset.id] = seg.llm_emotion
                        break

    clip_data: list[tuple[float, str, int | None]] = []
    for clip in selected_clips:
        segment = state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5))
        duration = segment[1] - segment[0]
        mood = mood_cache.get(clip.asset.id, "calm")
        month = None
        if clip.asset.file_created_at:
            try:
                month = clip.asset.file_created_at.month
            except AttributeError:
                pass
        clip_data.append((duration, mood, month))

    return VideoTimeline.from_clips(
        clips=clip_data,
        title_duration=(config.title_screens.title_duration if config.title_screens.enabled else 0),
        ending_duration=(
            config.title_screens.ending_duration if config.title_screens.enabled else 0
        ),
    )


async def _generate_music(
    state,
    progress_bar,
    status_label,
    player_container,
):
    """Generate a single music track using the multi-provider pipeline."""
    config = get_config()

    state.music_generating = True
    progress_bar.set_visibility(True)
    status_label.set_visibility(True)
    status_label.set_text("Building mood timeline...")
    progress_bar.value = 0.0

    try:
        from immich_memories.audio.music_generator import (
            MusicGenClientConfig,
            generate_music_for_video,
        )

        timeline = _build_timeline(state)

        musicgen_config = MusicGenClientConfig.from_app_config(config.musicgen)
        musicgen_config.num_versions = 1

        output_dir = Path(tempfile.mkdtemp(prefix="immich_music_preview_"))
        music_dir = output_dir / "music"
        music_dir.mkdir(exist_ok=True)

        def music_progress(version_idx, status, progress, detail):
            progress_bar.value = min(progress / 100.0, 0.99)
            status_label.set_text(f"{status} ({int(progress)}%)")

        status_label.set_text("Generating music...")
        music_result = await generate_music_for_video(
            timeline=timeline,
            output_dir=music_dir,
            config=musicgen_config,
            progress_callback=music_progress,
            app_config=config,
        )

        # Store result in state for Step 4 to use
        if music_result and music_result.versions:
            music_result.selected_version = 0
            state.music_preview_result = music_result

        progress_bar.value = 1.0
        status_label.set_text("Music generated!")

        # Render audio player
        _render_player(state, player_container)

        ui.notify("Music generated successfully!", type="positive")

    except Exception as e:
        logger.warning(f"Music preview generation failed: {e}")
        status_label.set_text(f"Failed: {sanitize_error_message(str(e))}")
        ui.notify(
            f"Music generation failed: {sanitize_error_message(str(e))}",
            type="negative",
        )
    finally:
        state.music_generating = False


def _render_player(state, container):
    """Render audio player for the generated music track."""
    container.clear()
    result = state.music_preview_result
    if not result or not result.versions:
        return

    version = result.versions[0]
    if not version.full_mix or not version.full_mix.exists():
        return

    with container, ui.card().classes("w-full p-4 bg-green-50 border-green-300 mt-4"):
        with ui.row().classes("w-full items-center gap-4"):
            ui.icon("check_circle", color="green").classes("text-2xl")
            with ui.column().classes("flex-1"):
                ui.label("Generated Music").classes("font-medium")
                mood_text = version.mood or result.mood or "auto"
                ui.label(f"Mood: {mood_text}").classes("text-sm text-gray-500")

        # Audio player
        ui.audio(str(version.full_mix)).classes("w-full mt-2")


def render_title_section() -> None:
    """Render the title editing section in Step 3.

    Shows LLM-generated title and subtitle as editable fields, plus
    read-only chips for trip_type and map_mode when set.
    """
    state = get_app_state()

    with ui.column().classes("w-full gap-3"):
        title_input = ui.input(
            label="Title",
            value=state.title_suggestion_title or "",
            placeholder="e.g. Summer in Saxony",
        ).classes("w-full")

        def on_title_change(e):
            state.title_suggestion_title = e.value or None

        title_input.on_value_change(on_title_change)

        subtitle_input = ui.input(
            label="Subtitle",
            value=state.title_suggestion_subtitle or "",
            placeholder="e.g. June – August 2025",
        ).classes("w-full")

        def on_subtitle_change(e):
            state.title_suggestion_subtitle = e.value or None

        subtitle_input.on_value_change(on_subtitle_change)

        # Read-only metadata chips
        if state.title_suggestion_trip_type or state.title_suggestion_map_mode:
            with ui.row().classes("gap-2 mt-1"):
                if state.title_suggestion_trip_type:
                    ui.badge(
                        f"Trip: {state.title_suggestion_trip_type}",
                    ).props("outline").classes("text-xs")
                if state.title_suggestion_map_mode:
                    ui.badge(
                        f"Map: {state.title_suggestion_map_mode}",
                    ).props("outline").classes("text-xs")


def render_music_preview_section(options: dict) -> None:
    """Render the music preview section in Step 3.

    This adds generate/preview/regenerate controls below the music settings
    when AI music generation is selected.
    """
    state = get_app_state()

    # Container for progress and player
    progress_container = ui.column().classes("w-full mt-4")
    player_container = ui.column().classes("w-full")

    with progress_container:
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_bar.set_visibility(False)
        status_label = ui.label("").classes("text-sm text-gray-600")
        status_label.set_visibility(False)

    # Show existing preview if available
    if state.music_preview_result and state.music_preview_result.versions:
        _render_player(state, player_container)

    # Generate / Regenerate button
    has_preview = state.music_preview_result and state.music_preview_result.versions
    button_text = "Regenerate Music" if has_preview else "Generate Music"
    button_icon = "refresh" if has_preview else "music_note"

    async def on_generate():
        await _generate_music(state, progress_bar, status_label, player_container)

    ui.button(
        button_text,
        on_click=on_generate,
        icon=button_icon,
    ).props("color=secondary").classes("mt-2")

    if not has_preview:
        ui.label("Generate music now to preview before rendering your video").classes(
            "text-sm text-gray-500 mt-1"
        )
