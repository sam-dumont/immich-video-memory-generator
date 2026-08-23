"""Video generation logic for Step 4 export.

Thin UI wrapper around generate_memory() — builds GenerationParams
from AppState, delegates pipeline work, then shows output.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

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
from immich_memories.ui.pages.step3_options import SCALE_MODE_OPTIONS, resolve_scale_mode_label

if TYPE_CHECKING:
    pass

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
    run_id: str | None = None,
) -> bool:
    """Render completed-artifact facts; say where the result went if the client is gone."""

    def write() -> None:
        run_id_label.set_text(f"Output: {result_path.parent.name}")
        progress_bar.value = 1.0
        status_label.set_text("Complete!")
        cancel_btn.set_visibility(False)
        _show_output(output_container, result_path, state)

    shown = run_ui_observer(write, description="generation completion UI")
    if not shown:
        logger.info(
            "Run %s finished (%s) but the browser had disconnected; reload Step 4 to see the result",
            run_id,
            result_path,
        )
    return shown


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
# Music sources that produce audio. "None" is the fourth option and means silence.
_SOURCES_WITH_AUDIO = frozenset({"AI Generated", "Upload file", "Bundled"})


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
        scale_mode=SCALE_MODE_OPTIONS[
            resolve_scale_mode_label(state.config, gen_options.get("scale_mode"))
        ],
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
        target_duration_seconds=state.target_duration_seconds,
        timeline_plan=state.timeline_plan,
        selected_photo_ids=None,
        # Music runs in the shared phase after assembly, on the same run
        # lifecycle; "None" stays silent because resolve_music checks no_music
        # before anything else.
        music_path=_uploaded_music_path(gen_options, output_path.parent),
        no_music=gen_options.get("music_source", "None") not in _SOURCES_WITH_AUDIO,
        music_volume=gen_options.get("music_volume", 0.5),
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

        run_id = generate_run_id()
        run_tracker = RunTracker(run_id, db_path=params.config.cache.database_path)
        state.active_run_id = run_id
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
            run_id=run_id,
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


def _uploaded_music_path(gen_options: dict, run_output_dir: Path) -> Path | None:
    """Materialise an uploaded track so the shared music phase can resolve it.

    The wizard holds the upload as bytes; `resolve_music` takes a path and
    checks it exists. Written beside the run rather than into a temp file so it
    is cleaned up with everything else the run produced.
    """
    if gen_options.get("music_source") != "Upload file":
        return None
    data = gen_options.get("music_file")
    if not data:
        return None
    run_output_dir.mkdir(parents=True, exist_ok=True)
    path = run_output_dir / "uploaded_music.mp3"
    path.write_bytes(data)
    return path


async def finalize_ui_generation(
    state,
    params,
    prepared,
    run_tracker,
    progress_bar,
    status_label,
):
    """Finish UI-managed post-processing on the caller-owned run."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.generate_progress import emit_operational_phase
    from immich_memories.operations.phases import OperationalPhase

    music_result = MusicPhaseResult(applied=False)
    music_source = state.generation_options.get("music_source", "None")
    emit_operational_phase(
        params,
        run_tracker,
        OperationalPhase.MUSIC,
        current=0,
        total=1 if music_source in _SOURCES_WITH_AUDIO else 0,
        message=("Applying music" if music_source in _SOURCES_WITH_AUDIO else "Music disabled"),
    )
    if music_source in _SOURCES_WITH_AUDIO:
        from immich_memories.generate_music import MusicSource
        from immich_memories.generate_settings import _run_music_phase

        music_result = await io_bound_result(
            _run_music_phase,
            params,
            list(prepared.assembly_clips),
            prepared.path,
            prepared.path.parent,
            run_tracker,
            encoding_plan=prepared.encoding_plan,
            mute_windows=prepared.music_mute_windows,
            source=(MusicSource.BUNDLED if music_source == "Bundled" else MusicSource.AUTO),
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
    from immich_memories.generate_timeline import validate_final_duration

    duration_warning = validate_final_duration(params, final_probe.duration_seconds)
    warnings = [w for w in (duration_warning, music_result.warning) if w]
    completed = run_tracker.complete_artifact(
        prepared.path,
        final_probe,
        warnings,
        delivery_requested=params.upload_enabled,
        delivery_album=params.upload_album,
        clips_analyzed=prepared.clips_analyzed,
        clips_selected=prepared.clips_selected,
    )
    state.generation_warning = music_result.warning or duration_warning
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
            video_wrapper = (
                ui.element("div")
                .classes("rounded-xl overflow-hidden mt-4")
                .style("background: var(--im-bg)")
            )
            with video_wrapper:
                ui.video(result_path).classes("w-full max-w-2xl").style(
                    "max-height: 60vh; object-fit: contain"
                )
            # Auto-scroll to the video player
            ui.run_javascript(
                "document.querySelector('.im-alert-success')"
                "?.scrollIntoView({behavior: 'smooth', block: 'start'})"
            )
