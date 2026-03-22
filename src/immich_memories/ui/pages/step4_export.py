"""Step 4: Preview & Export page with themed components."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import ui

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
        for start, end in (state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5)),)
    )

    options = state.generation_options

    # Summary
    im_section_header("Summary", icon="summarize")

    with ui.element("div").classes("grid grid-cols-3 gap-4 mb-4"):
        im_stat_card("Clips", str(len(selected_clips)), icon="movie")
        im_stat_card("Duration", format_duration(total_duration), icon="timer")
        im_stat_card("Format", options.get("format", "MP4"), icon="video_file")

    im_separator()

    # Output Settings
    im_section_header("Output", icon="folder")

    output_dir = Path.home() / "Videos" / "Memories"
    output_dir.mkdir(parents=True, exist_ok=True)

    from immich_memories.ui.filename_builder import build_output_filename

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

    # Upload to Immich
    im_section_header("Upload to Immich", icon="cloud_upload")
    from immich_memories.ui.pages._step4_upload import init_upload_state, render_upload_controls

    init_upload_state(state)
    render_upload_controls(state)
    im_separator()

    # Generate Button and Progress
    progress_container = ui.column().classes("w-full")
    output_container = ui.column().classes("w-full")

    async def generate_video():
        from immich_memories.ui.pages._step4_generate import run_generation

        await run_generation(
            state=state,
            selected_clips=selected_clips,
            total_duration=total_duration,
            output_dir=output_dir,
            output_path=output_dir / default_filename,
            filename_input=filename_input,
            progress_container=progress_container,
            output_container=output_container,
        )

    im_button("Generate Video", variant="primary", on_click=generate_video, icon="movie").classes(
        "w-full"
    )

    if state.output_path and Path(state.output_path).exists():
        im_separator()
        im_section_header("Output", icon="check_circle")
        ui.label(f"Saved to: {state.output_path}").classes("text-sm").style(
            "color: var(--im-text-secondary)"
        )
        video_url = nicegui_app.add_media_file(local_file=Path(state.output_path))
        with (
            ui.element("div")
            .classes("rounded-xl overflow-hidden mt-4")
            .style("background: var(--im-bg)")
        ):
            ui.video(video_url).classes("w-full max-w-2xl").style(
                "max-height: 60vh; object-fit: contain"
            )

    im_separator()
    # Navigation
    with ui.row().classes("w-full gap-4"):
        im_button(
            "Back to Generation Options",
            variant="secondary",
            icon="arrow_back",
            on_click=lambda: (setattr(state, "step", 3), ui.navigate.to("/step3")),
        )
        im_button(
            "Start New Project",
            variant="secondary",
            icon="refresh",
            on_click=lambda: (state.reset_clips(), setattr(state, "step", 1), ui.navigate.to("/")),
        )
