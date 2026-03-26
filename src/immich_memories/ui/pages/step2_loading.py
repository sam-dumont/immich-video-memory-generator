"""Step 2: Clip loading and cached analysis helpers."""

from __future__ import annotations

import contextlib
import logging

from nicegui import run, ui

from immich_memories.analysis.live_photo_pipeline import fetch_live_photo_clips
from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.processing.clip_probing import probe_video_url
from immich_memories.security import sanitize_error_message
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)

MIN_CLIP_DURATION = 1.5


def _fetch_assets(state) -> list:
    """Blocking: fetch video assets from Immich API."""
    date_range = state.date_range
    with SyncImmichClient(
        base_url=state.immich_url,
        api_key=state.immich_api_key,
    ) as client:
        multi_ids = state.memory_preset_params.get("person_ids", [])
        if len(multi_ids) >= 2:
            return client.get_videos_for_all_persons(multi_ids, date_range)
        if state.selected_person:
            return client.get_videos_for_person_and_date_range(state.selected_person.id, date_range)
        return client.get_videos_for_date_range(date_range)


def _filter_near_home(assets: list, state) -> list:
    """Filter out near-home videos for trip memories."""
    home_lat = state.memory_preset_params.get("home_lat")
    home_lon = state.memory_preset_params.get("home_lon")
    min_dist = state.memory_preset_params.get("min_distance_km")
    if not (home_lat and home_lon and min_dist):
        return assets
    from immich_memories.analysis.trip_detection import filter_near_home

    before = len(assets)
    assets = filter_near_home(assets, home_lat, home_lon, min_dist)
    filtered = before - len(assets)
    if filtered:
        logger.info(f"Filtered {filtered} near-home videos from trip")
    return assets


def _build_clips(assets: list) -> tuple[list[VideoClipInfo], int]:
    """Convert assets to VideoClipInfo, filtering short clips."""
    clips = []
    skipped = 0
    for asset in assets:
        duration = asset.duration_seconds or 0
        if duration < MIN_CLIP_DURATION:
            skipped += 1
            continue
        clips.append(VideoClipInfo(asset=asset, duration_seconds=duration))
    if skipped:
        logger.info(f"Skipped {skipped} clips shorter than {MIN_CLIP_DURATION}s")
    return clips, skipped


def _fetch_photos(state, date_range) -> list:
    """Fetch photo assets (blocking)."""
    person_id = state.selected_person.id if state.selected_person else None
    with SyncImmichClient(state.immich_url, state.immich_api_key) as client:
        return client.get_photos_for_date_range(date_range, person_id=person_id)


def _fetch_live_photos(state, date_range) -> tuple[list[VideoClipInfo], set[str]]:
    """Fetch live photo clips (blocking)."""
    lp_person_id = state.selected_person.id if state.selected_person else None
    multi_ids = state.memory_preset_params.get("person_ids", [])

    with SyncImmichClient(state.immich_url, state.immich_api_key) as lp_client:
        return fetch_live_photo_clips(
            lp_client,
            date_range,
            person_id=lp_person_id,
            person_ids=multi_ids if len(multi_ids) >= 2 else None,
            config=state.config,
        )


def _merge_live_photos(
    clips: list[VideoClipInfo], live_clips: list[VideoClipInfo], live_video_ids: set[str]
) -> list[VideoClipInfo]:
    """Merge live photo clips into main clip list, deduplicating video components."""
    if live_video_ids:
        before = len(clips)
        clips = [c for c in clips if c.asset.id not in live_video_ids]
        removed = before - len(clips)
        if removed:
            logger.info(f"Removed {removed} live photo video components")
    if live_clips:
        logger.info(f"Adding {len(live_clips)} Live Photo clips")
        clips.extend(live_clips)
    return clips


