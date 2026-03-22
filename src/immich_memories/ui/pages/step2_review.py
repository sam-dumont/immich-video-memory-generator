"""Step 2: Clip Review page."""

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
from immich_memories.ui.pages.clip_grid import (
    _detect_duplicates,
    _group_clips_by_datetime,
    _render_clip_grid_paginated,
    _render_compact_grid_paginated,
    _update_duration_summary,
)
from immich_memories.ui.pages.clip_pipeline import (
    _render_pipeline_progress_ui,
    render_pipeline_summary,
)
from immich_memories.ui.pages.clip_review import _render_review_selected_clips
from immich_memories.ui.pages.step2_loading import (
    _load_clips,
    _render_cached_analysis_summary,
)
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)

# ============================================================================
# Main Step 2 Render Function
# ============================================================================


def _render_step2_header(state) -> bool:
    """Render Step 2 header: session guard, date range, cache init, clip loading.

    Returns True if rendering should stop (early return in caller).
    """
    # Guard: redirect to step 1 if state was lost
    if not state.date_range or not state.immich_url:
        im_info_card(
            "Session expired \u2014 please reconfigure. "
            "The server restarted and lost your settings.",
            variant="warning",
        )
        im_button(
            "Back to Configuration",
            variant="secondary",
            on_click=lambda: ui.navigate.to("/"),
            icon="arrow_back",
        )
        return True

    # Show current date range
    ui.label(str(state.date_range.description)).classes("text-sm mb-4").style(
        "color: var(--im-text-secondary)"
    )

    # Initialize caches if not done
    if state.analysis_cache is None:
        from immich_memories.cache import VideoAnalysisCache
        from immich_memories.config import get_config

        _cfg = get_config()
        state.analysis_cache = VideoAnalysisCache(db_path=_cfg.cache.database_path)

    if state.thumbnail_cache is None:
        from immich_memories.cache.thumbnail_cache import ThumbnailCache
        from immich_memories.config import get_config

        _cfg = get_config()
        state.thumbnail_cache = ThumbnailCache(cache_dir=_cfg.cache.cache_path / "thumbnails")

    # Load clips if not already loaded
    if not state.clips:
        _load_clips()
        return True

    clips = state.clips

    if not clips:
        im_info_card("No videos found for the selected criteria.", variant="warning")

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        im_button("Back to Configuration", variant="secondary", on_click=go_back, icon="arrow_back")
        return True

    # Check pipeline state
    if state.pipeline_running:
        _render_pipeline_progress_ui(clips)
        return True

    if state.pipeline_result:
        render_pipeline_summary(state.pipeline_result)

        with ui.row().classes("w-full gap-4 mt-4"):

            def review_clips():
                state.pipeline_result = None
                state.review_selected_mode = True
                ui.navigate.to("/step2")

            def start_over():
                state.pipeline_result = None
                state.review_selected_mode = False
                state.selected_clip_ids = set()
                state.clip_segments = {}
                ui.navigate.to("/step2")

            im_button(
                "Review & Refine Selected Clips",
                variant="primary",
                on_click=review_clips,
                icon="edit",
            )
            im_button(
                "Start Over (Select Different Clips)",
                variant="secondary",
                on_click=start_over,
                icon="refresh",
            )
        return True

    # Check if in review mode
    if state.review_selected_mode and state.selected_clip_ids:
        _render_review_selected_clips(clips)
        return True

    return False


def _render_duration_and_clip_row(state, clips: list[VideoClipInfo]) -> int:
    """Render duration/clips-needed inputs. Returns clips_needed."""
    hdr_count = sum(1 for c in clips if c.is_hdr)
    fav_count = sum(1 for c in clips if c.asset.is_favorite)

    clips_needed = max(1, int((state.target_duration * 60) / state.avg_clip_duration))

    with ui.row().classes("w-full gap-4 items-end"):
        target_duration_input = ui.number(
            "Target duration (min)", value=state.target_duration, min=1, max=60
        ).classes("w-40")

        def on_target_change(e):
            state.target_duration = int(e.value if hasattr(e, "value") else e)

        target_duration_input.on_value_change(on_target_change)

        avg_clip_input = ui.number(
            "Avg seconds per clip", value=state.avg_clip_duration, min=2, max=30
        ).classes("w-40")

        def on_avg_change(e):
            state.avg_clip_duration = int(e.value if hasattr(e, "value") else e)

        avg_clip_input.on_value_change(on_avg_change)

        with ui.column().classes("items-center"):
            ui.label("Clips needed").classes("text-sm").style("color: var(--im-text-secondary)")
            ui.label(str(clips_needed)).classes("text-xl font-bold").style("color: var(--im-text)")

        ui.label(f"{len(clips)} clips ({hdr_count} HDR, {fav_count} favorites)").classes(
            "text-sm"
        ).style("color: var(--im-text-secondary)")

    return clips_needed


