"""Rendering helpers for the pipeline progress UI (split from clip_pipeline.py)."""

from __future__ import annotations

import base64
import contextlib
import logging
from typing import Any

from nicegui import ui

from immich_memories.ui.components import im_badge
from immich_memories.ui.pages.step2_helpers import get_thumbnail

logger = logging.getLogger(__name__)

_AUDIO_CAT_COLORS = {
    "laughter": "--im-error",
    "baby": "--im-error",
    "speech": "--im-text-secondary",
    "singing": "--im-analysis",
    "music": "--im-analysis",
    "engine": "--im-warning",
    "nature": "--im-success",
    "crowd": "--im-warning",
    "animals": "--im-warning-text",
}


def _render_thumbnail_for(asset_id: str | None, _container: ui.element) -> None:
    """Render a thumbnail into a container (called from UI thread)."""
    if not asset_id:
        ui.element("div").classes("w-full rounded").style(
            "height: 180px; background: var(--im-bg-elevated)"
        )
        return
    thumb = get_thumbnail(asset_id)
    if thumb:
        b64 = base64.b64encode(thumb).decode()
        ui.image(f"data:image/jpeg;base64,{b64}").classes("rounded").style(
            "max-height: 180px; max-width: 100%; object-fit: contain"
        )
    else:
        ui.element("div").classes("w-full rounded").style(
            "height: 180px; background: var(--im-bg-elevated)"
        )


def _render_currently_analyzing_card(
    progress_state: dict[str, Any], detail_container: ui.element
) -> None:
    """Render the 'Currently Analyzing' card."""
    with ui.card().classes("flex-1 p-3").style("max-width: 600px"):
        ui.label("Currently Analyzing").classes("text-sm font-semibold mb-2").style(
            "color: var(--im-info)"
        )
        current_item = progress_state["current_item"]
        current_asset = progress_state["current_asset_id"]
        if current_item:
            _render_thumbnail_for(current_asset, detail_container)
            ui.label(current_item).classes("font-medium mt-2 truncate")
            ui.spinner(size="sm").classes("mt-1")
        else:
            ui.label("Waiting...").classes("italic").style("color: var(--im-text-muted)")


def _render_llm_badges(
    llm_emotion: str | None,
    llm_interest: float | None,
    llm_quality: float | None,
) -> None:
    """Render LLM analysis badges row."""
    if not (llm_emotion or llm_interest is not None):
        return
    badges: list[str] = []
    if llm_emotion:
        badges.append(llm_emotion)
    if llm_interest is not None:
        badges.append(f"interest: {llm_interest:.0%}")
    if llm_quality is not None:
        badges.append(f"quality: {llm_quality:.0%}")
    with ui.row().classes("gap-1 mt-1 flex-wrap"):
        for b in badges:
            im_badge(b, variant="analysis")


def _render_audio_categories(audio_cats: list[str] | None) -> None:
    """Render audio category badges."""
    if not audio_cats:
        return
    with ui.row().classes("gap-1 mt-1 flex-wrap"):
        ui.icon("hearing").classes("text-sm").style("color: var(--im-info)")
        for cat in audio_cats:
            css_var = _AUDIO_CAT_COLORS.get(cat, "--im-text-secondary")
            ui.badge(cat).classes("text-xs").style(
                f"background: color-mix(in srgb, var({css_var}) 20%, var(--im-bg-elevated)); "
                f"color: var({css_var})"
            )


def _render_segment_and_score(segment: tuple | None, score: float | None) -> None:
    """Render segment time and score rows."""
    if segment:
        start, end = segment
        seg_dur = end - start
        with ui.row().classes("gap-2 mt-2 items-center"):
            ui.icon("content_cut").classes("text-sm").style("color: var(--im-info)")
            ui.label(f"{start:.1f}s - {end:.1f}s ({seg_dur:.1f}s)").classes("text-sm")
    if score is not None:
        with ui.row().classes("gap-2 items-center"):
            ui.icon("star").classes("text-sm").style("color: var(--im-warning)")
            ui.label(f"Score: {score:.2f}").classes("text-sm")


