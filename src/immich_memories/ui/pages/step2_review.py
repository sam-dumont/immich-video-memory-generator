"""Step 2: Clip Review page."""

from __future__ import annotations

import logging
from collections import defaultdict

from nicegui import ui

from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.pages.clip_grid import (
    _detect_duplicates,
    _group_clips_by_datetime,
    _render_clip_grid_paginated,
    _update_duration_summary,
)
from immich_memories.ui.pages.clip_pipeline import (
    PipelineCancelled,
    _render_pipeline_progress_ui,
    render_pipeline_summary,
)
from immich_memories.ui.pages.clip_review import _render_review_selected_clips
from immich_memories.ui.pages.step2_helpers import (
    format_duration,
    get_thumbnail,
)
from immich_memories.ui.pages.step2_loading import (
    _load_clips,
    _render_cached_analysis_summary,
)
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "PipelineCancelled",
    "clip_quality_score",
    "format_duration",
    "get_thumbnail",
    "render_step2",
]


def clip_quality_score(c: VideoClipInfo) -> tuple[int, int, int, int]:
    """Score a clip for quality comparison. Higher is better."""
    from immich_memories.ui.pages.clip_grid import clip_quality_score as _cqs

    return _cqs(c)


# ============================================================================
# Main Step 2 Render Function
# ============================================================================


def _render_step2_header(state) -> bool:
    """Render Step 2 header: session guard, date range, cache init, clip loading.

    Returns True if rendering should stop (early return in caller).
    """
    # Guard: redirect to step 1 if state was lost (e.g. hot-reload restart)
    if not state.date_range or not state.immich_url:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("Session expired \u2014 please reconfigure.").classes(
                "text-yellow-700 font-medium"
            )
            ui.label("The server restarted and lost your settings.").classes(
                "text-yellow-600 text-sm"
            )
        ui.button("Back to Configuration", on_click=lambda: ui.navigate.to("/"), icon="arrow_back")
        return True

    # Show current date range
    ui.label(f"{state.date_range.description}").classes("text-sm text-gray-500 mb-4")

    # Initialize caches if not done
    if state.analysis_cache is None:
        from immich_memories.cache import VideoAnalysisCache

        state.analysis_cache = VideoAnalysisCache()

    if state.thumbnail_cache is None:
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        state.thumbnail_cache = ThumbnailCache()

    # Load clips if not already loaded
    if not state.clips:
        _load_clips()
        return True

    clips = state.clips

    if not clips:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("No videos found for the selected criteria.").classes("text-yellow-700")

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        ui.button("Back to Configuration", on_click=go_back, icon="arrow_back")
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

            ui.button(
                "Review & Refine Selected Clips",
                on_click=review_clips,
                icon="edit",
            ).props("color=primary")
            ui.button("Start Over (Select Different Clips)", on_click=start_over, icon="refresh")
        return True

    # Check if in review mode (showing only selected clips)
    if state.review_selected_mode and state.selected_clip_ids:
        _render_review_selected_clips(clips)
        return True

    return False


def _render_step2_controls(state, clips: list[VideoClipInfo]) -> None:
    """Render the Generate Memories controls section."""
    ui.label("Generate Memories").classes("text-xl font-semibold mt-4")

    hdr_count = sum(1 for c in clips if c.is_hdr)
    fav_count = sum(1 for c in clips if c.asset.is_favorite)

    # Target duration and clips calculation
    with ui.row().classes("w-full gap-4 items-end"):
        target_duration_input = ui.number(
            "Target duration (min)",
            value=state.target_duration,
            min=1,
            max=60,
        ).classes("w-40")

        def on_target_change(e):
            state.target_duration = int(e.value if hasattr(e, "value") else e)

        target_duration_input.on_value_change(on_target_change)

        avg_clip_input = ui.number(
            "Avg seconds per clip",
            value=state.avg_clip_duration,
            min=2,
            max=30,
        ).classes("w-40")

        def on_avg_change(e):
            state.avg_clip_duration = int(e.value if hasattr(e, "value") else e)

        avg_clip_input.on_value_change(on_avg_change)

        clips_needed = max(1, int((state.target_duration * 60) / state.avg_clip_duration))

        with ui.column().classes("items-center"):
            ui.label("Clips needed").classes("text-sm text-gray-500")
            ui.label(str(clips_needed)).classes("text-xl font-bold")

        ui.label(f"{len(clips)} clips ({hdr_count} HDR, {fav_count} favorites)").classes(
            "text-sm text-gray-500"
        )

    # Selection options
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

    # Warning for HDR with no HDR clips
    if state.hdr_only and hdr_count == 0:
        ui.label("No HDR clips found. Disable 'HDR clips only' to select clips.").classes(
            "text-orange-600"
        )

    # Non-favorite ratio slider (only if prioritize_favorites)
    if state.prioritize_favorites:
        with ui.row().classes("w-full items-center gap-4 mt-2"):
            ui.label("Max non-favorites:").classes("text-sm")
            max_nonfav_slider = ui.slider(
                min=0,
                max=100,
                step=5,
                value=state.max_non_favorite_pct,
            ).classes("w-64")

            def on_nonfav_change(e):
                value = e.value if hasattr(e, "value") else e
                state.max_non_favorite_pct = int(value)
                state.max_non_favorite_ratio = value / 100.0

            max_nonfav_slider.on_value_change(on_nonfav_change)
            ui.label(f"{state.max_non_favorite_pct}%").bind_text_from(
                max_nonfav_slider, "value", lambda v: f"{int(v)}%"
            )

    # Calculate total available duration
    total_available_duration = sum(c.duration_seconds or 0 for c in clips)
    target_duration_sec = state.target_duration * 60
    if target_duration_sec > total_available_duration:
        available_min = total_available_duration / 60
        ui.label(
            f"Target ({state.target_duration} min) exceeds available content "
            f"({available_min:.1f} min). Consider reducing the target duration."
        ).classes("text-orange-600")

    ui.label(
        f"To fit {state.target_duration} minutes with ~{state.avg_clip_duration}s per clip, "
        f"you need approximately {clips_needed} clips."
    ).classes("text-sm text-gray-500 mt-2")

    # Generate button
    def start_generate():
        clips_needed = max(1, int((state.target_duration * 60) / state.avg_clip_duration))
        state.pipeline_running = True
        state.pipeline_config = {
            "target_clips": clips_needed,
            "avg_clip_duration": float(state.avg_clip_duration),
            "hdr_only": state.hdr_only,
            "prioritize_favorites": state.prioritize_favorites,
            "max_non_favorite_ratio": state.max_non_favorite_ratio,
            "analyze_all": state.analyze_all,
        }
        ui.navigate.to("/step2")

    ui.button(
        "Generate Memories",
        on_click=start_generate,
        icon="auto_awesome",
    ).props("color=primary").classes("w-full mt-4")

    ui.separator().classes("my-4")

    # Bulk actions
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

        ui.button("Select All", on_click=select_all).props("outline")
        ui.button("Deselect All", on_click=deselect_all).props("outline")
        ui.button("Invert Selection", on_click=invert_selection).props("outline")

    ui.separator().classes("my-4")