def _render_selection_options(state, hdr_count: int) -> None:
    """Render HDR / favorites / analyze-all checkboxes."""
    with ui.row().classes("w-full gap-6 mt-4"):
        hdr_checkbox = ui.checkbox("HDR clips only", value=state.hdr_only)

        def on_hdr_change(e):
            state.hdr_only = e.value if hasattr(e, "value") else e

        hdr_checkbox.on_value_change(on_hdr_change)

        fav_checkbox = ui.checkbox("Prioritize favorites", value=state.prioritize_favorites)

        def on_fav_change(e):
            state.prioritize_favorites = e.value if hasattr(e, "value") else e

        fav_checkbox.on_value_change(on_fav_change)

        analyze_checkbox = ui.checkbox("Analyze all videos", value=state.analyze_all)

        def on_analyze_change(e):
            state.analyze_all = e.value if hasattr(e, "value") else e

        analyze_checkbox.on_value_change(on_analyze_change)

    if state.hdr_only and hdr_count == 0:
        im_info_card(
            "No HDR clips found. Disable 'HDR clips only' to select clips.", variant="warning"
        )

    if state.prioritize_favorites:
        with ui.row().classes("w-full items-center gap-4 mt-2"):
            ui.label("Max non-favorites:").classes("text-sm")
            max_nonfav_slider = ui.slider(
                min=0, max=100, step=5, value=state.max_non_favorite_pct
            ).classes("w-64")

            def on_nonfav_change(e):
                value = e.value if hasattr(e, "value") else e
                state.max_non_favorite_pct = int(value)
                state.max_non_favorite_ratio = value / 100.0

            max_nonfav_slider.on_value_change(on_nonfav_change)
            ui.label(f"{state.max_non_favorite_pct}%").bind_text_from(
                max_nonfav_slider, "value", lambda v: f"{int(v)}%"
            )


def _render_step2_controls(state, clips: list[VideoClipInfo]) -> None:
    """Render the Generate Memories controls section."""
    im_section_header("Generate Memories", icon="auto_awesome")

    hdr_count = sum(1 for c in clips if c.is_hdr)
    clips_needed = _render_duration_and_clip_row(state, clips)
    _render_selection_options(state, hdr_count)

    total_available_duration = sum(c.duration_seconds or 0 for c in clips)
    target_duration_sec = state.target_duration * 60
    if target_duration_sec > total_available_duration:
        available_min = total_available_duration / 60
        im_info_card(
            f"Target ({state.target_duration} min) exceeds available content "
            f"({available_min:.1f} min). Consider reducing the target duration.",
            variant="warning",
        )

    ui.label(
        f"To fit {state.target_duration} minutes with ~{state.avg_clip_duration}s per clip, "
        f"you need approximately {clips_needed} clips."
    ).classes("text-sm mt-2").style("color: var(--im-text-secondary)")

    def start_generate():
        clips_needed_now = max(1, int((state.target_duration * 60) / state.avg_clip_duration))
        state.pipeline_running = True
        state.pipeline_config = {
            "target_clips": clips_needed_now,
            "avg_clip_duration": float(state.avg_clip_duration),
            "hdr_only": state.hdr_only,
            "prioritize_favorites": state.prioritize_favorites,
            "max_non_favorite_ratio": state.max_non_favorite_ratio,
            "analyze_all": state.analyze_all,
        }
        ui.navigate.to("/step2")

    im_button(
        "Generate Memories",
        variant="primary",
        on_click=start_generate,
        icon="auto_awesome",
    ).classes("w-full mt-4")

    im_separator()

    with ui.row().classes("w-full gap-2"):

        def select_all():
            state.selected_clip_ids = {c.asset.id for c in clips}
            ui.navigate.to("/step2")

        def deselect_all():
            state.selected_clip_ids = set()
            ui.navigate.to("/step2")

        def invert_selection():
            all_ids = {c.asset.id for c in clips}
            state.selected_clip_ids = all_ids - state.selected_clip_ids
            ui.navigate.to("/step2")

        im_button("Select All", variant="secondary", on_click=select_all)
        im_button("Deselect All", variant="secondary", on_click=deselect_all)
        im_button("Invert Selection", variant="secondary", on_click=invert_selection)

    im_separator()


