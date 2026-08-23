"""Step 3: Generation Options page with themed components."""

from __future__ import annotations

import logging

from nicegui import ui

from immich_memories.config_models_render import DefaultsConfig, normalize_scale_mode
from immich_memories.ui.components import (
    im_button,
    im_card,
    im_info_card,
    im_section_header,
    im_separator,
    im_stat_card,
)
from immich_memories.ui.state import get_app_state

OUTPUT_FORMAT_OPTIONS = [
    "MP4 (H.264)",
    "MP4 (H.265)",
    "MOV (H.264)",
    "MOV (H.265)",
    "MOV (ProRes)",
]

logger = logging.getLogger(__name__)


def configured_output_format_label(config) -> str:
    """Return the UI label for the configured codec and container."""
    codec = config.output.codec if config is not None else "h264"
    container = config.output.format.upper() if config is not None else "MP4"
    if codec == "prores":
        return "MOV (ProRes)"
    codec_label = "H.265" if codec == "h265" else "H.264"
    return f"{container} ({codec_label})"


_RESOLUTION_LABELS = {"4k": "4K", "1080p": "1080p", "720p": "720p"}

# WHY: the assembler fills an aspect mismatch two ways and no more — a blurred,
# zoomed copy of the frame behind the sharp one, or black bars. Offering a third
# choice here would just relabel the black bars (see FrameDecoder._build_vf).
SCALE_MODE_OPTIONS = {
    "Blur background": "blur",
    "Letterbox (black bars)": "fit",
}
_SCALE_MODE_LABELS = {mode: label for label, mode in SCALE_MODE_OPTIONS.items()}
_DEFAULT_SCALE_MODE_LABEL = _SCALE_MODE_LABELS[DefaultsConfig().scale_mode]


def default_resolution_label(config) -> str:
    """Match clips unless a preset pins the resolution (fast → 1080p on a NAS, not 4K)."""
    if config is None or config.preset is None:
        return "Auto (match clips)"
    return _RESOLUTION_LABELS.get(config.output.resolution, "Auto (match clips)")


def resolve_scale_mode_label(config, stored: str | None = None) -> str:
    """Pick the label to show, starting the wizard wherever the CLI would start.

    A `stored` label saved before the retired modes were dropped is discarded
    rather than shown as a mode that no longer exists, and so is a configured
    mode this wizard has no widget for.
    """
    if stored is not None and stored in SCALE_MODE_OPTIONS:
        return stored
    configured = normalize_scale_mode(config.defaults.scale_mode) if config is not None else ""
    return _SCALE_MODE_LABELS.get(configured, _DEFAULT_SCALE_MODE_LABEL)


def _render_preset_banner(config) -> None:
    if config is None or config.preset != "fast":
        return
    im_info_card(
        "Fast (NAS) preset is active: 1080p H.264 with the fast encoder preset, static "
        "title backgrounds, no speech analysis, fewer photos, favorites-first analysis. "
        "Set with `preset: fast` in config.yaml or IMMICH_MEMORIES_PRESET=fast; the choices "
        "below still win for this run.",
        variant="info",
    )


# A soundtrack is minutes of compressed audio; 64 MiB is generous for MP3/M4A
# and still bounds what one click can pin in session memory (S10).
MAX_MUSIC_UPLOAD_BYTES = 64 * 1024 * 1024

_AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav"}
_AUDIO_MAGIC = (
    b"ID3",  # MP3 with an ID3 tag
    b"RIFF",  # WAV
    b"\xff\xfb",  # MP3 frame sync
    b"\xff\xf3",
    b"\xff\xf2",
)


def is_supported_audio(filename: str, payload: bytes) -> bool:
    """Whether an upload really is one of the audio formats we accept.

    Suffix AND content: the browser's `accept=` filter is advisory, and a
    renamed executable must not reach the mixer or session state.
    """
    from pathlib import Path as _Path

    if _Path(filename).suffix.lower() not in _AUDIO_SUFFIXES:
        return False
    if payload.startswith(_AUDIO_MAGIC):
        return True
    # M4A/MP4: 'ftyp' box at offset 4
    return len(payload) >= 12 and payload[4:8] == b"ftyp"


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
        payload = e.content.read()
        # WHY (S10): `accept=` is browser-side only, so the server must check
        # what actually arrived before holding it in session state.
        if not is_supported_audio(e.name, payload):
            ui.notify("That file is not an MP3, M4A or WAV", type="negative")
            return
        options["music_file"] = payload
        options["music_filename"] = e.name
        ui.notify(f"Uploaded: {e.name}", type="positive")

    ui.upload(
        label="Select music file",
        auto_upload=True,
        max_file_size=MAX_MUSIC_UPLOAD_BYTES,
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
    configured_format = configured_output_format_label(config)

    if not state.generation_options:
        state.generation_options = {
            "orientation": "Auto (detect from clips)",
            "scale_mode": resolve_scale_mode_label(config),
            "transition": "Smart (mix of fades & cuts)",
            "resolution": default_resolution_label(config),
            "format": configured_format,
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
    _render_preset_banner(config)

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
                    options=OUTPUT_FORMAT_OPTIONS,
                    label="Output Format",
                    value=options.get("format", configured_format),
                ).classes("w-full")

                def on_format_change(e):
                    options["format"] = e.value
                    options["format_override"] = e.value

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
                    options=list(SCALE_MODE_OPTIONS),
                    label="Scaling Mode",
                    value=resolve_scale_mode_label(config, options.get("scale_mode")),
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

    from immich_memories.audio.bundled_music import bundled_library

    music_sources = ["None", "Upload file"]
    # Offered only when the music package is installed; picking it otherwise
    # would resolve to silence, which is what "None" is for.
    if bundled_library() is not None:
        music_sources.append("Bundled")
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
