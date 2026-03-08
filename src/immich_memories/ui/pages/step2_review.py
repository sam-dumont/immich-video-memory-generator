"""Step 2: Clip Review page."""

from __future__ import annotations

import base64
import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import run, ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.processing.clips import probe_video_url
from immich_memories.security import sanitize_error_message
from immich_memories.ui.state import get_app_state

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PipelineCancelled(Exception):
    """Raised when the user cancels the pipeline from the UI."""


# ============================================================================
# Helper Functions
# ============================================================================


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def get_thumbnail(asset_id: str) -> bytes | None:
    """Get thumbnail from cache on-demand."""
    state = get_app_state()
    if state.thumbnail_cache is None:
        return None
    try:
        return state.thumbnail_cache.get(asset_id, "preview")
    except Exception:
        return None


def _get_preview_path(asset_id: str) -> Path | None:
    """Get or create an H.264 480p preview for a clip (Chrome/Windows compatible).

    Returns the path to the preview file, or None if the source isn't cached.
    """
    import subprocess

    from immich_memories.config import get_config

    config = get_config()
    preview_dir = config.cache.cache_path / "preview-cache"
    preview_dir.mkdir(parents=True, exist_ok=True)

    preview_path = preview_dir / f"{asset_id}.mp4"
    if preview_path.exists():
        return preview_path

    # Find the source video in the video cache
    # Cache uses two-level dirs: {cache_dir}/{id[:2]}/{id}{ext}
    # Files can be {id}.MOV (original) or {id}_480p.MOV (analysis downscale)
    video_cache_dir = config.cache.video_cache_path
    if not video_cache_dir.exists():
        return None

    subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
    sub_path = video_cache_dir / subdir
    if not sub_path.exists():
        return None

    # Prefer the _480p downscale (already small, faster to transcode)
    source = None
    for pattern in [f"{asset_id}_480p.*", f"{asset_id}.*"]:
        matches = list(sub_path.glob(pattern))
        if matches:
            source = matches[0]
            break

    if source is None:
        return None

    # Transcode to H.264 480p — fast, small, plays everywhere
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=-2:480",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(preview_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and preview_path.exists():
            return preview_path
        logger.warning(f"Preview transcode failed for {asset_id}: {result.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"Preview transcode error for {asset_id}: {e}")

    return None


def clip_quality_score(c: VideoClipInfo) -> tuple[int, int, int, int]:
    """Score a clip for quality comparison. Higher is better."""
    res_score = (c.width or 0) * (c.height or 0)
    hdr_score = 1 if c.is_hdr else 0
    depth_score = c.bit_depth or 8
    bitrate_score = c.bitrate or 0
    return (res_score, hdr_score, depth_score, bitrate_score)


# ============================================================================
# UI Components
# ============================================================================


def render_duration_summary(
    total_duration: float,
    target_duration: float,
    clip_count: int,
    container: ui.element,
) -> None:
    """Render duration summary."""
    container.clear()
    with container, ui.row().classes("w-full gap-8"):
        with ui.column().classes("items-center"):
            ui.label("Selected Clips").classes("text-sm text-gray-500")
            ui.label(str(clip_count)).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Total Duration").classes("text-sm text-gray-500")
            ui.label(format_duration(total_duration)).classes("text-2xl font-bold")


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


# ============================================================================
# Main Step 2 Render Function
# ============================================================================


def render_step2() -> None:
    """Render Step 2: Clip Review."""
    state = get_app_state()

    # Guard: redirect to step 1 if state was lost (e.g. hot-reload restart)
    if not state.date_range or not state.immich_url:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("Session expired — please reconfigure.").classes("text-yellow-700 font-medium")
            ui.label("The server restarted and lost your settings.").classes(
                "text-yellow-600 text-sm"
            )
        ui.button("Back to Configuration", on_click=lambda: ui.navigate.to("/"), icon="arrow_back")
        return

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
        return

    clips = state.clips

    if not clips:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("No videos found for the selected criteria.").classes("text-yellow-700")

        def go_back():
            state.step = 1
            ui.navigate.to("/")

        ui.button("Back to Configuration", on_click=go_back, icon="arrow_back")
        return

    # Check pipeline state
    if state.pipeline_running:
        _render_pipeline_progress_ui(clips)
        return

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
        return

    # Check if in review mode (showing only selected clips)
    if state.review_selected_mode and state.selected_clip_ids:
        _render_review_selected_clips(clips)
        return

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

    # ========================================================================
    # Clip Grid
    # ========================================================================
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


# ============================================================================
# Sub-rendering Functions
# ============================================================================


def _load_clips() -> None:
    """Load clips from Immich API - triggers async loading."""
    state = get_app_state()

    # Create dialog with progress
    with ui.dialog() as loading_dialog, ui.card().classes("p-6"):
        ui.label("Loading videos...").classes("text-lg font-semibold")
        ui.spinner(size="lg").classes("my-4")
        status_label = ui.label("Connecting to Immich...").classes("text-sm text-gray-600")

    loading_dialog.open()

    async def do_load():
        """Async wrapper to load clips with run.io_bound."""
        try:

            def fetch_clips():
                """Blocking function to fetch clips from API."""
                with SyncImmichClient(
                    base_url=state.immich_url,
                    api_key=state.immich_api_key,
                ) as client:
                    date_range = state.date_range
                    if date_range is None:
                        raise ValueError("No date range configured")

                    if state.selected_person:
                        assets = client.get_videos_for_person_and_date_range(
                            state.selected_person.id,
                            date_range,
                        )
                    else:
                        assets = client.get_videos_for_date_range(date_range)

                    # Convert to VideoClipInfo, filtering out very short clips
                    MIN_CLIP_DURATION = 1.5
                    clips = []
                    skipped_short = 0
                    for asset in assets:
                        duration = asset.duration_seconds or 0
                        if duration < MIN_CLIP_DURATION:
                            skipped_short += 1
                            continue
                        clip = VideoClipInfo(
                            asset=asset,
                            duration_seconds=duration,
                        )
                        clips.append(clip)

                    if skipped_short > 0:
                        logger.info(
                            f"Skipped {skipped_short} clips shorter than {MIN_CLIP_DURATION}s"
                        )

                    return clips, client

            # Run blocking code in thread pool
            clips, client = await run.io_bound(fetch_clips)

            state.clips = clips
            state.selected_clip_ids = {c.asset.id for c in clips}

            # Load thumbnails and metadata
            status_label.set_text(f"Found {len(clips)} videos. Loading thumbnails...")
            await _load_thumbnails_and_metadata_async(clips, status_label)

            loading_dialog.close()
            ui.navigate.to("/step2")

        except Exception as e:
            loading_dialog.close()
            ui.notify(f"Failed to load videos: {sanitize_error_message(str(e))}", type="negative")
            logger.exception("Failed to load clips")

    # Schedule the async loading
    ui.timer(0.1, do_load, once=True)


async def _load_thumbnails_and_metadata_async(
    clips: list[VideoClipInfo],
    status_label: ui.label,
) -> None:
    """Load thumbnails and metadata from cache or API (async version)."""
    state = get_app_state()
    analysis_cache = state.analysis_cache
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        raise RuntimeError("Thumbnail cache not initialized")

    all_asset_ids = [c.asset.id for c in clips]

    # Check cached data (these are fast local operations)
    cached_thumbnail_ids = set(thumbnail_cache.get_batch(all_asset_ids, "preview").keys())
    cached_metadata = analysis_cache.get_video_metadata_batch(all_asset_ids)

    # Apply cached metadata
    for clip in clips:
        meta = cached_metadata.get(clip.asset.id)
        if meta:
            clip.width = meta.get("width") or clip.width
            clip.height = meta.get("height") or clip.height
            clip.fps = meta.get("fps") or clip.fps
            clip.codec = meta.get("codec") or clip.codec
            clip.bitrate = meta.get("bitrate") or clip.bitrate
            if meta.get("duration_seconds"):
                clip.duration_seconds = meta["duration_seconds"]
            clip.color_space = meta.get("color_space")
            clip.color_transfer = meta.get("color_transfer")
            clip.color_primaries = meta.get("color_primaries")
            clip.bit_depth = meta.get("bit_depth")

    # Find missing data
    clips_needing_thumbnails = [c for c in clips if c.asset.id not in cached_thumbnail_ids]
    clips_needing_metadata = [c for c in clips if c.asset.id not in cached_metadata]

    total_work = len(clips_needing_thumbnails) + len(clips_needing_metadata)

    if total_work == 0:
        return

    def fetch_thumbnails_and_metadata():
        """Blocking function to fetch thumbnails and metadata."""
        with SyncImmichClient(
            base_url=state.immich_url,
            api_key=state.immich_api_key,
        ) as client:
            # Fetch missing thumbnails
            for _i, clip in enumerate(clips_needing_thumbnails):
                try:
                    thumb = client.get_asset_thumbnail(clip.asset.id, size="preview")
                    if thumb:
                        thumbnail_cache.put(clip.asset.id, "preview", thumb)
                except Exception:
                    pass

            # Probe missing metadata
            for clip in clips_needing_metadata:
                try:
                    video_url = client.get_video_original_url(clip.asset.id)
                    headers = {"x-api-key": state.immich_api_key}
                    video_info = probe_video_url(video_url, headers=headers)
                    if video_info:
                        clip.width = video_info.get("width", clip.width)
                        clip.height = video_info.get("height", clip.height)
                        clip.fps = video_info.get("fps", clip.fps)
                        clip.codec = video_info.get("codec", clip.codec)
                        clip.bitrate = video_info.get("bitrate", clip.bitrate)
                        if video_info.get("duration"):
                            clip.duration_seconds = video_info["duration"]
                        clip.color_space = video_info.get("color_space")
                        clip.color_transfer = video_info.get("color_transfer")
                        clip.color_primaries = video_info.get("color_primaries")
                        clip.bit_depth = video_info.get("bit_depth")

                        analysis_cache.save_video_metadata(
                            asset_id=clip.asset.id,
                            checksum=clip.asset.checksum,
                            duration_seconds=clip.duration_seconds,
                            width=clip.width,
                            height=clip.height,
                            bitrate=clip.bitrate,
                            fps=clip.fps,
                            codec=clip.codec,
                            color_space=clip.color_space,
                            color_transfer=clip.color_transfer,
                            color_primaries=clip.color_primaries,
                            bit_depth=clip.bit_depth,
                        )
                except Exception as e:
                    logger.debug(f"Failed to probe video metadata: {e}")

    # Run blocking code in thread pool
    status_label.set_text(
        f"Loading {len(clips_needing_thumbnails)} thumbnails + probing {len(clips_needing_metadata)} metadata..."
    )
    await run.io_bound(fetch_thumbnails_and_metadata)
    status_label.set_text("Done loading metadata")


def _render_cached_analysis_summary(clips: list[VideoClipInfo]) -> None:
    """Render summary of previously analyzed clips."""
    state = get_app_state()
    analysis_cache = state.analysis_cache

    # Find clips with cached analysis
    analyzed_clips = {}
    for clip in clips:
        analysis = analysis_cache.get_analysis(clip.asset.id)
        if analysis and analysis.segments and len(analysis.segments) > 0:
            analyzed_clips[clip.asset.id] = analysis

    if not analyzed_clips:
        return

    time_saved_seconds = len(analyzed_clips) * 30

    with ui.card().classes("w-full p-2 bg-blue-50 mb-4"):
        ui.label(
            f"Previously Analyzed: Found {len(analyzed_clips)} clips already analyzed from cache. "
            f"This will save approximately {time_saved_seconds // 60}m {time_saved_seconds % 60}s."
        ).classes("text-blue-700 text-sm")

        def use_cached():
            for asset_id, analysis in analyzed_clips.items():
                best_seg = analysis.get_best_segment()
                if best_seg:
                    state.clip_segments[asset_id] = (best_seg.start_time, best_seg.end_time)
            state.selected_clip_ids = set(analyzed_clips.keys())
            ui.notify(f"Loaded {len(analyzed_clips)} clips from cache!", type="positive")
            ui.navigate.to("/step2")

        ui.button(
            "Use Cached Analysis (Skip Re-analysis)",
            on_click=use_cached,
        ).props("outline size=sm")


def _detect_duplicates(
    clips: list[VideoClipInfo],
) -> tuple[set[str], set[str]]:
    """Detect duplicate clips and identify lower quality ones."""
    clips_by_datetime = _group_clips_by_datetime(clips)

    duplicate_ids: set[str] = set()
    lower_quality_ids: set[str] = set()

    for _key, group in clips_by_datetime.items():
        if len(group) > 1:
            sorted_group = sorted(group, key=clip_quality_score, reverse=True)
            best_clip = sorted_group[0]

            for c in group:
                duplicate_ids.add(c.asset.id)
                if c.asset.id != best_clip.asset.id:
                    lower_quality_ids.add(c.asset.id)

    return duplicate_ids, lower_quality_ids


def _group_clips_by_datetime(clips: list[VideoClipInfo]) -> dict[str, list[VideoClipInfo]]:
    """Group clips by datetime and duration for duplicate detection."""
    clips_by_datetime: dict[str, list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        dt_key = clip.asset.file_created_at.strftime("%Y-%m-%d_%H:%M")
        dur_key = round(clip.duration_seconds or 0, 0)
        key = f"{dt_key}_{dur_key}"
        clips_by_datetime[key].append(clip)
    return clips_by_datetime


def _update_duration_summary(clips: list[VideoClipInfo], container: ui.element) -> None:
    """Update the duration summary display."""
    state = get_app_state()
    avg_clip_sec = state.avg_clip_duration

    selected_duration = 0.0
    for c in clips:
        if c.asset.id in state.selected_clip_ids:
            if c.asset.id in state.clip_segments:
                start, end = state.clip_segments[c.asset.id]
                selected_duration += end - start
            else:
                selected_duration += min(c.duration_seconds or avg_clip_sec, avg_clip_sec)

    selected_count = len(state.selected_clip_ids)
    render_duration_summary(
        selected_duration, state.target_duration * 60, selected_count, container
    )


CLIPS_PER_PAGE = 20


def _render_clip_grid_paginated(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render a paginated grid of clip cards (loads in batches)."""
    if len(clips) <= page_size:
        _render_clip_grid(clips, duplicate_ids, lower_quality_ids, summary_container)
        return

    # Show first page immediately
    grid_container = ui.column().classes("w-full")
    with grid_container:
        _render_clip_grid(clips[:page_size], duplicate_ids, lower_quality_ids, summary_container)

    remaining = clips[page_size:]
    if remaining:
        btn_container = ui.row().classes("w-full justify-center mt-2")
        with btn_container:

            def load_more(
                remaining_clips=remaining,
                parent=grid_container,
                btn_ctr=btn_container,
            ):
                btn_ctr.clear()
                with parent:
                    _render_clip_grid(
                        remaining_clips[:page_size],
                        duplicate_ids,
                        lower_quality_ids,
                        summary_container,
                    )
                still_remaining = remaining_clips[page_size:]
                if still_remaining:
                    with btn_ctr:
                        ui.button(
                            f"Show more ({len(still_remaining)} remaining)",
                            on_click=lambda sr=still_remaining: load_more(sr, parent, btn_ctr),
                        ).props("outline")

            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=load_more,
            ).props("outline")


def _render_clip_grid(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render a grid of clip cards."""
    state = get_app_state()
    all_clips = state.clips

    with ui.element("div").classes("grid grid-cols-5 gap-4"):
        for clip in clips:
            is_selected = clip.asset.id in state.selected_clip_ids
            is_duplicate = clip.asset.id in duplicate_ids
            is_best = is_duplicate and clip.asset.id not in lower_quality_ids

            date_str = clip.asset.file_created_at.strftime("%b %d %H:%M")
            duration_str = (
                format_duration(clip.duration_seconds) if clip.duration_seconds else "N/A"
            )

            with ui.card().classes("p-2"):
                # Thumbnail
                thumb = get_thumbnail(clip.asset.id)
                if thumb:
                    b64 = base64.b64encode(thumb).decode()
                    ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-24 object-cover")
                else:
                    ui.element("div").classes(
                        "w-full h-24 bg-gray-200 flex items-center justify-center"
                    )

                # Badges
                badges = []
                if clip.asset.is_favorite:
                    badges.append("star")
                if clip.is_hdr:
                    badges.append(clip.hdr_format or "HDR")
                if clip.width and clip.height:
                    if clip.width >= 3840 or clip.height >= 2160:
                        badges.append("4K")
                    elif clip.width >= 1920 or clip.height >= 1080:
                        badges.append("HD")

                if badges:
                    with ui.row().classes("gap-1 flex-wrap"):
                        for badge in badges:
                            if badge == "star":
                                ui.icon("star", color="yellow").classes("text-xs")
                            else:
                                ui.badge(badge, color="blue").classes("text-xs")

                # Date and duration
                ui.label(date_str).classes("font-semibold text-sm")
                ui.label(f"⏱ {duration_str}").classes("text-xs text-gray-500")

                # Filename
                filename = clip.asset.original_file_name or "Unknown"
                if len(filename) > 20:
                    filename = filename[:17] + "..."
                ui.label(filename).classes("text-xs text-gray-400 truncate")

                # Resolution
                if clip.width and clip.height:
                    res_str = f"{clip.width}x{clip.height}"
                    if clip.color_space:
                        res_str += f" • {clip.color_space}"
                    ui.label(res_str).classes("text-xs text-gray-400")

                # Duplicate indicator
                if is_duplicate:
                    if is_best:
                        ui.label("Best").classes("text-green-600 text-xs font-semibold")
                    else:
                        ui.label("Duplicate").classes("text-orange-600 text-xs")

                # Selection checkbox
                def make_toggle_handler(asset_id: str):
                    def toggle(e):
                        value = e.value if hasattr(e, "value") else e
                        if value:
                            state.selected_clip_ids.add(asset_id)
                        else:
                            state.selected_clip_ids.discard(asset_id)
                        _update_duration_summary(all_clips, summary_container)

                    return toggle

                checkbox = ui.checkbox("Include", value=is_selected)
                checkbox.on_value_change(make_toggle_handler(clip.asset.id))


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

    # Stats row: counts + ETA + speed
    stats_container = ui.row().classes(
        "w-full justify-between items-center text-sm text-gray-600 mt-1"
    )

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

        # Stats row — lightweight text updates (rebuild is cheap, no media)
        stats_container.clear()
        with stats_container:
            idx = progress_state["current_index"]
            total = progress_state["total_items"]
            errors = progress_state["error_count"]
            eta = progress_state["eta"]
            elapsed = progress_state["elapsed"]
            speed = progress_state["speed_ratio"]
            avg = progress_state["avg_duration"]

            ui.label(f"{idx}/{total} clips").classes("font-medium")
            ui.label(f"Elapsed: {elapsed}")
            if speed > 0:
                ui.label(f"Speed: {speed:.1f}x realtime")
            if avg > 0:
                ui.label(f"~{avg:.1f}s/clip")
            ui.label(f"ETA: {eta}").classes("font-medium")
            if errors > 0:
                ui.label(f"{errors} errors").classes("text-orange-600")

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


def _render_review_selected_clips(clips: list[VideoClipInfo]) -> None:
    """Render the review/refinement UI for selected clips only."""
    state = get_app_state()

    ui.label("Review & Refine Selected Clips").classes("text-xl font-semibold")
    ui.label("Adjust time segments, preview clips, and remove any unwanted selections.").classes(
        "text-sm text-gray-500 mb-4"
    )

    # Filter to selected clips only
    selected_clips = [c for c in clips if c.asset.id in state.selected_clip_ids]

    if not selected_clips:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("No clips selected. Return to generate new selections.").classes(
                "text-yellow-700"
            )

        def go_back():
            state.review_selected_mode = False
            ui.navigate.to("/step2")

        ui.button("Back to Clip Selection", on_click=go_back, icon="arrow_back")
        return

    # Initialize segments if missing
    avg_clip_duration = state.avg_clip_duration
    for clip in selected_clips:
        if clip.asset.id not in state.clip_segments:
            duration = clip.duration_seconds or 10
            end_time = min(duration, float(avg_clip_duration))
            state.clip_segments[clip.asset.id] = (0.0, end_time)

    # Calculate total selected duration
    def calc_total_duration():
        return sum(
            end - start
            for asset_id, (start, end) in state.clip_segments.items()
            if asset_id in state.selected_clip_ids
        )

    target_duration = state.target_duration * 60

    # Summary metrics
    summary_container = ui.row().classes("w-full gap-8 mb-4")

    def update_summary():
        summary_container.clear()
        total_selected = calc_total_duration()
        diff = total_selected - target_duration
        with summary_container:
            with ui.column().classes("items-center"):
                ui.label("Selected Clips").classes("text-sm text-gray-500")
                ui.label(
                    str(len([c for c in selected_clips if c.asset.id in state.selected_clip_ids]))
                ).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Total Duration").classes("text-sm text-gray-500")
                ui.label(format_duration(total_selected)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Target").classes("text-sm text-gray-500")
                ui.label(format_duration(target_duration)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Difference").classes("text-sm text-gray-500")
                diff_str = f"{'+' if diff > 0 else ''}{format_duration(abs(diff))}"
                ui.label(diff_str).classes("text-2xl font-bold")

    update_summary()

    ui.separator()

    # Quick actions
    with ui.row().classes("w-full gap-2 mb-4"):

        def set_all_first_5s():
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                state.clip_segments[clip.asset.id] = (0.0, min(5.0, duration))
            update_summary()

        def set_all_middle_5s():
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                mid = duration / 2
                state.clip_segments[clip.asset.id] = (max(0, mid - 2.5), min(duration, mid + 2.5))
            update_summary()

        ui.button("Set all to first 5s", on_click=set_all_first_5s).props("outline")
        ui.button("Set all to middle 5s", on_click=set_all_middle_5s).props("outline")

        custom_sec_input = ui.number("Custom seconds", value=5, min=1, max=30).classes("w-24")

        def set_all_custom():
            custom_sec = float(custom_sec_input.value)
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                state.clip_segments[clip.asset.id] = (0.0, min(custom_sec, duration))
            update_summary()

        ui.button("Apply", on_click=set_all_custom).props("outline")

    ui.separator()

    # Clip refinement cards
    selected_clips_sorted = sorted(selected_clips, key=lambda c: c.asset.file_created_at)
    for i, clip in enumerate(selected_clips_sorted):
        if clip.asset.id not in state.selected_clip_ids:
            continue

        date_str = clip.asset.file_created_at.strftime("%B %d, %Y")
        duration = float(clip.duration_seconds or 10)
        start, end = state.clip_segments.get(clip.asset.id, (0.0, min(5.0, duration)))
        start = float(start)
        end = float(end)
        segment_duration = end - start

        # Build title
        title_parts = []
        if clip.asset.is_favorite:
            title_parts.append("★")
        if clip.is_hdr:
            title_parts.append(f"[{clip.hdr_format}]")
        title_parts.append(date_str)
        title_parts.append(
            f"• Using {format_duration(segment_duration)} of {format_duration(duration)}"
        )

        with ui.expansion(" ".join(title_parts), value=i < 5).classes("w-full"):  # noqa: SIM117
            with ui.row().classes("w-full gap-4"):
                # Preview column
                with ui.column().classes("w-64"):
                    video_id = f"preview_{clip.asset.id.replace('-', '_')}"

                    # Video/thumbnail container (will be swapped async)
                    with ui.element("div").classes("w-full") as video_container:
                        thumb = get_thumbnail(clip.asset.id)
                        if thumb:
                            b64 = base64.b64encode(thumb).decode()
                            ui.image(f"data:image/jpeg;base64,{b64}").classes(
                                "w-full h-24 object-cover rounded"
                            )
                        else:
                            ui.element("div").classes("w-full h-24 bg-gray-200 rounded")

                    # Async preview loader — replaces thumbnail with video when ready
                    def make_preview_loader(asset_id: str, container, vid_id: str):
                        async def load_preview():
                            preview_path = await run.io_bound(_get_preview_path, asset_id)
                            if preview_path:
                                from nicegui import app as nicegui_app

                                preview_url = nicegui_app.add_media_file(local_file=preview_path)
                                container.clear()
                                with container:
                                    video_el = ui.video(preview_url).classes("w-full rounded")
                                    video_el.props(f'muted playsinline id="{vid_id}"')

                        return load_preview

                    ui.timer(
                        0.1,
                        make_preview_loader(clip.asset.id, video_container, video_id),
                        once=True,
                    )

                    # Keep checkbox
                    def make_toggle_handler(asset_id: str):
                        def handler(e):
                            value = e.value if hasattr(e, "value") else e
                            if value:
                                state.selected_clip_ids.add(asset_id)
                            else:
                                state.selected_clip_ids.discard(asset_id)
                            update_summary()

                        return handler

                    keep_checkbox = ui.checkbox("Include in compilation", value=True)
                    keep_checkbox.on_value_change(make_toggle_handler(clip.asset.id))

                # Controls column
                with ui.column().classes("flex-1"):
                    # Range slider
                    def make_range_handler(asset_id: str, vid_id: str | None):
                        def handler(e):
                            value = e.value if hasattr(e, "value") else e
                            state.clip_segments[asset_id] = (value["min"], value["max"])
                            update_summary()
                            # Seek video to new start position
                            if vid_id:
                                ui.run_javascript(f'''
                                    const v = document.getElementById("{vid_id}");
                                    if (v) {{ v.currentTime = {value["min"]}; }}
                                ''')

                        return handler

                    ui.label("Select range").classes("text-sm text-gray-500")
                    range_slider = ui.range(
                        min=0, max=duration, step=0.1, value={"min": start, "max": end}
                    ).classes("w-full")
                    range_slider.on_value_change(make_range_handler(clip.asset.id, video_id))

                    # Play preview button + quick range buttons
                    with ui.row().classes("gap-2 mt-2"):
                        if video_id:

                            def make_play_handler(
                                vid_id: str,
                                asset_id: str,
                                default_start: float = start,
                                default_end: float = end,
                            ):
                                def handler():
                                    s, e = state.clip_segments.get(
                                        asset_id, (default_start, default_end)
                                    )
                                    # Play from start to end of range, then pause
                                    ui.run_javascript(f'''
                                        const v = document.getElementById("{vid_id}");
                                        if (v) {{
                                            v.currentTime = {s};
                                            v.play();
                                            const endTime = {e};
                                            const checkEnd = setInterval(() => {{
                                                if (v.currentTime >= endTime) {{
                                                    v.pause();
                                                    clearInterval(checkEnd);
                                                }}
                                            }}, 50);
                                        }}
                                    ''')

                                return handler

                            ui.button(
                                "Preview", on_click=make_play_handler(video_id, clip.asset.id)
                            ).props("outline size=sm color=primary").classes("font-semibold")

                        def make_quick_btn_handler(
                            asset_id: str,
                            new_start: float,
                            new_end: float,
                            vid_id: str | None,
                            slider,
                        ):
                            def handler():
                                state.clip_segments[asset_id] = (new_start, new_end)
                                slider.value = {"min": new_start, "max": new_end}
                                update_summary()
                                if vid_id:
                                    ui.run_javascript(f'''
                                        const v = document.getElementById("{vid_id}");
                                        if (v) {{ v.currentTime = {new_start}; }}
                                    ''')

                            return handler

                        ui.button(
                            "First 5s",
                            on_click=make_quick_btn_handler(
                                clip.asset.id, 0.0, min(5.0, duration), video_id, range_slider
                            ),
                        ).props("outline size=sm")
                        ui.button(
                            "Last 5s",
                            on_click=make_quick_btn_handler(
                                clip.asset.id,
                                max(0, duration - 5.0),
                                duration,
                                video_id,
                                range_slider,
                            ),
                        ).props("outline size=sm")
                        mid = duration / 2
                        ui.button(
                            "Middle 5s",
                            on_click=make_quick_btn_handler(
                                clip.asset.id,
                                max(0, mid - 2.5),
                                min(duration, mid + 2.5),
                                video_id,
                                range_slider,
                            ),
                        ).props("outline size=sm")
                        ui.button(
                            "Full clip",
                            on_click=make_quick_btn_handler(
                                clip.asset.id, 0.0, duration, video_id, range_slider
                            ),
                        ).props("outline size=sm")

                    # Rotation override
                    rotation_options = {
                        "Auto": None,
                        "0°": 0,
                        "90° CW": 90,
                        "180°": 180,
                        "90° CCW": 270,
                    }
                    current_rotation = state.clip_rotations.get(clip.asset.id)
                    current_opt = "Auto"
                    for name, val in rotation_options.items():
                        if val == current_rotation:
                            current_opt = name
                            break

                    def make_rotation_handler(asset_id: str, opts: dict = rotation_options):
                        def handler(e):
                            value = e.value if hasattr(e, "value") else e
                            state.clip_rotations[asset_id] = opts.get(value)

                        return handler

                    rotation_select = ui.select(
                        options=list(rotation_options.keys()),
                        label="Rotation",
                        value=current_opt,
                    ).classes("w-32")
                    rotation_select.on_value_change(make_rotation_handler(clip.asset.id))

                    # Metadata
                    meta_parts = []
                    if clip.width and clip.height:
                        meta_parts.append(f"{clip.width}x{clip.height}")
                    if clip.fps:
                        meta_parts.append(f"{clip.fps:.0f}fps")
                    if clip.codec:
                        meta_parts.append(clip.codec)
                    if meta_parts:
                        ui.label(" | ".join(meta_parts)).classes("text-xs text-gray-400")

    ui.separator()

    # Final summary
    final_duration = calc_total_duration()
    ui.label(f"Final Duration: {format_duration(final_duration)}").classes("text-lg font-semibold")

    # Navigation
    with ui.row().classes("w-full gap-4 mt-4"):

        def go_back_selection():
            state.review_selected_mode = False
            ui.navigate.to("/step2")

        def rerun_analysis():
            state.review_selected_mode = False
            state.selected_clip_ids = set()
            state.clip_segments = {}
            ui.navigate.to("/step2")

        def continue_to_generation():
            if state.selected_clip_ids:
                state.review_selected_mode = False
                state.step = 3
                ui.navigate.to("/step3")
            else:
                ui.notify("Please select at least one clip", type="warning")

        ui.button("Back to Selection", on_click=go_back_selection, icon="arrow_back").props(
            "outline"
        )
        ui.button("Re-run Analysis", on_click=rerun_analysis, icon="refresh").props("outline")
        ui.button(
            "Continue to Generation",
            on_click=continue_to_generation,
            icon="arrow_forward",
        ).props("color=primary")
