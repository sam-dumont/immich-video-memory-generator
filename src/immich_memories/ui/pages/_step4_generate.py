"""Video generation logic for Step 4 export.

Thin UI wrapper around generate_memory() — builds GenerationParams
from AppState, delegates pipeline work, then shows output.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import app as nicegui_app
from nicegui import run as run  # re-exported test seam shared by nicegui_compat
from nicegui import ui

from immich_memories.generate import PreparedGeneration, generate_memory
from immich_memories.processing.output_contract import validate_output
from immich_memories.security import configured_secret_values, sanitize_error_message
from immich_memories.ui.components import (
    im_info_card,
    im_separator,
)
from immich_memories.ui.nicegui_compat import io_bound_result, run_ui_observer

if TYPE_CHECKING:
    from immich_memories.processing.encoding_plan import EncodingPlan

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


def _safe_ui_error_message(error: Exception, config: Config | None) -> str:
    """Return a user-safe error without any configured credential literals."""
    message = sanitize_error_message(str(error))
    if config is not None:
        for secret in configured_secret_values(config):
            message = message.replace(secret, "***")
    return message


def _restore_completed_ui_state(state, completed, fallback_output_path: Path | None = None) -> None:
    """Restore durable artifact facts before or after UI-only observer failures."""
    if completed is None or completed.status != "completed":
        return
    state.output_path = (
        Path(completed.output_path) if completed.output_path else fallback_output_path
    )
    state.generation_warning = completed.warnings[-1] if completed.warnings else None
    state.delivery_status = completed.delivery_status


def _read_persisted_run(run_tracker):
    """Read lifecycle truth without allowing a secondary diagnostic failure to escape."""
    try:
        return run_tracker.db.get_run(run_tracker.run_id)
    except Exception:  # WHY: the primary UI error must remain user-visible and sanitized
        logger.error("Could not inspect run lifecycle after UI failure")
        return None


def _request_cancel(state, cancel_btn: ui.button | None, status_label) -> None:
    """Request cancellation of the running generation."""
    state.cancel_requested = True
    if cancel_btn is not None:
        cancel_btn.set_text("Cancelling...")
        cancel_btn.disable()
    status_label.set_text("Cancel requested — stopping after current phase...")


def _write_progress_ui(progress_bar, status_label, progress: float, message: str) -> bool:
    """Update progress widgets unless their NiceGUI client has disappeared."""

    def write() -> None:
        progress_bar.value = progress
        status_label.set_text(message)

    return run_ui_observer(write, description="generation progress UI")


def _write_phase_ui(status_label, message: str) -> bool:
    """Update the current phase unless its NiceGUI client has disappeared."""
    return run_ui_observer(
        lambda: status_label.set_text(message),
        description="generation phase UI",
    )


def _write_preview_ui(preview_image, jpeg_bytes: bytes) -> bool:
    """Update the frame preview unless its NiceGUI client has disappeared."""
    source = f"data:image/jpeg;base64,{base64.b64encode(jpeg_bytes).decode()}"

    def write() -> None:
        preview_image.source = source

    return run_ui_observer(write, description="generation preview UI")


def _write_completion_ui(
    run_id_label,
    progress_bar,
    status_label,
    cancel_btn,
    output_container,
    result_path: Path,
    state,
) -> bool:
    """Render completed-artifact facts unless the NiceGUI client disconnected."""

    def write() -> None:
        run_id_label.set_text(f"Output: {result_path.parent.name}")
        progress_bar.value = 1.0
        status_label.set_text("Complete!")
        cancel_btn.set_visibility(False)
        _show_output(output_container, result_path, state)

    return run_ui_observer(write, description="generation completion UI")


def _write_failure_ui(
    cancel_btn,
    progress_container,
    notice: str,
    *,
    artifact_completed: bool,
) -> bool:
    """Render failure facts unless the NiceGUI client disconnected."""

    def write() -> None:
        ui.notify(notice, type="warning" if artifact_completed else "negative")
        cancel_btn.set_visibility(False)
        progress_container.clear()
        with progress_container:
            im_info_card(notice, variant="warning" if artifact_completed else "error")

    return run_ui_observer(write, description="generation failure UI")


# Maps UI labels from step3_options → GenerationParams values
_TRANSITION_MAP = {
    "Smart (mix of fades & cuts)": "smart",
    "Crossfade": "crossfade",
    "Cut": "cut",
    "None": "none",
}

_RESOLUTION_MAP = {
    "4K": "4k",
    "1080p": "1080p",
    "720p": "720p",
    "Auto (match clips)": "auto",
}

_SCALE_MODE_MAP = {
    "Smart Crop (keep faces)": "smart_crop",
    "Fill (crop)": "fill",
    "Fit (letterbox)": "fit",
    "Blur (blurred background)": "blur",
}

_FORMAT_MAP = {
    "MP4 (H.264)": "mp4",
    "MP4 (H.265)": "h265",
    "MOV (H.264)": "h264_mov",
    "MOV (H.265)": "h265_mov",
    "MOV (ProRes)": "prores",
}


def resolve_ui_output_selection(state):
    """Resolve UI/config output provenance through the shared output contract."""
    from immich_memories.processing.encoding_plan import resolve_output_selection

    if state.config is None:
        raise ValueError("Output settings require a loaded configuration")
    return resolve_output_selection(
        config_codec=state.config.output.codec,
        config_container=state.config.output.format,
        format_override=_FORMAT_MAP.get(state.generation_options.get("format_override")),
    )


def normalize_ui_output_path(state, output_path: Path) -> Path:
    """Make the selected UI filename suffix agree with the resolved container."""
    from immich_memories.filename_builder import normalize_output_path

    selection = resolve_ui_output_selection(state)
    return normalize_output_path(output_path, selection.container)


def _filter_selected_photos(state) -> list | None:
    """Return only photo assets whose IDs are in the selected set."""
    if not state.include_photos or not state.photo_assets:
        return None
    return [p for p in state.photo_assets if p.id in state.selected_photo_ids]


def _build_generation_params(state, selected_clips, output_path):
    """Build GenerationParams from UI AppState."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.generate import GenerationParams

    gen_options = state.generation_options
    person = state.selected_person
    date_range = state.date_range

    # Apply photo duration from UI to config
    if state.include_photos and state.config:
        state.config.photos.duration = state.photo_duration

    client = SyncImmichClient(
        base_url=state.immich_url,
        api_key=state.immich_api_key,
        api_version=state.immich_api_version,
    )

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
        output_format=_FORMAT_MAP.get(gen_options.get("format_override")),
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
        # WHY: Photos are already in selected_clips as IMAGE-type assets
        # from the unified selection pool. Setting include_photos=False
        # prevents _add_photos_if_enabled from re-adding them.
        include_photos=False,
        photo_assets=None,
        target_duration_seconds=state.target_duration * 60,
        selected_photo_ids=None,
        # Music and upload are finalized separately by the UI on the same run lifecycle.
        music_path=None,
        no_music=True,
        upload_enabled=state.upload_enabled,
        upload_album=state.upload_album_name,
    )


