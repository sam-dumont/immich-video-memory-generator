"""Step 2: Clip loading and cached analysis helpers."""

from __future__ import annotations

import logging

from nicegui import run, ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.processing.clips import probe_video_url
from immich_memories.security import sanitize_error_message
from immich_memories.timeperiod import DateRange
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def _load_clips() -> None:
    """Load clips from Immich API - triggers async loading."""
    state = get_app_state()

    # Create dialog with progress bar
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
        """Async wrapper to load clips with run.io_bound."""
        try:
            status_label.set_text("Fetching videos from Immich...")
            progress_bar.value = 0.02

            date_range = state.date_range
            if date_range is None:
                raise ValueError("No date range configured")

            def fetch_assets():
                """Blocking: fetch assets from API."""
                with SyncImmichClient(
                    base_url=state.immich_url,
                    api_key=state.immich_api_key,
                ) as client:
                    multi_ids = state.memory_preset_params.get("person_ids", [])
                    if len(multi_ids) >= 2:
                        assets = client.get_videos_for_all_persons(multi_ids, date_range)
                    elif state.selected_person:
                        assets = client.get_videos_for_person_and_date_range(
                            state.selected_person.id,
                            date_range,
                        )
                    else:
                        assets = client.get_videos_for_date_range(date_range)
                    return assets, client

            assets, client = await run.io_bound(fetch_assets)
            status_label.set_text(f"Found {len(assets)} assets. Filtering...")
            progress_bar.value = 0.05

            # Convert to VideoClipInfo, filtering out very short clips
            MIN_CLIP_DURATION = 1.5
            clips = []
            skipped_short = 0
            for asset in assets:
                duration = asset.duration_seconds or 0
                if duration < MIN_CLIP_DURATION:
                    skipped_short += 1
                    continue
                clips.append(VideoClipInfo(asset=asset, duration_seconds=duration))

            if skipped_short > 0:
                logger.info(f"Skipped {skipped_short} clips shorter than {MIN_CLIP_DURATION}s")

            # Include Live Photos if enabled
            if state.include_live_photos:
                status_label.set_text("Fetching Live Photos...")
                # Live photo person filtering: Immich doesn't return
                # livePhotoVideoId when filtering by IMAGE type + person.
                # Instead we search by person with NO type filter and then
                # filter client-side for is_live_photo.
                lp_person_id = state.selected_person.id if state.selected_person else None
                multi_ids = state.memory_preset_params.get("person_ids", [])

                def fetch_live():
                    with SyncImmichClient(state.immich_url, state.immich_api_key) as lp_client:
                        return _fetch_live_photo_clips(
                            lp_client,
                            date_range,
                            person_id=lp_person_id,
                            person_ids=multi_ids if len(multi_ids) >= 2 else None,
                        )

                live_clips, live_video_ids = await run.io_bound(fetch_live)
                if live_video_ids:
                    before = len(clips)
                    clips = [c for c in clips if c.asset.id not in live_video_ids]
                    removed = before - len(clips)
                    if removed:
                        logger.info(f"Removed {removed} live photo video components")
                if live_clips:
                    logger.info(f"Adding {len(live_clips)} Live Photo clips")
                    clips.extend(live_clips)

            # Sort all clips (videos + live photos) by date
            clips.sort(key=lambda c: c.asset.file_created_at)

            state.clips = clips
            state.selected_clip_ids = {c.asset.id for c in clips}

            # Load thumbnails and metadata with progress
            status_label.set_text(f"Found {len(clips)} videos. Loading thumbnails...")
            progress_bar.value = 0.1
            await _load_thumbnails_and_metadata_async(clips, status_label, progress_bar)

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
    progress_bar: ui.linear_progress | None = None,
) -> None:
    """Load thumbnails and metadata from cache or API with live progress."""
    state = get_app_state()
    analysis_cache = state.analysis_cache
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        raise RuntimeError("Thumbnail cache not initialized")

    all_asset_ids = [c.asset.id for c in clips]

    # Check cached data (fast local operations)
    cached_thumbnail_ids = set(thumbnail_cache.get_batch(all_asset_ids, "preview").keys())
    cached_metadata = analysis_cache.get_video_metadata_batch(all_asset_ids)

    # Apply cached metadata
    for clip in clips:
        meta = cached_metadata.get(clip.asset.id)
        if meta:
            _apply_metadata(clip, meta)

    # Find missing data
    need_thumbs = [c for c in clips if c.asset.id not in cached_thumbnail_ids]
    need_meta = [c for c in clips if c.asset.id not in cached_metadata]
    total_work = len(need_thumbs) + len(need_meta)

    if total_work == 0:
        return

    done = 0
    batch_size = 10

    # Phase 1: Thumbnails (batched for progress updates)
    for i in range(0, len(need_thumbs), batch_size):
        batch = need_thumbs[i : i + batch_size]

        def fetch_thumb_batch(clips_batch=batch):
            with SyncImmichClient(state.immich_url, state.immich_api_key) as client:
                for clip in clips_batch:
                    try:
                        thumb = client.get_asset_thumbnail(clip.asset.id, size="preview")
                        if thumb:
                            thumbnail_cache.put(clip.asset.id, "preview", thumb)
                    except Exception:
                        pass

        await run.io_bound(fetch_thumb_batch)
        done += len(batch)
        frac = done / total_work
        status_label.set_text(
            f"Thumbnails: {min(i + batch_size, len(need_thumbs))}/{len(need_thumbs)}"
        )
        if progress_bar:
            progress_bar.value = 0.1 + frac * 0.85

    # Phase 2: Metadata probing (batched)
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

    if progress_bar:
        progress_bar.value = 1.0
    status_label.set_text("Done")


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
        # For live photos, probe the video component (not the image asset)
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


