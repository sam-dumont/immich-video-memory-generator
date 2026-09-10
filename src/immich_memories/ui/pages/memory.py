"""The Memory page: the brief, the cut in progress, and what the cut produced.

One route, three states, decided by the session: a cut is running, a cut has
finished, or neither and the brief is shown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from nicegui import ui

from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning
from immich_memories.security import sanitize_error_message
from immich_memories.ui.components import im_button, im_separator
from immich_memories.ui.pages.clip_pipeline import (
    _render_pipeline_progress_ui,
    render_pipeline_summary,
)
from immich_memories.ui.pages.memory_brief import render_brief
from immich_memories.ui.pages.memory_story import render_story
from immich_memories.ui.pages.memory_story_data import StoryView, read_story_view
from immich_memories.ui.pages.step2_loading import ensure_caches, load_pool
from immich_memories.ui.pages.step2_review import _start_over_selection
from immich_memories.ui.state import get_app_state

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

logger = logging.getLogger(__name__)


def render_memory() -> None:
    """Render whichever of the three states the session is in."""
    state = get_app_state()
    if state.pipeline_running:
        _render_cutting(state)
    elif (result := state.pipeline_result) is not None:
        _render_cut_result(state, result)
    else:
        render_brief(state)


def _abandon_cut(state: AppState, message: str, *, kind: Literal["negative", "warning"]) -> None:
    state.pipeline_running = False
    ui.notify(message, type=kind)
    ui.navigate.to("/")


async def _load_pool_for_cut(state: AppState, container: ui.column) -> bool:
    """Load the brief's media into the session; False when the cut cannot go on."""
    with container:
        progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
        status = ui.label("Connecting to Immich...").classes("text-sm")
    try:
        await load_pool(state, status, progress)
    except Exception as exc:  # WHY: UI graceful degradation
        logger.exception("Loading the pool for a cut failed")
        _abandon_cut(
            state, f"Could not load media: {sanitize_error_message(str(exc))}", kind="negative"
        )
        return False
    if not state.clips and not state.photo_assets:
        _abandon_cut(state, "No media found for this brief.", kind="warning")
        return False
    return True


def _render_cutting(state: AppState) -> None:
    ensure_caches(state)
    container = ui.column().classes("w-full")

    async def begin() -> None:
        pool_loaded = bool(state.clips or state.photo_assets)
        if not pool_loaded and not await _load_pool_for_cut(state, container):
            return
        container.clear()
        with container:
            _render_pipeline_progress_ui(state.clips, return_route="/")

    ui.timer(0.1, begin, once=True)


def _recut(state: AppState) -> None:
    """Run the editor again over the same pool."""
    _start_over_selection(state)
    state.pipeline_running = True
    ui.navigate.to("/")


def _new_brief(state: AppState) -> None:
    state.reset_clips()
    ui.navigate.to("/")


def _story_view(state: AppState) -> StoryView | None:
    """The story the last cut wrote, with the final-cut render modes laid over it."""
    if state.editorial_attempt_dir is None:
        return None
    render_modes = {
        selection.asset_id: selection.render_mode
        for selection in state.editorial_selections
        if selection.render_mode
    }
    return read_story_view(state.editorial_attempt_dir, render_modes)


def _render_cut_result(state: AppState, result: dict) -> None:
    view = _story_view(state)
    if view is None:
        # A result that left no plan behind still has its counts to show.
        render_pipeline_summary(result)
    else:
        realization = result.get("stats", {}).get("editorial_duration_realization")
        render_story(view, warning=editorial_duration_warning(realization))
    im_separator()
    with ui.row().classes("w-full gap-4"):
        im_button(
            "Export", variant="primary", icon="movie", on_click=lambda: ui.navigate.to("/step4")
        )
        im_button(
            "Review the pool",
            variant="secondary",
            icon="video_library",
            on_click=lambda: ui.navigate.to("/step2"),
        )
        im_button("Cut again", variant="secondary", icon="refresh", on_click=lambda: _recut(state))
        im_button(
            "Change the brief", variant="ghost", icon="edit", on_click=lambda: _new_brief(state)
        )