def _auto_deselect_duplicates(state, lower_quality_ids: set[str]) -> None:
    """Deselect lower-quality duplicate clips if not already processed."""
    if not lower_quality_ids or state._duplicates_processed:
        return
    deselected_count = 0
    for asset_id in lower_quality_ids:
        if asset_id in state.selected_clip_ids:
            state.selected_clip_ids.discard(asset_id)
            deselected_count += 1
    state._duplicates_processed = True
    if deselected_count > 0:
        ui.notify(f"Auto-deselected {deselected_count} lower-quality duplicates", type="info")


def _make_lazy_loader(
    exp: ui.expansion,
    clips_list: list[VideoClipInfo],
    dup_ids: set[str],
    lq_ids: set[str],
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
                    _render_clip_grid_paginated(clips_list, dup_ids, lq_ids, summary_ctr)

    exp.on_value_change(on_expand)


def _render_period_expansions(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render one expansion panel per (year, month)."""
    import calendar as cal

    clips_by_period: dict[tuple[int, int], list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        dt = clip.asset.file_created_at
        clips_by_period[(dt.year, dt.month)].append(clip)

    span_years = len({ym[0] for ym in clips_by_period}) > 1

    for year_month in sorted(clips_by_period.keys()):
        period_clips = clips_by_period[year_month]
        year, month = year_month
        month_name = cal.month_name[month]
        label = (
            f"{month_name} {year} ({len(period_clips)} clips)"
            if span_years
            else f"{month_name} ({len(period_clips)} clips)"
        )
        expansion = ui.expansion(label, icon="calendar_month", value=False).classes("w-full")
        _make_lazy_loader(
            expansion, period_clips, duplicate_ids, lower_quality_ids, summary_container
        )


def _render_view_toggle(state) -> None:
    """Render Grid/List view toggle buttons."""
    is_grid = state.clip_view_mode == "grid"

    def set_view(mode: str):
        state.clip_view_mode = mode
        ui.navigate.to("/step2")

    with ui.row().classes("gap-1 mb-2"):
        grid_btn = ui.button(icon="grid_view", on_click=lambda: set_view("grid")).props(
            "dense flat" if not is_grid else "dense unelevated"
        )
        grid_btn.tooltip("Compact grid view")
        list_btn = ui.button(icon="view_list", on_click=lambda: set_view("list")).props(
            "dense flat" if is_grid else "dense unelevated"
        )
        list_btn.tooltip("Detailed list view")


def _render_step2_content(
    state,
    clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render the clip grid and navigation section."""
    im_section_header(f"{len(clips)} Videos Found", icon="video_library")

    duplicate_ids, lower_quality_ids = _detect_duplicates(clips)
    _auto_deselect_duplicates(state, lower_quality_ids)

    if duplicate_ids:
        clips_by_datetime = _group_clips_by_datetime(clips)
        num_duplicate_groups = len([g for g in clips_by_datetime.values() if len(g) > 1])
        im_info_card(
            f"Duplicate Detection: Found {num_duplicate_groups} duplicate groups "
            f"({len(lower_quality_ids)} lower-quality copies auto-deselected). "
            f"Best versions are marked with green check.",
            variant="info",
        )

    _render_view_toggle(state)

    if state.clip_view_mode == "grid":
        _render_compact_grid_paginated(clips, summary_container)
    else:
        _render_period_expansions(clips, duplicate_ids, lower_quality_ids, summary_container)

    im_separator()

    with ui.row().classes("w-full gap-4"):

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        def go_next():
            if state.selected_clip_ids:
                state.review_selected_mode = True
                ui.navigate.to("/step2")
            else:
                ui.notify("Please select at least one clip", type="warning")

        im_button("Back to Configuration", variant="secondary", on_click=go_back, icon="arrow_back")
        im_button("Next: Refine Moments", variant="primary", on_click=go_next, icon="arrow_forward")


def render_step2() -> None:
    """Render Step 2: Clip Review."""
    state = get_app_state()

    if _render_step2_header(state):
        return

    clips = state.clips

    summary_container = ui.row().classes("w-full mb-4")
    _update_duration_summary(clips, summary_container)

    im_separator()

    _render_cached_analysis_summary(clips)
    _render_step2_controls(state, clips)
    _render_step2_content(state, clips, summary_container)