async def execute_ui_generation(
    state,
    params,
    run_tracker,
    progress_bar,
    status_label,
) -> PreparedGeneration:
    """Run assembly and UI finalization with one caller-owned tracker."""
    prepared = await io_bound_result(
        generate_memory,
        params,
        run_tracker=run_tracker,
        defer_finalization=True,
    )
    await finalize_ui_generation(
        state,
        params,
        prepared,
        run_tracker,
        progress_bar,
        status_label,
    )
    return prepared


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
    from immich_memories.ui.components import im_button

    state.cancel_requested = False
    state.generation_warning = None
    from immich_memories.tracking import DeliveryStatus

    state.delivery_status = DeliveryStatus.NOT_REQUESTED
    state.upload_result = None
    state.output_path = None
    run_tracker = None
    # Mutable ref so the lambda closure can access the button after creation
    cancel_ref: list[ui.button | None] = [None]

    progress_container.clear()
    with progress_container:
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_bar.style("--q-linear-progress-color: var(--im-primary)")
        status_label = ui.label("Starting...").classes("text-sm").style("color: var(--im-text)")
        run_id_label = ui.label("").classes("text-sm").style("color: var(--im-text-secondary)")

        preview_image = (
            ui.image().classes("w-full rounded-lg").style("max-height: 400px; object-fit: contain")
        )

        cancel_btn = im_button(
            "Cancel",
            variant="secondary",
            on_click=lambda: _request_cancel(state, cancel_ref[0], status_label),
            icon="cancel",
        )
        cancel_ref[0] = cancel_btn

    try:
        from immich_memories.generate import GenerationError

        effective_output_path = normalize_ui_output_path(
            state,
            output_dir / sanitize_filename(filename_input.value),
        )

        def on_progress(phase: str, progress: float, msg: str) -> None:
            if state.cancel_requested:
                raise GenerationError("Generation cancelled by user")
            _write_progress_ui(progress_bar, status_label, progress, msg)

        def on_phase(event) -> None:
            _write_phase_ui(status_label, event.message)

        def on_frame_preview(jpeg_bytes: bytes) -> None:
            _write_preview_ui(preview_image, jpeg_bytes)

        params = _build_generation_params(state, selected_clips, effective_output_path)
        params.progress_callback = on_progress
        params.phase_callback = on_phase
        params.frame_preview_callback = on_frame_preview

        from immich_memories.tracking import RunTracker, generate_run_id

        run_tracker = RunTracker(
            generate_run_id(),
            db_path=params.config.cache.database_path,
        )
        prepared = await execute_ui_generation(
            state,
            params,
            run_tracker,
            progress_bar,
            status_label,
        )
        result_path = prepared.path
        state.output_path = result_path
        persisted = _read_persisted_run(run_tracker)
        _restore_completed_ui_state(state, persisted, result_path)
        _write_completion_ui(
            run_id_label,
            progress_bar,
            status_label,
            cancel_btn,
            output_container,
            result_path,
            state,
        )

    except Exception as e:  # WHY: UI graceful degradation
        safe_msg = _safe_ui_error_message(e, state.config)
        persisted = None
        if run_tracker is not None:
            persisted = _read_persisted_run(run_tracker)
            if persisted is not None and persisted.status == "running":
                try:
                    run_tracker.fail_run(safe_msg)
                except Exception:  # WHY: durable UI failures must not replace the primary message
                    logger.error("Could not persist UI generation failure state")
        artifact_completed = (
            persisted is not None and persisted.status == "completed"
        ) or state.output_path is not None
        if artifact_completed:
            _restore_completed_ui_state(state, persisted)
            logger.error("Completion screen failed after artifact publication")
            notice = f"Video saved, but the completion screen failed: {safe_msg}"
        else:
            logger.error("Video generation failed: %s", safe_msg)
            notice = f"Generation failed: {safe_msg}"
        _write_failure_ui(
            cancel_btn,
            progress_container,
            notice,
            artifact_completed=artifact_completed,
        )


