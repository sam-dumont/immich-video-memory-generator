"""The Memory page: the brief, the cut in progress, and what the cut produced.

One route, four states, decided by the session: a cut is running; a cut has
finished with its result in hand; a cut was armed but the session holds no
result for it (it finished, failed or was cut off while the page was away);
or neither, and the brief is shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning
from immich_memories.ui.components import im_button, im_info_card, im_separator
from immich_memories.ui.i18n import N_, tr
from immich_memories.ui.pages.clip_pipeline import render_pipeline_summary
from immich_memories.ui.pages.memory_brief import render_brief
from immich_memories.ui.pages.memory_run import (
    CUT_ALREADY_RUNNING,
    arm_cut,
    latest_attempt_of,
    render_cutting,
    render_reload_media,
)
from immich_memories.ui.pages.memory_story import render_story
from immich_memories.ui.pages.memory_story_data import StoryView, read_story_view
from immich_memories.ui.pages.memory_storyboard import (
    Storyboard,
    read_storyboard,
    render_storyboard,
)
from immich_memories.ui.pages.step2_review import reset_for_recut
from immich_memories.ui.state import get_app_state

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

_ENDINGS = {
    "cancelled": N_("The last cut was cancelled before it finished."),
    "failed": N_("The last cut failed; the server log has the reason."),
    "interrupted": N_("The last cut was interrupted: the process that ran it is gone."),
    "incomplete": N_("The last cut stopped without a result."),
}


def render_memory() -> None:
    """Render whichever of the four states the session is in."""
    state = get_app_state()
    if state.pipeline_running:
        render_cutting(state)
    elif (result := state.pipeline_result) is not None:
        _render_cut_result(state, result)
    elif (recovered := latest_attempt_of(state)) is not None:
        _render_recovered(state, recovered)
    else:
        render_brief(state)


def _recut(state: AppState) -> None:
    """Run the editor again over the same pool, with the owner's ticks."""
    if not arm_cut(state, before=lambda: reset_for_recut(state)):
        ui.notify(tr(CUT_ALREADY_RUNNING), type="warning")
        return
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


def _render_actions(
    state: AppState, *, recovered: tuple[Path, Mapping[str, Any]] | None = None
) -> None:
    """Export when the session holds the cut; re-load first when only the attempt does."""
    im_separator()
    with ui.row().classes("w-full gap-4"):
        if recovered is None:
            im_button(
                tr("Export"),
                variant="primary",
                icon="movie",
                on_click=lambda: ui.navigate.to("/step4"),
            )
        else:
            render_reload_media(state, *recovered)
        im_button(
            tr("Review the pool"),
            variant="secondary",
            icon="video_library",
            on_click=lambda: ui.navigate.to("/step2"),
        )
        im_button(
            tr("Cut again"), variant="secondary", icon="refresh", on_click=lambda: _recut(state)
        )
        im_button(
            tr("Change the brief"), variant="ghost", icon="edit", on_click=lambda: _new_brief(state)
        )


def _render_views(
    view: StoryView, board: Storyboard | None, warning: str | None, attempt_dir: Path | None
) -> None:
    """The storyboard first, since that is the video; the weighed story one tab away."""
    if board is None:
        render_story(view, warning=warning)
        return
    # The thesis once, above both readings of the same cut.
    ui.label(view.thesis or tr("The editor left no thesis for this cut.")).classes(
        "text-lg cut-thesis"
    ).style("color: var(--im-text)")
    with ui.tabs().classes("w-full") as tabs:
        storyboard_tab = ui.tab("Storyboard", icon="view_timeline", label=tr("Storyboard"))
        story_tab = ui.tab("Story", icon="auto_stories", label=tr("Story"))
    # No slide between the two readings of one cut: a screenshot mid-animation shows both.
    with ui.tab_panels(tabs, value=storyboard_tab, animated=False).classes("w-full cut-views"):
        with ui.tab_panel(storyboard_tab):
            render_storyboard(
                board,
                note=view.preparation,
                warning=warning,
                show_thesis=False,
                attempt_dir=attempt_dir,
            )
        with ui.tab_panel(story_tab):
            render_story(view, warning=warning, show_thesis=False)


def _render_cut_result(state: AppState, result: dict) -> None:
    view = _story_view(state)
    if view is None:
        # A result that left no plan behind still has its counts to show.
        render_pipeline_summary(result)
    else:
        realization = result.get("stats", {}).get("editorial_duration_realization")
        board = (
            read_storyboard(state.editorial_attempt_dir) if state.editorial_attempt_dir else None
        )
        _render_views(
            view, board, editorial_duration_warning(realization), state.editorial_attempt_dir
        )
    _render_actions(state)


def _render_recovered(state: AppState, record: Mapping[str, Any]) -> None:
    """A cut the session armed but holds no result for: read back what the attempt left."""
    status = str(record.get("status") or "")
    attempt_dir = Path(str(record.get("directory") or ""))
    if status != "complete":
        im_info_card(
            (
                tr(_ENDINGS[status])
                if status in _ENDINGS
                else tr("The last cut ended as {status}.", status=status)
            )
            + (tr(" Cut again runs it once more.")),
            variant="warning",
        )
        _render_actions(state, recovered=None)
        return
    view = read_story_view(attempt_dir)
    if view is not None:
        warning = editorial_duration_warning(record.get("duration_realization"))
        _render_views(view, read_storyboard(attempt_dir), warning, attempt_dir)
    im_info_card(
        tr("This cut finished while the page was away. Re-load its media to export it."),
        variant="info",
    )
    _render_actions(state, recovered=(attempt_dir, record))
