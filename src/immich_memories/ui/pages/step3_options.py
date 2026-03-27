"""Step 3: Generation Options page with themed components."""

from __future__ import annotations

import logging

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


def _render_volume_slider(options: dict, width: str = "w-64") -> None:
    """Render a music volume slider."""
    with ui.row().classes("items-center gap-4 mt-2"):
        ui.label("Music volume:").classes("text-sm")
        volume_slider = ui.slider(
            min=0.0, max=1.0, step=0.05, value=options.get("music_volume", 0.70)
        ).classes(width)

        def on_volume_change(e):
            options["music_volume"] = e.value

        volume_slider.on_value_change(on_volume_change)
        ui.label().bind_text_from(volume_slider, "value", lambda v: f"{int(v * 100)}%")


def _render_upload_music_options(options: dict) -> None:
    """Render the 'Upload file' music source options."""
    ui.label("Select a music file:").classes("text-sm mt-4").style(
        "color: var(--im-text-secondary)"
    )

    async def handle_upload(e):
        options["music_file"] = e.content.read()
        options["music_filename"] = e.name
        ui.notify(f"Uploaded: {e.name}", type="positive")

    ui.upload(
        label="Select music file",
        auto_upload=True,
        on_upload=handle_upload,
    ).props("accept='.mp3,.m4a,.wav'").classes("w-full max-w-md")

    if options.get("music_filename"):
        ui.label(f"Selected: {options['music_filename']}").classes("text-sm").style(
            "color: var(--im-success)"
        )
    _render_volume_slider(options, "w-64")


def _render_ai_music_options(options: dict) -> None:
    """Render the 'AI Generated' music source options."""
    im_info_card(
        "AI will generate music based on the mood of your video clips",
        variant="info",
    )
    _render_volume_slider(options, "w-48")

    from immich_memories.ui.pages._step3_music_preview import render_music_preview_section

    render_music_preview_section(options)


def _render_photo_status(state) -> None:
    """Show photo inclusion status and duration control in Step 3."""
    if not state.include_photos:
        return
    with ui.row().classes("items-center gap-2"):
        ui.icon("photo_library").style("color: var(--im-success)")
        photos_count = len(state.photo_assets) if state.photo_assets else 0
        ui.label(f"Photos enabled ({photos_count} found)").classes("text-sm").style(
            "color: var(--im-success)"
        )

    ui.number(
        "Photo duration (seconds)",
        value=state.photo_duration,
        min=1.0,
        max=10.0,
        step=0.5,
    ).classes("w-48").bind_value(state, "photo_duration")