def _render_step2_content(
    state,
    clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render the clip grid and navigation section."""
    ui.label(f"{len(clips)} Videos Found").classes("text-xl font-semibold")

    # Detect duplicates
    duplicate_ids, lower_quality_ids = _detect_duplicates(clips)

    # Auto-deselect lower quality duplicates on first load
    if lower_quality_ids and not state._duplicates_processed:
        deselected_count = 0
        for asset_id in lower_quality_ids:
            if asset_id in state.selected_clip_ids:
                state.selected_clip_ids.discard(asset_id)
                deselected_count += 1
        state._duplicates_processed = True
        if deselected_count > 0:
            ui.notify(
                f"Auto-deselected {deselected_count} lower-quality duplicates",
                type="info",
            )

    # Show duplicate summary
    if duplicate_ids:
        clips_by_datetime = _group_clips_by_datetime(clips)
        num_duplicate_groups = len([g for g in clips_by_datetime.values() if len(g) > 1])
        with ui.card().classes("w-full p-2 bg-blue-50 mb-4"):
            ui.label(
                f"Duplicate Detection: Found {num_duplicate_groups} duplicate groups "
                f"({len(lower_quality_ids)} lower-quality copies auto-deselected). "
                f"Best versions are marked with green check."
            ).classes("text-blue-700 text-sm")

    # Group clips by month
    clips_by_month: dict[int, list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        month = clip.asset.file_created_at.month
        clips_by_month[month].append(clip)

    month_names = {
        1: "January",
        2: "February",
        3: "March",
        4: "April",
        5: "May",
        6: "June",
        7: "July",
        8: "August",
        9: "September",
        10: "October",
        11: "November",
        12: "December",
    }

    # Render clip grid by month (lazy-loaded, collapsed by default)
    for month in sorted(clips_by_month.keys()):
        month_clips = clips_by_month[month]
        expansion = ui.expansion(
            f"{month_names[month]} ({len(month_clips)} clips)",
            icon="calendar_month",
            value=False,
        ).classes("w-full")

        # Lazy rendering: only create clip cards when the expansion is first opened
        def _make_lazy_loader(
            exp: ui.expansion,
            clips_list: list[VideoClipInfo],
            dup_ids: set[str],
            lq_ids: set[str],
            summary_ctr: ui.element,
        ) -> None:
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
                            _render_clip_grid_paginated(
                                clips_list,
                                dup_ids,
                                lq_ids,
                                summary_ctr,
                            )

            exp.on_value_change(on_expand)

        _make_lazy_loader(
            expansion, month_clips, duplicate_ids, lower_quality_ids, summary_container
        )

    ui.separator().classes("my-4")

    # Navigation
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

        ui.button("Back to Configuration", on_click=go_back, icon="arrow_back").props("outline")
        ui.button(
            "Next: Refine Moments",
            on_click=go_next,
            icon="arrow_forward",
        ).props("color=primary")


def render_step2() -> None:
    """Render Step 2: Clip Review."""
    state = get_app_state()

    if _render_step2_header(state):
        return

    clips = state.clips

    # ========================================================================
    # Main Clip Review UI
    # ========================================================================

    # Summary section
    summary_container = ui.row().classes("w-full mb-4")
    _update_duration_summary(clips, summary_container)

    ui.separator()

    # Show cached analysis summary
    _render_cached_analysis_summary(clips)

    # ========================================================================
    # Generate Memories Section
    # ========================================================================
    _render_step2_controls(state, clips)

    # ========================================================================
    # Clip Grid
    # ========================================================================
    _render_step2_content(state, clips, summary_container)