def _set_initial_selection(clips: list[VideoClipInfo], state) -> None:
    """Set initial selected_clip_ids, prioritizing real videos over live photos."""
    has_live = state.include_live_photos and any(c.asset.is_live_photo for c in clips)
    if not has_live:
        state.selected_clip_ids = {c.asset.id for c in clips}
        return

    video_clips = [c for c in clips if not c.asset.is_live_photo]
    video_duration = sum(c.duration_seconds or 0 for c in video_clips)
    target_seconds = state.target_duration * 60

    if video_duration >= target_seconds:
        state.selected_clip_ids = {c.asset.id for c in video_clips}
        logger.info(
            f"Enough video ({video_duration:.0f}s >= {target_seconds}s target), "
            f"live photos available but not pre-selected"
        )
    else:
        state.selected_clip_ids = {c.asset.id for c in clips}


def _load_clips() -> None:
    """Load clips from Immich API - triggers async loading."""
    state = get_app_state()

    with ui.dialog() as loading_dialog, ui.card().classes("p-6 min-w-[360px]"):
        ui.label("Loading videos...").classes("text-lg font-semibold").style(
            "color: var(--im-text)"
        )
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full my-3")
        progress_bar.style("--q-linear-progress-color: var(--im-primary)")
        status_label = (
            ui.label("Connecting to Immich...")
            .classes("text-sm")
            .style("color: var(--im-text-secondary)")
        )

    loading_dialog.open()

    async def do_load():
        try:
            status_label.set_text("Fetching videos from Immich...")
            progress_bar.value = 0.02

            date_range = state.date_range
            if date_range is None:
                raise ValueError("No date range configured")

            assets = await run.io_bound(_fetch_assets, state)
            assets = _filter_near_home(assets, state)

            status_label.set_text(f"Found {len(assets)} assets. Filtering...")
            progress_bar.value = 0.05

            clips, _ = _build_clips(assets)

            if state.include_live_photos:
                status_label.set_text("Fetching Live Photos...")
                live_clips, live_video_ids = await run.io_bound(
                    _fetch_live_photos, state, date_range
                )
                clips = _merge_live_photos(clips, live_clips, live_video_ids)

            if state.include_photos:
                status_label.set_text("Fetching photos...")
                photo_assets = await run.io_bound(_fetch_photos, state, date_range)
                state.photo_assets = photo_assets
                if photo_assets:
                    logger.info(f"Found {len(photo_assets)} photos")

            clips.sort(key=lambda c: c.asset.file_created_at)
            state.clips = clips
            _set_initial_selection(clips, state)

            status_label.set_text(f"Found {len(clips)} videos. Loading thumbnails...")
            progress_bar.value = 0.1
            await _load_thumbnails_and_metadata_async(clips, status_label, progress_bar)

            loading_dialog.close()
            ui.navigate.to("/step2")

        except Exception as e:
            loading_dialog.close()
            ui.notify(f"Failed to load videos: {sanitize_error_message(str(e))}", type="negative")
            logger.exception("Failed to load clips")

    ui.timer(0.1, do_load, once=True)


async def _load_thumbnails_and_metadata_async(
    clips: list[VideoClipInfo],
    status_label: ui.label,
    progress_bar: ui.linear_progress | None = None,
) -> None:
    """Load thumbnails and metadata from cache or API with live progress."""
    state = get_app_state()
    analysis_cache = state.analysis_cache
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        raise RuntimeError("Thumbnail cache not initialized")

    all_asset_ids = [c.asset.id for c in clips]

    cached_thumbnail_ids = set(thumbnail_cache.get_batch(all_asset_ids, "preview").keys())
    cached_metadata = analysis_cache.get_video_metadata_batch(all_asset_ids)

    for clip in clips:
        meta = cached_metadata.get(clip.asset.id)
        if meta:
            _apply_metadata(clip, meta)

    need_thumbs = [c for c in clips if c.asset.id not in cached_thumbnail_ids]
    need_meta = [c for c in clips if c.asset.id not in cached_metadata]
    total_work = len(need_thumbs) + len(need_meta)

    if total_work == 0:
        return

    done = 0
    batch_size = 10

    done = await _fetch_thumbnails_batched(
        need_thumbs,
        thumbnail_cache,
        state,
        status_label,
        progress_bar,
        done,
        total_work,
        batch_size,
    )
    await _fetch_metadata_batched(
        need_meta, state, analysis_cache, status_label, progress_bar, done, total_work, batch_size
    )

    if progress_bar:
        progress_bar.value = 1.0
    status_label.set_text("Done")


