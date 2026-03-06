"""Step 3: Generation Options page."""

from __future__ import annotations

import logging

from nicegui import ui

from immich_memories.config import get_config
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def render_step3() -> None:
    """Render Step 3: Generation Options."""
    state = get_app_state()

    # Get config for MusicGen availability check
    config = get_config()
    musicgen_available = config.musicgen.enabled and config.musicgen.base_url

    # Initialize generation options if not set
    if not state.generation_options:
        state.generation_options = {
            "orientation": "Auto (detect from clips)",
            "scale_mode": "Smart Crop (keep faces)",
            "transition": "Smart (mix of fades & cuts)",
            "resolution": "Auto (match clips)",
            "format": "MP4 (H.264)",
            "add_date": False,
            "music_source": "AI Generated (MusicGen)" if musicgen_available else "None",
            "music_file": None,
            "music_volume": 0.4,
            "musicgen_versions": 1,
        }

    options = state.generation_options

    # ========================================================================
    # Output Settings
    # ========================================================================
    ui.label("Output Settings").classes("text-xl font-semibold mt-4")

    with ui.row().classes("w-full gap-8"):
        # Left column
        with ui.column().classes("flex-1 gap-4"):
            # Orientation
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

            # Scale mode
            scale_select = ui.select(
                options=[
                    "Smart Crop (keep faces)",
                    "Fill (crop)",
                    "Fit (letterbox)",
                ],
                label="Scaling Mode",
                value=options.get("scale_mode", "Smart Crop (keep faces)"),
            ).classes("w-full")

            def on_scale_change(e):
                options["scale_mode"] = e.value

            scale_select.on_value_change(on_scale_change)

            # Transition
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

        # Right column
        with ui.column().classes("flex-1 gap-4"):
            # Resolution
            resolution_select = ui.select(
                options=[
                    "Auto (match clips)",
                    "4K",
                    "1080p",
                    "720p",
                ],
                label="Resolution",
                value=options.get("resolution", "Auto (match clips)"),
            ).classes("w-full")

            def on_resolution_change(e):
                options["resolution"] = e.value

            resolution_select.on_value_change(on_resolution_change)

            # Output format
            format_select = ui.select(
                options=["MP4 (H.264)", "MOV (ProRes)"],
                label="Output Format",
                value=options.get("format", "MP4 (H.264)"),
            ).classes("w-full")

            def on_format_change(e):
                options["format"] = e.value

            format_select.on_value_change(on_format_change)

            # Add date overlay
            date_checkbox = ui.checkbox(
                "Add date overlay",
                value=options.get("add_date", False),
            )

            def on_date_change(e):
                options["add_date"] = e.value

            date_checkbox.on_value_change(on_date_change)

            # Keep intermediate files (for debugging)
            debug_checkbox = ui.checkbox(
                "Keep intermediate files",
                value=options.get("keep_intermediates", False),
            )

            def on_debug_change(e):
                options["keep_intermediates"] = e.value

            debug_checkbox.on_value_change(on_debug_change)

    ui.separator().classes("my-6")

    # ========================================================================
    # Music Settings
    # ========================================================================
    ui.label("Music").classes("text-xl font-semibold")

    # Music source options
    music_sources = ["None", "Upload file"]
    if musicgen_available:
        music_sources.append("AI Generated (MusicGen)")

    # Container for music-source-dependent options
    music_options_container = ui.column().classes("w-full")

    def _render_music_options(source: str) -> None:
        """Render music options based on selected source."""
        music_options_container.clear()
        with music_options_container:
            if source == "Upload file":
                ui.label("Select a music file:").classes("text-sm text-gray-500 mt-4")

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
                    ui.label(f"Selected: {options['music_filename']}").classes("text-sm text-green-600")

                with ui.row().classes("items-center gap-4 mt-4"):
                    ui.label("Music volume:").classes("text-sm")
                    volume_slider = ui.slider(
                        min=0.0,
                        max=1.0,
                        step=0.05,
                        value=options.get("music_volume", 0.3),
                    ).classes("w-64")

                    def on_upload_volume_change(e):
                        options["music_volume"] = e.value

                    volume_slider.on_value_change(on_upload_volume_change)
                    ui.label().bind_text_from(volume_slider, "value", lambda v: f"{int(v * 100)}%")

            elif source == "AI Generated (MusicGen)":
                with ui.card().classes("w-full p-4 bg-blue-50 mt-4"):
                    ui.label(
                        "AI will generate music based on the mood of your video clips"
                    ).classes("text-blue-700")

                with ui.row().classes("w-full gap-8 mt-4"):
                    versions_select = ui.select(
                        options=[1, 2, 3],
                        label="Generate versions",
                        value=options.get("musicgen_versions", 3),
                    ).classes("w-40")

                    def on_versions_change(e):
                        options["musicgen_versions"] = e.value

                    versions_select.on_value_change(on_versions_change)

                    with ui.column():
                        ui.label("Music volume").classes("text-sm text-gray-500")
                        with ui.row().classes("items-center gap-2"):
                            volume_slider = ui.slider(
                                min=0.0,
                                max=1.0,
                                step=0.05,
                                value=options.get("music_volume", 0.3),
                            ).classes("w-48")

                            def on_ai_volume_change(e):
                                options["music_volume"] = e.value

                            volume_slider.on_value_change(on_ai_volume_change)
                            ui.label().bind_text_from(volume_slider, "value", lambda v: f"{int(v * 100)}%")

    music_source_select = ui.select(
        options=music_sources,
        label="Background music",
        value=options.get("music_source", music_sources[-1] if musicgen_available else "None"),
    ).classes("w-64")

    def on_music_source_change(e):
        options["music_source"] = e.value
        _render_music_options(e.value)

    music_source_select.on_value_change(on_music_source_change)

    # Render initial music options
    _render_music_options(options.get("music_source", "None"))

    ui.separator().classes("my-6")

    # ========================================================================
    # Summary
    # ========================================================================
    ui.label("Summary").classes("text-xl font-semibold")

    selected_clips = state.get_selected_clips()
    total_duration = sum(
        end - start
        for clip in selected_clips
        for start, end in [state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5))]
    )

    with ui.row().classes("w-full gap-8"):
        with ui.column().classes("items-center"):
            ui.label("Clips").classes("text-sm text-gray-500")
            ui.label(str(len(selected_clips))).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Duration").classes("text-sm text-gray-500")
            minutes = int(total_duration // 60)
            secs = int(total_duration % 60)
            ui.label(f"{minutes}:{secs:02d}").classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Resolution").classes("text-sm text-gray-500")
            ui.label(options.get("resolution", "Auto")).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Music").classes("text-sm text-gray-500")
            music_str = "None"
            current_music = options.get("music_source", "None")
            if current_music == "AI Generated (MusicGen)":
                music_str = "AI"
            elif current_music == "Upload file" and options.get("music_filename"):
                music_str = "Custom"
            ui.label(music_str).classes("text-2xl font-bold")

    ui.separator().classes("my-6")

    # ========================================================================
    # Navigation
    # ========================================================================
    with ui.row().classes("w-full gap-4"):

        def go_back():
            # Go back to Step 2 with review mode to show selected clips
            state.review_selected_mode = True
            state.step = 2
            ui.navigate.to("/step2")

        def go_next():
            state.step = 4
            ui.navigate.to("/step4")

        ui.button("Back to Clip Review", on_click=go_back, icon="arrow_back").props("outline")
        ui.button(
            "Next: Preview & Export",
            on_click=go_next,
            icon="arrow_forward",
        ).props("color=primary")
