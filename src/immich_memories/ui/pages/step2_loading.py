"""Step 2: Clip loading and cached analysis helpers."""

from __future__ import annotations

import logging

from nicegui import run, ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.processing.clips import probe_video_url
from immich_memories.security import sanitize_error_message
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


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