async def _fetch_thumbnails_batched(
    need_thumbs: list[VideoClipInfo],
    thumbnail_cache,
    state,
    status_label,
    progress_bar,
    done: int,
    total_work: int,
    batch_size: int,
) -> int:
    """Fetch thumbnails in batches. Returns updated done count."""
    for i in range(0, len(need_thumbs), batch_size):
        batch = need_thumbs[i : i + batch_size]

        def fetch_thumb_batch(clips_batch=batch):
            with SyncImmichClient(state.immich_url, state.immich_api_key) as client:
                for clip in clips_batch:
                    with contextlib.suppress(Exception):
                        thumb = client.get_asset_thumbnail(clip.asset.id, size="preview")
                        if thumb:
                            thumbnail_cache.put(clip.asset.id, "preview", thumb)

        await run.io_bound(fetch_thumb_batch)
        done += len(batch)
        frac = done / total_work
        status_label.set_text(
            f"Thumbnails: {min(i + batch_size, len(need_thumbs))}/{len(need_thumbs)}"
        )
        if progress_bar:
            progress_bar.value = 0.1 + frac * 0.85
    return done


async def _fetch_metadata_batched(
    need_meta: list[VideoClipInfo],
    state,
    analysis_cache,
    status_label,
    progress_bar,
    done: int,
    total_work: int,
    batch_size: int,
) -> None:
    """Fetch video metadata in batches."""
    for i in range(0, len(need_meta), batch_size):
        batch = need_meta[i : i + batch_size]

        def fetch_meta_batch(clips_batch=batch):
            with SyncImmichClient(state.immich_url, state.immich_api_key) as client:
                for clip in clips_batch:
                    _probe_and_cache_metadata(clip, client, state, analysis_cache)

        await run.io_bound(fetch_meta_batch)
        done += len(batch)
        frac = done / total_work
        status_label.set_text(
            f"Probing metadata: {min(i + batch_size, len(need_meta))}/{len(need_meta)}"
        )
        if progress_bar:
            progress_bar.value = 0.1 + frac * 0.85


def _apply_metadata(clip: VideoClipInfo, meta: dict) -> None:
    """Apply cached metadata to a clip."""
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


def _probe_and_cache_metadata(clip, client, state, analysis_cache) -> None:
    """Probe video metadata via ffprobe and save to cache."""
    try:
        probe_id = clip.asset.live_photo_video_id or clip.asset.id
        video_url = client.get_video_original_url(probe_id)
        headers = {"x-api-key": state.immich_api_key}
        video_info = probe_video_url(video_url, headers=headers)
        if video_info:
            _apply_metadata(clip, video_info)
            if video_info.get("duration"):
                clip.duration_seconds = video_info["duration"]
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


def _render_cached_analysis_summary(clips: list[VideoClipInfo]) -> None:
    """Render summary of previously analyzed clips."""
    state = get_app_state()
    analysis_cache = state.analysis_cache

    analyzed_clips = {}
    for clip in clips:
        analysis = analysis_cache.get_analysis(clip.asset.id)
        if analysis and analysis.segments and len(analysis.segments) > 0:
            analyzed_clips[clip.asset.id] = analysis

    if not analyzed_clips:
        return

    time_saved_seconds = len(analyzed_clips) * 30

    with ui.card().classes("w-full p-2 mb-4").style("background: var(--im-info-bg)"):
        ui.label(
            f"Previously Analyzed: Found {len(analyzed_clips)} clips already analyzed from cache. "
            f"This will save approximately {time_saved_seconds // 60}m {time_saved_seconds % 60}s."
        ).classes("text-sm").style("color: var(--im-info)")

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
