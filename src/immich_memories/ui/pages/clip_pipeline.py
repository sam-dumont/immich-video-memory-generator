"""Pipeline execution UI for Step 2: Clip Review."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import run, ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.pages.clip_pipeline_helpers import (
    _poll_detail_cards,
    _poll_phase,
    _poll_stats,
)
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


class PipelineCancelled(Exception):
    """Raised when the user cancels the pipeline from the UI."""


def render_phase_indicator(current_phase: int, total_phases: int = 4) -> None:
    """Render pipeline phase indicator."""
    phase_labels = ["Clustering", "Filtering", "Analyzing", "Refining"]

    with ui.row().classes("w-full gap-4 justify-center mb-4"):
        for i in range(total_phases):
            phase_num = i + 1
            label = phase_labels[i] if i < len(phase_labels) else f"Phase {phase_num}"

            if phase_num < current_phase:
                ui.label(f"{phase_num}. {label}").classes("text-green-600")
            elif phase_num == current_phase:
                ui.label(f"{phase_num}. {label}").classes("text-blue-600 font-bold")
            else:
                ui.label(f"{phase_num}. {label}").classes("text-gray-400")


def render_pipeline_summary(result: dict) -> None:
    """Render pipeline completion summary."""
    stats = result.get("stats", {})
    errors = result.get("errors", [])

    selected_count = stats.get("selected_count", 0)
    total_analyzed = stats.get("total_analyzed", 0)
    error_count = stats.get("error_count", 0)
    elapsed = stats.get("elapsed_seconds", 0)

    with ui.card().classes("w-full p-4 bg-green-50"):
        ui.label(
            f"Pipeline complete! Selected {selected_count} clips from {total_analyzed} analyzed."
        ).classes("text-green-700 font-semibold")

        with ui.row().classes("w-full gap-8 mt-4"):
            with ui.column().classes("items-center"):
                ui.label("Clips Selected").classes("text-sm text-gray-500")
                ui.label(str(selected_count)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Clips Analyzed").classes("text-sm text-gray-500")
                ui.label(str(total_analyzed)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Time Elapsed").classes("text-sm text-gray-500")
                time_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"
                ui.label(time_str).classes("text-2xl font-bold")

        if error_count > 0:
            with ui.expansion(f"Errors ({error_count})", icon="warning").classes("mt-4"):
                for err in errors:
                    clip_id = err.get("clip_id", "Unknown")
                    error_msg = err.get("error", "Unknown error")
                    ui.label(f"{clip_id}: {error_msg}").classes("text-orange-600")


def _handle_pipeline_completion(
    progress_state: dict[str, Any],
    progress_timer: ui.timer,
    state: Any,
) -> None:
    """Handle pipeline done/error/cancel states."""
    progress_timer.deactivate()
    if progress_state.get("cancelled"):
        ui.notify("Pipeline cancelled", type="warning")
        state.pipeline_running = False
        ui.navigate.to("/step2")
    elif progress_state["error"]:
        ui.notify(f"Pipeline failed: {progress_state['error']}", type="negative")
        state.pipeline_running = False
    else:
        state.pipeline_running = False
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        asyncio.ensure_future(generate_title_after_pipeline(state))
        ui.navigate.to("/step2")


_PROGRESS_STATUS_KEYS = [
    "phase_number",
    "total_phases",
    "phase_label",
    "progress_fraction",
    "current_item",
    "current_asset_id",
    "current_index",
    "total_items",
    "elapsed",
    "eta",
    "avg_duration",
    "speed_ratio",
    "completed_count",
    "error_count",
    "last_completed_asset_id",
    "last_completed_segment",
    "last_completed_score",
    "last_completed_video_path",
    "last_completed_llm_description",
    "last_completed_llm_emotion",
    "last_completed_llm_interestingness",
    "last_completed_llm_quality",
    "last_completed_audio_categories",
]
_PROGRESS_DEFAULTS: dict[str, Any] = {
    "phase_number": 1,
    "total_phases": 4,
    "phase_label": "Processing",
    "progress_fraction": 0,
    "current_item": "",
    "current_index": 0,
    "total_items": 0,
    "elapsed": "0s",
    "eta": "--",
    "avg_duration": 0.0,
    "speed_ratio": 0.0,
    "completed_count": 0,
    "error_count": 0,
}


def _make_progress_callback(progress_state: dict[str, Any]) -> Any:
    """Return a progress callback that writes into the shared progress_state dict."""

    def on_progress(status: dict) -> None:
        if progress_state["cancelled"]:
            raise PipelineCancelled("Cancelled by user")
        for key in _PROGRESS_STATUS_KEYS:
            progress_state[key] = status.get(key, _PROGRESS_DEFAULTS.get(key))

    return on_progress


def _run_pipeline_blocking(
    state: Any, config: Any, clips: list[VideoClipInfo], progress_state: dict[str, Any]
) -> None:
    """Run the SmartPipeline in a background thread — no UI calls here."""
    from immich_memories.analysis.smart_pipeline import SmartPipeline

    tc = state.thumbnail_cache
    if tc is None:
        raise RuntimeError("Thumbnail cache not initialized")
    try:
        with SyncImmichClient(
            base_url=state.immich_url,
            api_key=state.immich_api_key,
        ) as client:
            from immich_memories.config import get_config

            app_config = get_config()
            pipeline = SmartPipeline(
                client=client,
                analysis_cache=state.analysis_cache,
                thumbnail_cache=tc,
                config=config,
                analysis_config=app_config.analysis,
                app_config=app_config,
            )
            result = pipeline.run(
                clips=clips,
                progress_callback=_make_progress_callback(progress_state),
            )
            state.pipeline_result = {
                "selected_clips": result.selected_clips,
                "clip_segments": result.clip_segments,
                "errors": result.errors,
                "stats": result.stats,
            }
            state.selected_clip_ids = {c.asset.id for c in result.selected_clips}
            state.clip_segments = result.clip_segments
            state.pipeline_running = False
            progress_state["done"] = True
    except PipelineCancelled:
        logger.info("Pipeline cancelled by user")
        state.pipeline_running = False
        progress_state["done"] = True
    except Exception as e:
        logger.exception("Pipeline error")
        state.pipeline_running = False
        progress_state["error"] = str(e)
        progress_state["done"] = True


def _detect_overnight_bases(state: Any) -> list | None:
    """Detect overnight stop bases for trip memories."""
    if not (state.memory_type == "trip" and state.clips):
        return None
    try:
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        trip_assets = [c.asset for c in state.clips]
        return detect_overnight_stops(trip_assets) or None
    except Exception:
        logger.debug("Trip segment detection failed", exc_info=True)
        return None


def _build_pipeline_config(state: Any) -> Any:
    """Build PipelineConfig from app state."""
    from immich_memories.analysis.smart_pipeline import PipelineConfig

    config_dict = state.pipeline_config
    overnight_bases = _detect_overnight_bases(state)
    return PipelineConfig(
        target_clips=config_dict.get("target_clips", 120),
        avg_clip_duration=config_dict.get("avg_clip_duration", 5.0),
        hdr_only=config_dict.get("hdr_only", False),
        prioritize_favorites=config_dict.get("prioritize_favorites", True),
        max_non_favorite_ratio=config_dict.get("max_non_favorite_ratio", 0.25),
        analyze_all=config_dict.get("analyze_all", False),
        overnight_bases=overnight_bases,
    )


def _wire_progress_timer(
    progress_state: dict[str, Any],
    _rendered_state: dict[str, Any],
    state: Any,
    phase_container: ui.element,
    progress_bar: Any,
    status_label: Any,
    detail_container: ui.element,
    stats_clips_label: Any,
    stats_elapsed_label: Any,
    stats_speed_label: Any,
    stats_avg_label: Any,
    stats_eta_label: Any,
    stats_errors_label: Any,
    clips: list[VideoClipInfo],
    config: Any,
) -> None:
    """Wire up the poll timer and background pipeline runner."""

    def poll_progress() -> None:
        _poll_phase(progress_state, _rendered_state, phase_container, render_phase_indicator)
        progress_bar.value = progress_state["progress_fraction"]
        _poll_stats(
            progress_state,
            stats_clips_label,
            stats_elapsed_label,
            stats_speed_label,
            stats_avg_label,
            stats_eta_label,
            stats_errors_label,
        )
        if not progress_state["cancelled"]:
            status_label.set_text(
                f"{progress_state['phase_label']}: {progress_state['current_item'] or '...'}"
            )
        _poll_detail_cards(progress_state, _rendered_state, detail_container)
        if progress_state["done"]:
            _handle_pipeline_completion(progress_state, progress_timer, state)

    progress_timer = ui.timer(1.0, poll_progress)

    async def start_pipeline() -> None:
        await run.io_bound(_run_pipeline_blocking, state, config, clips, progress_state)

    ui.timer(0.1, start_pipeline, once=True)


def _render_pipeline_progress_ui(clips: list[VideoClipInfo]) -> None:
    """Render pipeline progress UI."""
    state = get_app_state()
    config = _build_pipeline_config(state)

    ui.label("Generating Memories...").classes("text-2xl font-bold mb-4")

    # Progress UI elements
    phase_container = ui.column().classes("w-full mb-4")
    progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")

    # Stats row: persistent labels updated via set_text (avoids clear/rebuild churn)
    with ui.row().classes("w-full justify-between items-center text-sm text-gray-600 mt-1"):
        stats_clips_label = ui.label("0/0 clips").classes("font-medium")
        stats_elapsed_label = ui.label("Elapsed: 0s")
        stats_speed_label = ui.label("")
        stats_avg_label = ui.label("")
        stats_eta_label = ui.label("ETA: --").classes("font-medium")
        stats_errors_label = ui.label("").classes("text-orange-600")

    status_label = ui.label("Starting pipeline...")

    # Current clip + last analyzed side by side
    detail_container = ui.row().classes("w-full gap-6 mt-4")

    # Shared progress state — written by background thread, read by UI timer.
    # Simple dict assignments are thread-safe in CPython (GIL).
    progress_state: dict[str, Any] = {
        "phase_number": 0,
        "total_phases": 4,
        "phase_label": "Starting",
        "progress_fraction": 0.0,
        "current_item": "",
        "current_asset_id": None,
        "current_index": 0,
        "total_items": 0,
        "elapsed": "0s",
        "eta": "--",
        "avg_duration": 0.0,
        "speed_ratio": 0.0,
        "completed_count": 0,
        "error_count": 0,
        "last_completed_asset_id": None,
        "last_completed_segment": None,
        "last_completed_score": None,
        "last_completed_video_path": None,
        "last_completed_llm_description": None,
        "last_completed_llm_emotion": None,
        "last_completed_llm_interestingness": None,
        "last_completed_llm_quality": None,
        "last_completed_audio_categories": None,
        "done": False,
        "error": None,
        "cancelled": False,
    }

    # Track what's currently rendered so we only rebuild on actual changes
    _rendered_state: dict[str, Any] = {
        "current_asset_id": None,
        "last_completed_asset_id": None,
        "phase_number": -1,
        "prev_media_url": None,
    }

    cancel_btn = ui.button("Cancel Pipeline", icon="stop").props("color=red outline")

    def cancel_pipeline() -> None:
        progress_state["cancelled"] = True
        status_label.set_text("Cancelling... (waiting for current clip to finish)")
        cancel_btn.set_enabled(False)

    cancel_btn.on("click", cancel_pipeline)

    _wire_progress_timer(
        progress_state,
        _rendered_state,
        state,
        phase_container,
        progress_bar,
        status_label,
        detail_container,
        stats_clips_label,
        stats_elapsed_label,
        stats_speed_label,
        stats_avg_label,
        stats_eta_label,
        stats_errors_label,
        clips,
        config,
    )
