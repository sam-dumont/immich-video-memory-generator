"""Upload-back-to-Immich controls for Step 4 export.

Provides toggle + album name input, and the async upload function.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from nicegui import run, ui

from immich_memories.security import sanitize_error_message

logger = logging.getLogger(__name__)


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
    progress_bar: object,
    status_label: object,
) -> None:
    """Upload the generated video to Immich.

    Args:
        video_path: Path to the generated video file.
        state: AppState with immich_url, immich_api_key, upload_album_name.
        progress_bar: NiceGUI progress bar element.
        status_label: NiceGUI status label element.
    """
    if not state.upload_enabled:
        return

    status_label.set_text("Uploading to Immich...")
    progress_bar.value = 0.98

    try:
        from immich_memories.api.immich import SyncImmichClient

        client = SyncImmichClient(
            base_url=state.immich_url,
            api_key=state.immich_api_key,
        )

        album_name = state.upload_album_name or "Memories"

        result = await run.io_bound(
            client.upload_memory,
            video_path=video_path,
            album_name=album_name,
        )

        state.upload_result = result
        ui.notify(
            f"Uploaded to Immich! Album: {album_name}",
            type="positive",
        )
        logger.info(f"Upload complete: asset={result.get('asset_id')}, album={album_name}")

    except Exception as e:  # WHY: UI graceful degradation
        logger.warning(f"Upload to Immich failed: {e}")
        safe_msg = sanitize_error_message(str(e))
        ui.notify(f"Upload failed: {safe_msg}", type="warning")
