"""Step 2: the media pool page.

Everything the brief found, ticked or not, with one counters line and one primary
action. Before a cut, an untick is an exclusion; after a cut the ticks are the
owner's instructions and "Cut again" applies them.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from nicegui import ui

from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.components import (
    im_button,
    im_info_card,
    im_section_header,
    im_separator,
)
from immich_memories.ui.i18n import tr
from immich_memories.ui.pages.clip_grid import (
    GridItem,
    _render_clip_grid,
    _render_compact_grid,
    _render_compact_mixed_grid,
    _render_mixed_grid,
    _render_paginated,
    _update_duration_summary,
    grid_item_date,
)
from immich_memories.ui.pages.clip_review import _render_review_selected_clips
from immich_memories.ui.pages.memory_run import CUT_ALREADY_RUNNING, arm_cut
from immich_memories.ui.pages.step2_helpers import review_candidates, tick_explanation
from immich_memories.ui.pages.step2_loading import (
    _load_clips,
    _set_initial_selection,
    ensure_caches,
)
from immich_memories.ui.state import AppState, get_app_state

logger = logging.getLogger(__name__)


def _start_over_selection(state: AppState) -> None:
    """Discard one editorial result while keeping the loaded source library."""
    _forget_result(state)
    state.selected_clip_ids = set()
    state.previous_cut_asset_ids = None


def reset_for_recut(state: AppState) -> None:
    """Discard the result but keep the ticks: they are the owner's instructions for the next cut.

    A pool nobody ticked (a recovered session, a cleared selection) is ticked whole again,
    so "Cut again" never runs over an empty pool (#778).
    """
    _forget_result(state)
    if not state.selected_clip_ids and not state.selected_photo_ids:
        _set_initial_selection(state.clips, state)
        state.selected_photo_ids = {asset.id for asset in state.photo_assets}


def _forget_result(state: AppState) -> None:
    state.pipeline_result = None
    state.pipeline_selected_clips = []
    state.editorial_selections = ()
    state.editorial_attempt_dir = None
    state.review_selected_mode = False
    state.clip_segments = {}


def _render_step2_header(state) -> bool:
    """Session guard, cache init and clip loading. Returns True when the caller must stop."""
    if not state.scope_is_selected or not state.immich_url:
        im_info_card(
            tr(
                "Session expired — please reconfigure. The server restarted and lost your settings."
            ),
            variant="warning",
        )
        im_button(
            tr("Back to the brief"),
            variant="secondary",
            on_click=lambda: ui.navigate.to("/"),
            icon="arrow_back",
        )
        return True

    ensure_caches(state)

    if not state.clips:
        _load_clips()
        return True

    if not state.clips:
        im_info_card(tr("No videos found for the selected criteria."), variant="warning")

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        im_button(tr("Back to the brief"), variant="secondary", on_click=go_back, icon="arrow_back")
        return True

    # A running cut lives on the Memory page, whatever page armed it.
    if state.pipeline_running:
        ui.navigate.to("/")
        return True

    if state.review_selected_mode and state.selected_clip_ids:
        _render_review_selected_clips(state.get_selected_clips())
        return True

    return False


def _render_pool_actions(state) -> None:
    """One primary action. Before a cut it is Cut; after one it is Cut again."""
    has_result = state.pipeline_result is not None

    def cut() -> None:
        before = (lambda: reset_for_recut(state)) if has_result else None
        if not arm_cut(state, before=before):
            ui.notify(CUT_ALREADY_RUNNING, type="warning")
            return
        ui.navigate.to("/")

    def trim_clips() -> None:
        # The result stays: trimming edits the cut, it does not discard it.
        state.review_selected_mode = True
        ui.navigate.to("/step2")

    def start_over() -> None:
        _start_over_selection(state)
        ui.navigate.to("/step2")

    ui.label(tick_explanation(has_result=has_result)).classes("text-sm tick-explanation").style(
        "color: var(--im-text-secondary)"
    )
    with ui.row().classes("w-full gap-4 mt-2"):
        if has_result:
            im_button(tr("Cut again"), variant="primary", on_click=cut, icon="refresh")
            if review_candidates(state.get_selected_clips()):
                im_button(
                    tr("Trim the video clips"),
                    variant="secondary",
                    on_click=trim_clips,
                    icon="edit",
                )
            im_button(tr("Start over"), variant="ghost", on_click=start_over, icon="restart_alt")
        else:
            im_button(tr("Cut"), variant="primary", on_click=cut, icon="auto_awesome")


def _make_lazy_loader(
    exp: ui.expansion,
    clips_list: list[VideoClipInfo],
    summary_ctr: ui.element,
) -> None:
    """Wire a lazy-load handler onto an expansion panel."""
    loaded = False
    container = None

    def on_expand(e):
        nonlocal loaded, container
        val = e.value if hasattr(e, "value") else e
        if val and not loaded:
            loaded = True
            with exp:
                container = ui.column().classes("w-full")
                with container:
                    _render_paginated(
                        clips_list, lambda page: _render_clip_grid(list(page), summary_ctr)
                    )

    exp.on_value_change(on_expand)


def _render_period_expansions(
    clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render one expansion panel per (year, month)."""
    import calendar as cal

    clips_by_period: dict[tuple[int, int], list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        dt = clip.asset.file_created_at
        clips_by_period[(dt.year, dt.month)].append(clip)

    span_years = len({ym[0] for ym in clips_by_period}) > 1

    sorted_periods = sorted(clips_by_period.keys())
    for idx, year_month in enumerate(sorted_periods):
        period_clips = clips_by_period[year_month]
        year, month = year_month
        month_name = cal.month_name[month]
        label = (
            f"{month_name} {year} ({len(period_clips)} clips)"
            if span_years
            else f"{month_name} ({len(period_clips)} clips)"
        )
        is_first = idx == 0
        expansion = ui.expansion(label, icon="calendar_month", value=is_first).classes("w-full")
        if is_first:
            # Render first month eagerly (lazy loader won't fire for initial value=True)
            with expansion:
                _render_paginated(
                    period_clips, lambda page: _render_clip_grid(list(page), summary_container)
                )
        else:
            _make_lazy_loader(expansion, period_clips, summary_container)


def _render_view_toggle(state) -> None:
    """Render Grid/List view toggle buttons."""
    is_grid = state.clip_view_mode == "grid"

    def set_view(mode: str):
        state.clip_view_mode = mode
        ui.navigate.to("/step2")

    with ui.row().classes("gap-1"):
        grid_btn = ui.button(icon="grid_view", on_click=lambda: set_view("grid")).props(
            "dense flat" if not is_grid else "dense unelevated"
        )
        grid_btn.tooltip(tr("Compact grid view"))
        list_btn = ui.button(icon="view_list", on_click=lambda: set_view("list")).props(
            "dense flat" if is_grid else "dense unelevated"
        )
        list_btn.tooltip(tr("Detailed list view"))


def _build_mixed_items(clips: list[VideoClipInfo], state) -> list[GridItem]:
    """Merge clips and photos into a single chronologically sorted list."""
    if not state.include_photos or not state.photo_assets:
        return clips.copy()  # type: ignore[return-value]
    items: list[GridItem] = [*clips, *state.photo_assets]
    items.sort(key=grid_item_date)
    return items


def _render_step2_content(
    state,
    clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """The grid or the list: photos and videos in one chronological pool."""
    period = str(state.date_range.description) if state.date_range else ""
    im_section_header(
        tr("The pool{value}", value=": " + period if period else ""), icon="video_library"
    )

    with ui.row().classes("w-full items-center gap-2 mb-2"):
        ui.element("div").classes("flex-grow")
        _render_view_toggle(state)

    has_photos = state.include_photos and bool(state.photo_assets)

    if has_photos:
        mixed_items = _build_mixed_items(clips, state)
        if state.clip_view_mode == "grid":
            _render_paginated(
                mixed_items, lambda page: _render_compact_mixed_grid(list(page), summary_container)
            )
        else:
            _render_paginated(
                mixed_items, lambda page: _render_mixed_grid(list(page), summary_container)
            )
    elif state.clip_view_mode == "grid":
        _render_paginated(clips, lambda page: _render_compact_grid(list(page), summary_container))
    else:
        _render_period_expansions(clips, summary_container)


def _render_step2_nav(state) -> None:
    """Render navigation buttons at the bottom."""
    im_separator()
    with ui.row().classes("w-full gap-4"):

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        im_button(tr("Back to the brief"), variant="secondary", on_click=go_back, icon="arrow_back")
        if state.pipeline_result is not None:
            im_button(
                tr("Back to the cut"),
                variant="ghost",
                on_click=lambda: ui.navigate.to("/"),
                icon="view_timeline",
            )


def render_step2() -> None:
    """Render the media pool page."""
    state = get_app_state()

    if _render_step2_header(state):
        return

    clips = state.clips

    # One counters line; the grids redraw it on every tick.
    summary_container = ui.element("div").classes("w-full mb-1")
    _update_duration_summary(clips, summary_container)
    _render_pool_actions(state)
    _render_step2_content(state, clips, summary_container)
    _render_step2_nav(state)