def render_step3() -> None:
    """Render Step 3: Generation Options."""
    state = get_app_state()

    config = state.config
    ace_step_available = (
        config is not None and getattr(config, "ace_step", None) and config.ace_step.enabled
    )
    musicgen_available = (
        config is not None and config.musicgen.enabled and config.musicgen.base_url
    ) or ace_step_available

    if not state.generation_options:
        state.generation_options = {
            "orientation": "Auto (detect from clips)",
            "scale_mode": "Smart Crop (keep faces)",
            "transition": "Smart (mix of fades & cuts)",
            "resolution": "Auto (match clips)",
            "format": "MP4 (H.264)",
            "add_date": False,
            "music_source": "AI Generated" if musicgen_available else "None",
            "music_file": None,
            "music_volume": 0.7,
        }

    options = state.generation_options

    # ========================================================================
    # Output Settings
    # ========================================================================
    im_section_header("Output Settings", icon="tune")

    with im_card() as card:
        card.classes("p-4")

        # Always visible: Resolution, Output Format
        with ui.row().classes("w-full gap-6"):
            with ui.column().classes("flex-1 gap-4"):
                resolution_select = ui.select(
                    options=["Auto (match clips)", "4K", "1080p", "720p"],
                    label="Resolution",
                    value=options.get("resolution", "Auto (match clips)"),
                ).classes("w-full")

                def on_resolution_change(e):
                    options["resolution"] = e.value

                resolution_select.on_value_change(on_resolution_change)

            with ui.column().classes("flex-1 gap-4"):
                format_select = ui.select(
                    options=["MP4 (H.264)", "MOV (ProRes)"],
                    label="Output Format",
                    value=options.get("format", "MP4 (H.264)"),
                ).classes("w-full")

                def on_format_change(e):
                    options["format"] = e.value

                format_select.on_value_change(on_format_change)

        # Collapsed: advanced output options
        with (
            ui.expansion("Advanced options", icon="settings").classes("w-full mt-2"),
            ui.row().classes("w-full gap-6"),
        ):
            with ui.column().classes("flex-1 gap-4"):
                orientation_select = ui.select(
                    options=[
                        "Auto (detect from clips)",
                        "Landscape (16:9)",
                        "Portrait (9:16)",
                        "Square (1:1)",
                    ],
                    label="Orientation",
                    value=options.get("orientation", "Auto (detect from clips)"),
                ).classes("w-full")

                def on_orientation_change(e):
                    options["orientation"] = e.value

                orientation_select.on_value_change(on_orientation_change)

                scale_select = ui.select(
                    options=[
                        "Smart Crop (keep faces)",
                        "Fill (crop)",
                        "Fit (letterbox)",
                        "Blur (blurred background)",
                    ],
                    label="Scaling Mode",
                    value=options.get("scale_mode", "Smart Crop (keep faces)"),
                ).classes("w-full")

                def on_scale_change(e):
                    options["scale_mode"] = e.value

                scale_select.on_value_change(on_scale_change)

                transition_select = ui.select(
                    options=[
                        "Smart (mix of fades & cuts)",
                        "Crossfade",
                        "Cut",
                        "None",
                    ],
                    label="Transition Style",
                    value=options.get("transition", "Smart (mix of fades & cuts)"),
                ).classes("w-full")

                def on_transition_change(e):
                    options["transition"] = e.value

                transition_select.on_value_change(on_transition_change)

            with ui.column().classes("flex-1 gap-4"):
                date_checkbox = ui.checkbox(
                    "Add date overlay", value=options.get("add_date", False)
                )

                def on_date_change(e):
                    options["add_date"] = e.value

                date_checkbox.on_value_change(on_date_change)

                debug_checkbox = ui.checkbox(
                    "Keep intermediate files",
                    value=options.get("keep_intermediates", False),
                )

                def on_debug_change(e):
                    options["keep_intermediates"] = e.value

                debug_checkbox.on_value_change(on_debug_change)

                _render_photo_status(state)

    # ========================================================================
    # Title Settings
    # ========================================================================
    im_section_header("Title", icon="title")

    with im_card() as title_card:
        title_card.classes("p-4")
        from immich_memories.ui.pages._step3_music_preview import render_title_section

        render_title_section()

    # ========================================================================
    # Music Settings
    # ========================================================================
    im_section_header("Music", icon="music_note")

    music_sources = ["None", "Upload file"]
    if musicgen_available:
        music_sources.append("AI Generated")

    music_options_container = ui.column().classes("w-full")

    def _render_music_options(source: str) -> None:
        music_options_container.clear()
        with music_options_container:
            if source == "Upload file":
                _render_upload_music_options(options)
            elif source == "AI Generated":
                _render_ai_music_options(options)

    music_source_select = ui.select(
        options=music_sources,
        label="Background music",
        value=options.get("music_source", music_sources[-1] if musicgen_available else "None"),
    ).classes("w-64")

    def on_music_source_change(e):
        options["music_source"] = e.value
        _render_music_options(e.value)

    music_source_select.on_value_change(on_music_source_change)
    _render_music_options(options.get("music_source", "None"))

    # ========================================================================
    # Summary
    # ========================================================================
    im_section_header("Summary", icon="summarize")

    selected_clips = state.get_selected_clips()
    total_duration = sum(
        end - start
        for clip in selected_clips
        for start, end in (state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5)),)
    )

    minutes = int(total_duration // 60)
    secs = int(total_duration % 60)
    music_str = "None"
    current_music = options.get("music_source", "None")
    if current_music == "AI Generated":
        music_str = "AI"
    elif current_music == "Upload file" and options.get("music_filename"):
        music_str = "Custom"

    with (
        ui.element("div")
        .classes("w-full grid gap-3")
        .style("grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))")
    ):
        im_stat_card("Clips", str(len(selected_clips)), icon="movie")
        im_stat_card("Duration", f"{minutes}:{secs:02d}", icon="timer")
        im_stat_card("Resolution", options.get("resolution", "Auto"), icon="hd")
        im_stat_card("Music", music_str, icon="music_note")

    im_separator()

    # ========================================================================
    # Navigation
    # ========================================================================
    with ui.row().classes("w-full gap-4"):

        def go_back():
            state.review_selected_mode = True
            state.step = 2
            ui.navigate.to("/step2")

        def go_next():
            state.step = 4
            ui.navigate.to("/step4")

        im_button("Back to Clip Review", variant="secondary", on_click=go_back, icon="arrow_back")
        im_button(
            "Next: Preview & Export", variant="primary", on_click=go_next, icon="arrow_forward"
        )
