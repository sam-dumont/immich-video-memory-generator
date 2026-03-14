"""Pipeline execution UI for Step 2: Clip Review."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any

from nicegui import run, ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.pages.step2_helpers import get_thumbnail
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


def _render_pipeline_progress_ui(clips: list[VideoClipInfo]) -> None:
    """Render pipeline progress UI."""
    from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

    state = get_app_state()

    ui.label("Generating Memories...").classes("text-2xl font-bold mb-4")

    config_dict = state.pipeline_config
    config = PipelineConfig(
        target_clips=config_dict.get("target_clips", 120),
        avg_clip_duration=config_dict.get("avg_clip_duration", 5.0),
        hdr_only=config_dict.get("hdr_only", False),
        prioritize_favorites=config_dict.get("prioritize_favorites", True),
        max_non_favorite_ratio=config_dict.get("max_non_favorite_ratio", 0.25),
        analyze_all=config_dict.get("analyze_all", False),
    )

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
        # Last completed clip details
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

    # Cancel button
    def cancel_pipeline() -> None:
        progress_state["cancelled"] = True
        status_label.set_text("Cancelling... (waiting for current clip to finish)")
        cancel_btn.set_enabled(False)

    cancel_btn = ui.button(
        "Cancel Pipeline",
        on_click=cancel_pipeline,
        icon="stop",
    ).props("color=red outline")

    def _render_thumbnail_for(asset_id: str | None, container: ui.element) -> None:
        """Render a thumbnail into a container (called from UI thread)."""
        if not asset_id:
            ui.element("div").classes(
                "w-full h-32 bg-gray-200 rounded flex items-center justify-center"
            )
            return
        thumb = get_thumbnail(asset_id)
        if thumb:
            b64 = base64.b64encode(thumb).decode()
            ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-32 object-cover rounded")
        else:
            with ui.element("div").classes(
                "w-full h-32 bg-gray-200 rounded flex items-center justify-center"
            ):
                ui.icon("videocam", color="gray").classes("text-3xl")

    # Track what's currently rendered so we only rebuild on actual changes
    _rendered_state: dict[str, Any] = {
        "current_asset_id": None,
        "last_completed_asset_id": None,
        "phase_number": -1,
        "prev_media_url": None,  # Track previous media route for cleanup
    }

    def poll_progress() -> None:
        """Periodic UI update from shared progress state (runs on event loop)."""
        # Phase indicator — only rebuild when phase changes
        if _rendered_state["phase_number"] != progress_state["phase_number"]:
            _rendered_state["phase_number"] = progress_state["phase_number"]
            phase_container.clear()
            with phase_container:
                render_phase_indicator(
                    progress_state["phase_number"],
                    progress_state["total_phases"],
                )

        # Progress bar — lightweight update
        progress_bar.value = progress_state["progress_fraction"]

        # Stats row — update existing labels in-place (no clear/rebuild churn)
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

        # Status text
        if not progress_state["cancelled"]:
            status_label.set_text(
                f"{progress_state['phase_label']}: {progress_state['current_item'] or '...'}"
            )

        # Detail cards — only rebuild when the displayed clip changes
        current_changed = _rendered_state["current_asset_id"] != progress_state["current_asset_id"]
        last_changed = (
            _rendered_state["last_completed_asset_id"] != progress_state["last_completed_asset_id"]
        )

        if current_changed or last_changed:
            _rendered_state["current_asset_id"] = progress_state["current_asset_id"]
            _rendered_state["last_completed_asset_id"] = progress_state["last_completed_asset_id"]

            detail_container.clear()
            with detail_container:
                # --- Currently analyzing ---
                with ui.card().classes("flex-1 p-3"):
                    ui.label("Currently Analyzing").classes(
                        "text-sm font-semibold text-blue-600 mb-2"
                    )
                    current_item = progress_state["current_item"]
                    current_asset = progress_state["current_asset_id"]
                    if current_item:
                        _render_thumbnail_for(current_asset, detail_container)
                        ui.label(current_item).classes("font-medium mt-2 truncate")
                        ui.spinner(size="sm").classes("mt-1")
                    else:
                        ui.label("Waiting...").classes("text-gray-400 italic")

                # --- Last analyzed clip ---
                last_asset = progress_state["last_completed_asset_id"]
                if last_asset:
                    with ui.card().classes("flex-1 p-3"):
                        ui.label("Last Analyzed").classes(
                            "text-sm font-semibold text-green-600 mb-2"
                        )

                        # Show video preview if available, otherwise thumbnail
                        preview_path = progress_state["last_completed_video_path"]
                        video_shown = False
                        if preview_path:
                            try:
                                from nicegui import app as nicegui_app

                                video_url = nicegui_app.add_media_file(
                                    local_file=preview_path,
                                )

                                # Track URLs for cleanup but don't remove routes
                                # while they may still be streaming — Starlette raises
                                # RuntimeError if a route is removed mid-request.
                                prev_url = _rendered_state.get("prev_media_url")
                                urls_to_clean = _rendered_state.setdefault(
                                    "media_urls_to_clean", []
                                )
                                if prev_url and prev_url != video_url:
                                    urls_to_clean.append(prev_url)
                                # Only clean up old URLs (2+ cycles stale)
                                while len(urls_to_clean) > 3:
                                    old_url = urls_to_clean.pop(0)
                                    with contextlib.suppress(Exception):
                                        nicegui_app.remove_route(old_url)
                                _rendered_state["prev_media_url"] = video_url

                                ui.video(video_url).classes("w-full rounded").props(
                                    "muted autoplay loop"
                                )
                                video_shown = True
                            except Exception:
                                pass

                        if not video_shown:
                            _render_thumbnail_for(last_asset, detail_container)

                        # Segment info
                        segment = progress_state["last_completed_segment"]
                        score = progress_state["last_completed_score"]
                        if segment:
                            start, end = segment
                            seg_dur = end - start
                            with ui.row().classes("gap-2 mt-2 items-center"):
                                ui.icon("content_cut", color="blue").classes("text-sm")
                                ui.label(f"{start:.1f}s - {end:.1f}s ({seg_dur:.1f}s)").classes(
                                    "text-sm"
                                )
                        if score is not None:
                            with ui.row().classes("gap-2 items-center"):
                                ui.icon("star", color="amber").classes("text-sm")
                                ui.label(f"Score: {score:.2f}").classes("text-sm")

                        # LLM analysis
                        llm_desc = progress_state["last_completed_llm_description"]
                        llm_emotion = progress_state["last_completed_llm_emotion"]
                        llm_interest = progress_state["last_completed_llm_interestingness"]
                        llm_quality = progress_state["last_completed_llm_quality"]

                        if llm_desc:
                            with ui.row().classes("gap-2 mt-1 items-start"):
                                ui.icon("smart_toy", color="purple").classes("text-sm mt-0.5")
                                ui.label(llm_desc).classes("text-sm text-gray-600 italic")
                        if llm_emotion or llm_interest is not None:
                            badges: list[str] = []
                            if llm_emotion:
                                badges.append(llm_emotion)
                            if llm_interest is not None:
                                badges.append(f"interest: {llm_interest:.0%}")
                            if llm_quality is not None:
                                badges.append(f"quality: {llm_quality:.0%}")
                            with ui.row().classes("gap-1 mt-1 flex-wrap"):
                                for b in badges:
                                    ui.badge(b, color="purple").props("outline").classes("text-xs")

                        # Audio categories
                        audio_cats = progress_state["last_completed_audio_categories"]
                        if audio_cats:
                            _cat_colors = {
                                "laughter": "pink-4",
                                "baby": "pink-3",
                                "speech": "blue-grey-4",
                                "singing": "purple-4",
                                "music": "deep-purple-4",
                                "engine": "orange-6",
                                "nature": "green-5",
                                "crowd": "amber-6",
                                "animals": "brown-4",
                            }
                            with ui.row().classes("gap-1 mt-1 flex-wrap"):
                                ui.icon("hearing", color="blue").classes("text-sm")
                                for cat in audio_cats:
                                    color = _cat_colors.get(cat, "grey-5")
                                    ui.badge(cat, color=color).classes("text-xs")

        # Handle completion
        if progress_state["done"]:
            progress_timer.deactivate()
            if progress_state.get("cancelled"):
                ui.notify("Pipeline cancelled", type="warning")
                state.pipeline_running = False
                ui.navigate.to("/step2")
            elif progress_state["error"]:
                ui.notify(
                    f"Pipeline failed: {progress_state['error']}",
                    type="negative",
                )
                state.pipeline_running = False
            else:
                state.pipeline_running = False
                # Fire-and-forget: generate LLM title suggestion in background
                from immich_memories.ui.pages.pipeline_title import (
                    generate_title_after_pipeline,
                )

                asyncio.ensure_future(generate_title_after_pipeline(state))
                ui.navigate.to("/step2")

    # Poll progress every second — keeps event loop free for WebSocket heartbeats
    progress_timer = ui.timer(1.0, poll_progress)

    async def start_pipeline() -> None:
        """Launch the blocking pipeline in a thread pool."""

        def run_blocking() -> None:
            """Runs entirely in a background thread — no UI calls here."""
            tc = state.thumbnail_cache
            if tc is None:
                raise RuntimeError("Thumbnail cache not initialized")
            try:
                with SyncImmichClient(
                    base_url=state.immich_url,
                    api_key=state.immich_api_key,
                ) as client:
                    pipeline = SmartPipeline(
                        client=client,
                        analysis_cache=state.analysis_cache,
                        thumbnail_cache=tc,
                        config=config,
                    )

                    def on_progress(status: dict) -> None:
                        # Check cancellation flag — raised here so it stops
                        # at the next progress update (per-clip granularity)
                        if progress_state["cancelled"]:
                            raise PipelineCancelled("Cancelled by user")

                        # Copy all status fields to shared dict — no UI calls
                        progress_state["phase_number"] = status.get("phase_number", 1)
                        progress_state["total_phases"] = status.get("total_phases", 4)
                        progress_state["phase_label"] = status.get("phase_label", "Processing")
                        progress_state["progress_fraction"] = status.get("progress_fraction", 0)
                        progress_state["current_item"] = status.get("current_item", "")
                        progress_state["current_asset_id"] = status.get("current_asset_id")
                        progress_state["current_index"] = status.get("current_index", 0)
                        progress_state["total_items"] = status.get("total_items", 0)
                        progress_state["elapsed"] = status.get("elapsed", "0s")
                        progress_state["eta"] = status.get("eta", "--")
                        progress_state["avg_duration"] = status.get("avg_duration", 0.0)
                        progress_state["speed_ratio"] = status.get("speed_ratio", 0.0)
                        progress_state["completed_count"] = status.get("completed_count", 0)
                        progress_state["error_count"] = status.get("error_count", 0)
                        # Last completed clip details
                        progress_state["last_completed_asset_id"] = status.get(
                            "last_completed_asset_id"
                        )
                        progress_state["last_completed_segment"] = status.get(
                            "last_completed_segment"
                        )
                        progress_state["last_completed_score"] = status.get("last_completed_score")
                        progress_state["last_completed_video_path"] = status.get(
                            "last_completed_video_path"
                        )
                        progress_state["last_completed_llm_description"] = status.get(
                            "last_completed_llm_description"
                        )
                        progress_state["last_completed_llm_emotion"] = status.get(
                            "last_completed_llm_emotion"
                        )
                        progress_state["last_completed_llm_interestingness"] = status.get(
                            "last_completed_llm_interestingness"
                        )
                        progress_state["last_completed_llm_quality"] = status.get(
                            "last_completed_llm_quality"
                        )
                        progress_state["last_completed_audio_categories"] = status.get(
                            "last_completed_audio_categories"
                        )

                    result = pipeline.run(clips=clips, progress_callback=on_progress)

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

        await run.io_bound(run_blocking)

    # Kick off the pipeline without blocking the event loop
    ui.timer(0.1, start_pipeline, once=True)