async def finalize_ui_generation(
    state,
    params,
    prepared,
    run_tracker,
    progress_bar,
    status_label,
):
    """Finish UI-managed post-processing on the caller-owned run."""
    from immich_memories.generate import emit_operational_phase
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.operations.phases import OperationalPhase

    music_result = MusicPhaseResult(applied=False)
    music_source = state.generation_options.get("music_source", "None")
    emit_operational_phase(
        params,
        run_tracker,
        OperationalPhase.MUSIC,
        current=0,
        total=1 if music_source in {"AI Generated", "Upload file"} else 0,
        message=(
            "Applying music"
            if music_source in {"AI Generated", "Upload file"}
            else "Music disabled"
        ),
    )
    if music_source in {"AI Generated", "Upload file"}:
        music_result = await _apply_music(
            state,
            params.config,
            prepared.path,
            list(params.clips),
            prepared.path.parent,
            run_tracker,
            progress_bar,
            status_label,
            encoding_plan=prepared.encoding_plan,
        )
        emit_operational_phase(
            params,
            run_tracker,
            OperationalPhase.MUSIC,
            current=1,
            total=1,
            message=music_result.warning or "Music ready",
        )
    final_probe = await io_bound_result(validate_output, prepared.path, prepared.encoding_plan)
    from immich_memories.generate import _validate_final_duration

    _validate_final_duration(params, final_probe.duration_seconds)
    warnings = [music_result.warning] if music_result.warning else []
    completed = run_tracker.complete_artifact(
        prepared.path,
        final_probe,
        warnings,
        delivery_requested=params.upload_enabled,
        delivery_album=params.upload_album,
        clips_analyzed=prepared.clips_analyzed,
        clips_selected=prepared.clips_selected,
    )
    state.generation_warning = music_result.warning
    state.delivery_status = completed.delivery_status
    emit_operational_phase(
        params,
        run_tracker,
        OperationalPhase.DELIVERY,
        current=0,
        total=1 if params.upload_enabled else 0,
        message="Uploading to Immich" if params.upload_enabled else "Delivery not requested",
    )
    if params.upload_enabled:
        from immich_memories.tracking.models import DeliveryStatus
        from immich_memories.ui.pages._step4_upload import upload_to_immich

        completed = await upload_to_immich(
            prepared.path,
            state,
            params,
            run_tracker,
            progress_bar,
            status_label,
        )
        if completed.delivery_status is not DeliveryStatus.DELIVERED:
            emit_operational_phase(
                params,
                run_tracker,
                OperationalPhase.DELIVERY,
                current=0,
                total=1,
                message="Delivery pending",
            )
            return run_tracker.db.get_run(run_tracker.run_id) or completed
        emit_operational_phase(
            params,
            run_tracker,
            OperationalPhase.DELIVERY,
            current=1,
            total=1,
            message="Delivered to Immich",
        )
    emit_operational_phase(
        params,
        run_tracker,
        OperationalPhase.COMPLETE,
        current=1,
        total=1,
        message="Complete",
    )
    completed = run_tracker.db.get_run(run_tracker.run_id) or completed
    return completed


