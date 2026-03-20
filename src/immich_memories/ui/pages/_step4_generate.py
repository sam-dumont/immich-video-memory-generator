"""Video generation logic for Step 4 export.

Thin UI wrapper around generate_memory() — builds GenerationParams
from AppState, delegates pipeline work, then shows output.
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

# Maps UI labels from step3_options → GenerationParams values
_TRANSITION_MAP = {
    "Smart (mix of fades & cuts)": "smart",
    "Crossfade": "crossfade",
    "Cut": "cut",
    "None": "none",
}

_RESOLUTION_MAP = {
    "4K": "4K",
    "1080p": "1080p",
    "720p": "720p",
    "Auto (match clips)": None,
}

_SCALE_MODE_MAP = {
    "Smart Crop (keep faces)": "smart_crop",
    "Fill (crop)": "fill",
    "Fit (letterbox)": "fit",
}

_FORMAT_MAP = {
    "MP4 (H.264)": "mp4",
    "MOV (ProRes)": "prores",
}


def _build_generation_params(state, selected_clips, output_path):
    """Build GenerationParams from UI AppState."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.generate import GenerationParams

    gen_options = state.generation_options
    person = state.selected_person
    date_range = state.date_range

    client = SyncImmichClient(base_url=state.immich_url, api_key=state.immich_api_key)

    return GenerationParams(
        clips=selected_clips,
        output_path=output_path,
        config=state.config,
        client=client,
        transition=_TRANSITION_MAP.get(
            gen_options.get("transition", "Smart (mix of fades & cuts)"), "crossfade"
        ),
        output_resolution=_RESOLUTION_MAP.get(gen_options.get("resolution", "Auto (match clips)")),
        scale_mode=_SCALE_MODE_MAP.get(
            gen_options.get("scale_mode", "Smart Crop (keep faces)"), "smart_crop"
        ),
        output_format=_FORMAT_MAP.get(gen_options.get("format", "MP4 (H.264)"), "mp4"),
        add_date_overlay=gen_options.get("add_date", False),
        debug_preserve_intermediates=gen_options.get("keep_intermediates", False),
        privacy_mode=state.demo_mode,
        person_name=person.name if person else None,
        date_start=date_range.start if date_range else None,
        date_end=date_range.end if date_range else None,
        memory_type=state.memory_type,
        memory_preset_params=state.memory_preset_params,
        title=state.title_suggestion_title,
        subtitle=state.title_suggestion_subtitle,
        clip_segments=state.clip_segments,
        clip_rotations=state.clip_rotations,
        # Music and upload handled separately by UI (AI gen, 4-stem ducking, NiceGUI progress)
        music_path=None,
        upload_enabled=False,
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
    """Execute video generation by building GenerationParams and calling generate_memory()."""
    from immich_memories.security import sanitize_filename

    progress_container.clear()
    with progress_container:
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_bar.style("--q-linear-progress-color: var(--im-primary)")
        status_label = ui.label("Starting...").classes("text-sm").style("color: var(--im-text)")
        run_id_label = ui.label("").classes("text-sm").style("color: var(--im-text-secondary)")

    try:
        from immich_memories.generate import generate_memory

        effective_output_path = output_dir / sanitize_filename(filename_input.value)

        def on_progress(phase: str, progress: float, msg: str) -> None:
            progress_bar.value = progress
            status_label.set_text(msg)

        params = _build_generation_params(state, selected_clips, effective_output_path)
        params.progress_callback = on_progress

        # Phases 1+2: extract clips and assemble video
        result_path = await run.io_bound(generate_memory, params)

        run_id_label.set_text(f"Output: {result_path.parent.name}")

        # Phase 3: Music (UI-specific — supports AI generation + 4-stem ducking)
        from immich_memories.config import get_config
        from immich_memories.tracking import RunTracker, generate_run_id

        run_tracker = RunTracker(
            generate_run_id(),
            db_path=get_config().cache.database_path,
        )
        await _apply_music(
            state,
            state.config,
            result_path,
            [],  # assembly_clips not needed for pre-generated music path
            result_path.parent,
            run_tracker,
            progress_bar,
            status_label,
        )

        # Phase 4: Upload (UI-specific — NiceGUI progress feedback)
        if state.upload_enabled:
            from immich_memories.ui.pages._step4_upload import upload_to_immich

            await upload_to_immich(result_path, state, progress_bar, status_label)

        progress_bar.value = 1.0
        status_label.set_text("Complete!")
        state.output_path = result_path

        _show_output(output_container, result_path)

    except Exception as e:
        logger.exception("Video generation failed")
        safe_msg = sanitize_error_message(str(e))
        ui.notify(f"Generation failed: {safe_msg}", type="negative")
        progress_container.clear()
        with progress_container:
            im_info_card(f"Generation failed: {safe_msg}", variant="error")


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
