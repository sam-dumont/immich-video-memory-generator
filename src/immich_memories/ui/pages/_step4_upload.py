"""Upload-back-to-Immich controls for Step 4 export.

Provides toggle + album name input, and the async upload function.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from immich_memories.ui.nicegui_compat import io_bound_result

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams
    from immich_memories.tracking import RunMetadata, RunTracker

logger = logging.getLogger(__name__)


def _safe_delivery_message(message: str, params: GenerationParams) -> str:
    """Redact both labelled credentials and configured secret literals."""
    from immich_memories.security import configured_secret_values, sanitize_error_message

    safe_message = sanitize_error_message(message)
    for secret in configured_secret_values(params.config):
        safe_message = safe_message.replace(secret, "***")
    return safe_message


def _authoritative_delivery_run(run_tracker: RunTracker) -> RunMetadata | None:
    """Read delivery truth after an ambiguous transition without rewriting it."""
    try:
        return run_tracker.db.get_run(run_tracker.run_id)
    except Exception:  # WHY: presentation must not replace the primary delivery outcome
        logger.error("Could not inspect delivery lifecycle after upload")
        return run_tracker.current_run


def init_upload_state(state) -> None:
    """Initialize upload state from config defaults."""
    with contextlib.suppress(Exception):
        upload_config = state.config.upload
        if upload_config.enabled and not state.upload_enabled:
            state.upload_enabled = True
        if upload_config.album_name and state.upload_album_name == "Memories":
            state.upload_album_name = upload_config.album_name


def render_upload_controls(state) -> None:
    """Render upload-to-Immich toggle and album name input.

    Args:
        state: AppState instance with upload_enabled and upload_album_name.
    """
    with ui.column().classes("w-full gap-3"):
        upload_switch = ui.switch("Upload after generation").bind_value(state, "upload_enabled")

        (
            ui.input("Album name", placeholder="Memories")
            .bind_value(state, "upload_album_name")
            .classes("w-full")
            .bind_visibility_from(upload_switch, "value")
        )

        with ui.element("div").bind_visibility_from(upload_switch, "value"):
            ui.label(
                "The generated video will be uploaded to your Immich instance "
                "and added to the specified album."
            ).classes("text-xs").style("color: var(--im-text-secondary)")


async def upload_to_immich(
    video_path: Path,
    state,
    params: GenerationParams,
    run_tracker: RunTracker,
    progress_bar: object,
    status_label: object,
) -> RunMetadata:
    """Upload the generated video to Immich.

    Args:
        video_path: Path to the generated video file.
        state: AppState with immich_url, immich_api_key, upload_album_name.
        progress_bar: NiceGUI progress bar element.
        status_label: NiceGUI status label element.
    """
    if not params.upload_enabled:
        current = run_tracker.current_run
        if current is None:
            raise RuntimeError("Run not started")
        return current

    status_label.set_text("Uploading to Immich...")
    progress_bar.value = 0.98

    try:
        from immich_memories.generate import deliver_completed_artifact

        result = await io_bound_result(deliver_completed_artifact, params, video_path, run_tracker)
        completed = _authoritative_delivery_run(run_tracker)
        if completed is None:  # pragma: no cover - delivery requires an owned completed run
            raise RuntimeError("Run disappeared after Immich delivery")
        state.upload_result = result
        state.delivery_status = completed.delivery_status
        try:
            ui.notify(
                f"Uploaded to Immich! Album: {params.upload_album}",
                type="positive",
            )
        except Exception:  # WHY: the success observer cannot redefine delivery truth
            logger.warning("Delivery success notification failed")
        logger.info("Upload complete: asset=%s", completed.immich_asset_id)
        return completed

    except Exception as exc:  # WHY: a completed artifact must remain visible and retryable
        current = _authoritative_delivery_run(run_tracker)
        if current is None:
            raise
        state.upload_result = None
        state.delivery_status = current.delivery_status
        logger.warning("Upload to Immich remains pending")
        try:
            ui.notify(_safe_delivery_message(str(exc), params), type="warning")
        except Exception:  # WHY: a warning observer cannot hide the retryable artifact
            logger.warning("Delivery failure notification failed")
        return current