async def _apply_music(
    state,
    config,
    result_path,
    selected_clips,
    run_output_dir,
    run_tracker,
    progress_bar,
    status_label,
    *,
    encoding_plan: EncodingPlan,
):
    """Apply selected UI music after its published artifact contract is derived."""
    gen_options = state.generation_options
    music_source = gen_options.get("music_source", "None")

    if music_source == "AI Generated":
        from immich_memories.ui.pages._step4_music import apply_ai_music

        return await apply_ai_music(
            result_path,
            selected_clips,
            state.clip_segments,
            gen_options,
            config,
            run_output_dir,
            run_tracker,
            progress_bar,
            status_label,
            encoding_plan=encoding_plan,
            memory_type=state.memory_type,
        )
    elif music_source == "Upload file" and gen_options.get("music_file"):
        from immich_memories.ui.pages._step4_music import apply_uploaded_music

        return await apply_uploaded_music(
            result_path,
            gen_options,
            run_tracker,
            progress_bar,
            status_label,
            encoding_plan=encoding_plan,
            config=config,
        )
    from immich_memories.generate_music import MusicPhaseResult

    return MusicPhaseResult(applied=False)


async def _run_ui_music_phase(
    state,
    config,
    result_path: Path,
    selected_clips: list,
    run_output_dir: Path,
    progress_bar,
    status_label,
):
    """Apply legacy UI music requests without creating detached tracking state."""
    from immich_memories.generate_music import (
        MusicPhaseResult,
        derive_music_validation_plan,
        optional_music_warning,
    )

    music_source = state.generation_options.get("music_source", "None")
    if music_source not in {"AI Generated", "Upload file"}:
        return MusicPhaseResult(applied=False)
    try:
        encoding_plan = await io_bound_result(derive_music_validation_plan, result_path)
        return await _apply_music(
            state,
            config,
            result_path,
            selected_clips,
            run_output_dir,
            None,
            progress_bar,
            status_label,
            encoding_plan=encoding_plan,
        )
    except Exception as exc:  # WHY: an optional UI plan cannot invalidate the base artifact
        warning = optional_music_warning(exc, config)
        logger.warning(warning)
        ui.notify(f"{warning}. Video saved without music.", type="warning")
        return MusicPhaseResult(applied=False, warning=warning)


def _format_file_size(path: Path) -> str:
    """Format file size in human-readable form."""
    size_bytes = path.stat().st_size
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.1f} GB"
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.0f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def _show_output(output_container, result_path: Path, state) -> None:
    """Display the generated video with success state."""
    ui.notify("Video generated successfully!", type="positive")
    output_container.clear()
    with output_container:
        im_separator()

        # Success banner
        with (
            ui.element("div").classes("w-full rounded-lg p-4 im-alert-success"),
            ui.row().classes("items-center gap-3"),
        ):
            ui.icon("check_circle").classes("text-2xl").style("color: var(--im-success)")
            with ui.column().classes("gap-0"):
                ui.label("Your memory video is ready!").classes("text-base font-semibold").style(
                    "color: var(--im-success)"
                )
                if result_path.exists():
                    file_size = _format_file_size(result_path)
                    ui.label(f"Saved to: {result_path} ({file_size})").classes("text-sm").style(
                        "color: var(--im-text-secondary)"
                    )
                if state.generation_warning:
                    ui.label(state.generation_warning).classes("text-sm").style(
                        "color: var(--im-warning)"
                    )
                delivery_label = state.delivery_status.value.replace("_", " ").title()
                ui.label(f"Immich delivery: {delivery_label}").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )

        if result_path.exists():
            video_url = nicegui_app.add_media_file(local_file=result_path)
            video_wrapper = (
                ui.element("div")
                .classes("rounded-xl overflow-hidden mt-4")
                .style("background: var(--im-bg)")
            )
            with video_wrapper:
                ui.video(video_url).classes("w-full max-w-2xl").style(
                    "max-height: 60vh; object-fit: contain"
                )
            # Auto-scroll to the video player
            ui.run_javascript(
                "document.querySelector('.im-alert-success')"
                "?.scrollIntoView({behavior: 'smooth', block: 'start'})"
            )