def _try_render_video_preview(preview_path: str | None, _rendered_state: dict[str, Any]) -> bool:
    """Attempt to render a video preview. Returns True if successful."""
    if not preview_path:
        return False
    try:
        from nicegui import app as nicegui_app

        video_url = nicegui_app.add_media_file(local_file=preview_path)

        # Track URLs for cleanup but don't remove routes while they may still be
        # streaming — Starlette raises RuntimeError if a route is removed mid-request.
        prev_url = _rendered_state.get("prev_media_url")
        urls_to_clean = _rendered_state.setdefault("media_urls_to_clean", [])
        if prev_url and prev_url != video_url:
            urls_to_clean.append(prev_url)
        # Only clean up old URLs (2+ cycles stale)
        while len(urls_to_clean) > 3:
            old_url = urls_to_clean.pop(0)
            with contextlib.suppress(Exception):
                nicegui_app.remove_route(old_url)
        _rendered_state["prev_media_url"] = video_url

        ui.video(video_url).classes("rounded").props("muted autoplay loop").style(
            "max-height: 180px; max-width: 100%; object-fit: contain"
        )
        return True
    except Exception:  # WHY: UI graceful degradation
        return False


def _render_last_analyzed_card(
    progress_state: dict[str, Any],
    _rendered_state: dict[str, Any],
    detail_container: ui.element,
) -> None:
    """Render the 'Last Analyzed' card."""
    last_asset = progress_state["last_completed_asset_id"]
    if not last_asset:
        return

    with ui.card().classes("flex-1 p-3").style("max-width: 600px"):
        ui.label("Last Analyzed").classes("text-sm font-semibold mb-2").style(
            "color: var(--im-success)"
        )

        preview_path = progress_state["last_completed_video_path"]
        video_shown = _try_render_video_preview(preview_path, _rendered_state)
        if not video_shown:
            _render_thumbnail_for(last_asset, detail_container)

        _render_segment_and_score(
            progress_state["last_completed_segment"],
            progress_state["last_completed_score"],
        )

        llm_desc = progress_state["last_completed_llm_description"]
        if llm_desc:
            with ui.row().classes("gap-2 mt-1 items-start"):
                ui.icon("smart_toy").classes("text-sm mt-0.5").style("color: var(--im-analysis)")
                ui.label(llm_desc).classes("text-sm italic").style(
                    "color: var(--im-text-secondary)"
                )

        _render_llm_badges(
            progress_state["last_completed_llm_emotion"],
            progress_state["last_completed_llm_interestingness"],
            progress_state["last_completed_llm_quality"],
        )
        _render_audio_categories(progress_state["last_completed_audio_categories"])


def _poll_phase(
    progress_state: dict[str, Any],
    _rendered_state: dict[str, Any],
    phase_container: ui.element,
    render_phase_indicator_fn: Any,
) -> None:
    if _rendered_state["phase_number"] == progress_state["phase_number"]:
        return
    _rendered_state["phase_number"] = progress_state["phase_number"]
    phase_container.clear()
    with phase_container:
        render_phase_indicator_fn(progress_state["phase_number"], progress_state["total_phases"])


def _poll_stats(
    progress_state: dict[str, Any],
    stats_clips_label: Any,
    stats_elapsed_label: Any,
    stats_speed_label: Any,
    stats_avg_label: Any,
    stats_eta_label: Any,
    stats_errors_label: Any,
) -> None:
    idx = progress_state["current_index"]
    total = progress_state["total_items"]
    errors = progress_state["error_count"]
    speed = progress_state["speed_ratio"]
    avg = progress_state["avg_duration"]
    stats_clips_label.set_text(f"{idx}/{total} clips")
    stats_elapsed_label.set_text(f"Elapsed: {progress_state['elapsed']}")
    stats_speed_label.set_text(f"Speed: {speed:.1f}x realtime" if speed > 0 else "")
    stats_avg_label.set_text(f"~{avg:.1f}s/clip" if avg > 0 else "")
    stats_eta_label.set_text(f"ETA: {progress_state['eta']}")
    stats_errors_label.set_text(f"{errors} errors" if errors > 0 else "")


def _poll_detail_cards(
    progress_state: dict[str, Any],
    _rendered_state: dict[str, Any],
    detail_container: ui.element,
) -> None:
    current_changed = _rendered_state["current_asset_id"] != progress_state["current_asset_id"]
    last_changed = (
        _rendered_state["last_completed_asset_id"] != progress_state["last_completed_asset_id"]
    )
    if not (current_changed or last_changed):
        return
    _rendered_state["current_asset_id"] = progress_state["current_asset_id"]
    _rendered_state["last_completed_asset_id"] = progress_state["last_completed_asset_id"]
    detail_container.clear()
    with detail_container:
        _render_currently_analyzing_card(progress_state, detail_container)
        _render_last_analyzed_card(progress_state, _rendered_state, detail_container)
