"""Step 4: Preview & Export page with themed components."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import ui

from immich_memories.ui.components import (
    im_button,
    im_card,
    im_info_card,
    im_section_header,
    im_separator,
    im_stat_card,
)
from immich_memories.ui.i18n import tr
from immich_memories.ui.pages.film_length import film_length_stat, measured_film_label
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def _render_existing_result(state) -> None:
    """Render the durable outcome when Step 4 is revisited in this session."""
    if not state.output_path:
        return
    output_path = Path(state.output_path)
    if not output_path.is_file():
        return

    im_section_header(tr("Result"), icon="check_circle")
    ui.label(tr("Saved to: {output_path}", output_path=output_path)).classes("text-sm").style(
        "color: var(--im-text-secondary)"
    )
    if (length := measured_film_label(state)) is not None:
        ui.label(length).classes("text-sm").style("color: var(--im-text-secondary)")
    if state.generation_warning:
        ui.label(state.generation_warning).classes("text-sm").style("color: var(--im-warning)")
    delivery_label = state.delivery_status.value.replace("_", " ").title()
    ui.label(tr("Immich delivery: {delivery_label}", delivery_label=delivery_label)).classes(
        "text-sm"
    ).style("color: var(--im-text-secondary)")
    ui.video(output_path).classes("w-full rounded-lg").style(
        "max-height: 400px; object-fit: contain; background: var(--im-bg-surface)"
    )


def _render_recovered_run(state) -> None:
    """Tell a reloaded page about a run that outlived the previous page (#322)."""
    from immich_memories.ui.pages.step4_recovery import recover_active_run

    recovered = recover_active_run(state)
    if recovered is None or recovered.status == "completed":
        return  # a completed run is restored into state and shown by _render_existing_result
    run_id = recovered.run.run_id if recovered.run else state.active_run_id
    if recovered.status == "running":
        started = recovered.run.created_at.astimezone().strftime("%H:%M") if recovered.run else "…"
        im_info_card(
            tr(
                "A generation started at {started} is still running (run {run_id}). This page checks every few seconds and shows the video when it finishes.",
                started=started,
                run_id=run_id,
            ),
            variant="info",
        )

        timer = ui.timer(5.0, lambda: _poll_recovered_run(state, timer))
        return
    if recovered.status == "stale":
        message = f"Run {run_id} was still marked running hours later — it did not finish. "
    else:
        message = f"The last generation (run {run_id}) {recovered.status}. "
    im_info_card((message) + (tr("Check the server log, then generate again.")), variant="warning")


def _poll_recovered_run(state, timer) -> bool:
    """Reload the page once the run has left "running", and stop the timer with it.

    The old timer kept firing after the reload it triggered, so a finished run
    reloaded the page every five seconds for as long as it stayed open (#824).
    """
    from immich_memories.ui.pages.step4_recovery import recover_active_run

    current = recover_active_run(state)
    if current is not None and current.status == "running":
        return False
    timer.deactivate()
    ui.navigate.reload()
    return True


def _render_photo_preview(state, photos_count: int) -> None:
    """Render the optional photo pool without inflating the page orchestrator."""
    if not photos_count:
        return

    from immich_memories.ui.pages.step2_helpers import render_thumbnail

    with ui.expansion(
        tr(
            "{photos_count} Photos Available (auto-selected at generation)",
            photos_count=photos_count,
        ),
        icon="photo_library",
        value=False,
    ).classes("w-full"):
        max_preview = 40
        with (
            ui.element("div")
            .classes("w-full grid gap-2")
            .style("grid-template-columns: repeat(auto-fill, minmax(80px, 1fr))")
        ):
            for photo in state.photo_assets[:max_preview]:
                render_thumbnail(
                    photo.id, classes="w-full rounded", style="aspect-ratio: 1; object-fit: cover"
                )
        if photos_count > max_preview:
            ui.label(tr("+ {value} more", value=photos_count - max_preview)).classes(
                "text-sm mt-1"
            ).style("color: var(--im-text-secondary)")


def render_step4() -> None:
    """Render Step 4: Preview & Export."""
    state = get_app_state()

    selected_clips = state.get_selected_clips()

    if not selected_clips:
        im_info_card(tr("No clips selected. Go back to select clips."), variant="warning")

        def go_back():
            state.step = 2
            ui.navigate.to("/step2")

        im_button(
            tr("Back to the media pool"), variant="secondary", on_click=go_back, icon="arrow_back"
        )
        return

    options = state.generation_options

    # Summary
    im_section_header(tr("Summary"), icon="summarize")

    photos_count = len(state.photo_assets) if state.include_photos and state.photo_assets else 0

    with (
        ui.element("div")
        .classes("w-full grid gap-3 mb-2")
        .style("grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))")
    ):
        im_stat_card(tr("Clips"), str(len(selected_clips)), icon="movie")
        if photos_count:
            im_stat_card(tr("Photo Pool"), str(photos_count), icon="photo_library")
        im_stat_card(*film_length_stat(state, selected_clips), icon="timer")
        im_stat_card(tr("Format"), options.get("format", "MP4"), icon="video_file")

    # Photo preview (if included)
    _render_photo_preview(state, photos_count)

    # Output Settings (merged with Upload)
    im_section_header(tr("Output"), icon="folder")

    if state.config is None:
        raise RuntimeError("Output settings require a loaded configuration")
    output_dir = state.config.output.output_path
    output_dir.mkdir(parents=True, exist_ok=True)

    from immich_memories.filename_builder import build_output_filename
    from immich_memories.ui.pages._step4_generate import resolve_ui_output_selection

    date_range = state.date_range
    person = state.selected_person
    output_selection = resolve_ui_output_selection(state)

    default_filename = build_output_filename(
        memory_type=state.memory_type,
        preset_params=state.memory_preset_params,
        person_name=person.name if person else None,
        date_start=date_range.start if date_range else None,
        date_end=date_range.end if date_range else None,
        container=output_selection.container,
    )

    from immich_memories.ui.pages._step4_upload import init_upload_state, render_upload_controls

    init_upload_state(state)

    with im_card() as card:
        card.classes("p-4")
        filename_input = ui.input(tr("Output filename"), value=default_filename).classes(
            "w-full max-w-lg"
        )
        ui.label(tr("Will be saved to: {value}", value=output_dir / default_filename)).classes(
            "text-sm"
        ).style("color: var(--im-text-secondary)").bind_text_from(
            filename_input, "value", lambda v: f"Will be saved to: {output_dir / v}"
        )

        ui.separator().classes("my-2")

        render_upload_controls(state)

    # Generate Button and Progress
    progress_container = ui.column().classes("w-full min-h-[200px]")
    output_container = ui.column().classes("w-full min-h-[200px]")

    async def generate_video():
        from immich_memories.ui.pages._step4_generate import run_generation

        await run_generation(
            state=state,
            selected_clips=selected_clips,
            output_dir=output_dir,
            output_path=output_dir / default_filename,
            filename_input=filename_input,
            progress_container=progress_container,
            output_container=output_container,
        )

    im_button(
        tr("Generate Video"), variant="primary", on_click=generate_video, icon="movie"
    ).classes("w-full")

    # A run that finished (or is still running) while this page was gone — below the button
    _render_recovered_run(state)
    _render_existing_result(state)

    im_separator()
    # Navigation
    with ui.row().classes("w-full gap-4"):
        im_button(
            tr("Back to Generation Options"),
            variant="secondary",
            icon="arrow_back",
            on_click=lambda: (setattr(state, "step", 3), ui.navigate.to("/step3")),
        )
        im_button(
            tr("Start New Project"),
            variant="secondary",
            icon="refresh",
            on_click=lambda: (state.reset_clips(), setattr(state, "step", 1), ui.navigate.to("/")),
        )