def _expand_to_neighbors(
    tagged: list,
    all_live: list,
) -> list:
    """Include untagged live photos that are near tagged ones.

    If photos 1, 2, 3 were taken within the merge window and only 2 is
    tagged with the person, 1 and 3 are clearly from the same moment and
    should be included too.
    """
    from immich_memories.config import get_config

    config = get_config()
    window = config.analysis.live_photo_merge_window_seconds

    tagged_ids = {a.id for a in tagged}
    tagged_times = [a.file_created_at for a in tagged]

    result_ids = set(tagged_ids)
    for asset in all_live:
        if asset.id in result_ids:
            continue
        # Check if this untagged photo is within the merge window of any tagged one
        for t in tagged_times:
            diff = abs((asset.file_created_at - t).total_seconds())
            if diff <= window:
                result_ids.add(asset.id)
                break

    # Build final list preserving chronological order
    by_id = {a.id: a for a in all_live}
    by_id.update({a.id: a for a in tagged})
    result = [by_id[aid] for aid in result_ids if aid in by_id]
    result.sort(key=lambda a: a.file_created_at)
    return result


def _search_live_photos(
    client: SyncImmichClient,
    date_range: DateRange,
    person_id: str | None = None,
    person_ids: list[str] | None = None,
) -> list:
    """Search for live photo assets, handling person filtering.

    When a person is selected, we search by person with NO asset type
    filter, then keep only ``is_live_photo`` results.  This works around
    an Immich quirk where IMAGE+person search omits ``livePhotoVideoId``.

    Without a person filter we use the dedicated live photo endpoint.
    """
    from immich_memories.api.models import Asset

    # Multi-person AND: intersect per-person results
    if person_ids and len(person_ids) >= 2:
        per_person: list[set[str]] = []
        assets_by_id: dict[str, Asset] = {}
        for pid in person_ids:
            results = _search_live_photos(client, date_range, person_id=pid)
            per_person.append({a.id for a in results})
            assets_by_id.update({a.id: a for a in results})
        common = per_person[0]
        for s in per_person[1:]:
            common &= s
        result = [assets_by_id[aid] for aid in common]
        result.sort(key=lambda a: a.file_created_at)
        return result

    # Single person: search by person, no type filter, filter for live photos
    if person_id:
        tagged: list[Asset] = []
        page = 1
        while True:
            result = client._run(
                client._async_client.search_metadata(
                    person_ids=[person_id],
                    taken_after=date_range.start,
                    taken_before=date_range.end,
                    page=page,
                    size=100,
                )
            )
            for asset in result.all_assets:
                if asset.is_live_photo:
                    tagged.append(asset)
            if not result.next_page:
                break
            page += 1

        if not tagged:
            logger.info("Live photo person search: 0 tagged live photos")
            return []

        # Smart grouping: fetch ALL live photos and include untagged
        # neighbors within the merge window of tagged ones.
        all_live = client.get_live_photos_for_date_range(date_range)
        merged = _expand_to_neighbors(tagged, all_live)
        logger.info(
            f"Live photo person search: {len(tagged)} tagged, "
            f"{len(merged)} after neighbor expansion (from {len(all_live)} total)"
        )
        return merged

    # No person filter: use dedicated endpoint
    return client.get_live_photos_for_date_range(date_range)


def _fetch_live_photo_clips(
    client: SyncImmichClient,
    date_range: DateRange,
    person_id: str | None = None,
    person_ids: list[str] | None = None,
) -> tuple[list[VideoClipInfo], set[str]]:
    """Fetch Live Photo assets and convert to VideoClipInfo clips.

    For person filtering we search by person with NO asset type filter,
    then filter client-side for ``is_live_photo``.  Immich doesn't
    populate ``livePhotoVideoId`` when searching IMAGE+person directly.

    Returns:
        Tuple of (live_photo_clips, live_video_ids).
    """
    from immich_memories.config import get_config
    from immich_memories.processing.live_photo_merger import cluster_live_photos

    config = get_config()

    try:
        live_assets = _search_live_photos(
            client,
            date_range,
            person_id=person_id,
            person_ids=person_ids,
        )
    except Exception:
        logger.warning("Failed to fetch live photos", exc_info=True)
        return [], set()

    if not live_assets:
        logger.info("No live photos found in date range")
        return [], set()

    # Collect all video component IDs so we can remove them from regular results
    live_video_ids = {a.live_photo_video_id for a in live_assets if a.live_photo_video_id}

    clusters = cluster_live_photos(
        live_assets,
        merge_window_seconds=config.analysis.live_photo_merge_window_seconds,
    )

    clips: list[VideoClipInfo] = []
    for cluster in clusters:
        first_asset = cluster.assets[0]
        video_id = first_asset.live_photo_video_id
        if not video_id:
            continue

        # Store burst metadata for multi-photo clusters so assembly can merge them
        burst_ids = cluster.video_asset_ids if cluster.count > 1 else None
        burst_trims = cluster.trim_points() if cluster.count > 1 else None

        clip = VideoClipInfo(
            asset=first_asset,
            duration_seconds=cluster.estimated_duration,
            live_burst_video_ids=burst_ids,
            live_burst_trim_points=burst_trims,
        )
        # Propagate favorite from cluster (any photo favorite → clip favorite)
        if cluster.is_favorite:
            clip.asset.is_favorite = True

        clips.append(clip)

    logger.info(
        f"Live Photos: {len(live_assets)} photos → {len(clusters)} clusters → "
        f"{len(clips)} clips ({len(live_video_ids)} video components to filter)"
    )
    return clips, live_video_ids
