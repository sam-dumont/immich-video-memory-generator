"""Step 2: Clip loading and cached analysis helpers."""

from __future__ import annotations

import contextlib
import logging

from nicegui import run, ui

from immich_memories.analysis.cache_projection import (
    apply_cached_segment,
    is_compatible_analysis_cache,
)
from immich_memories.analysis.editorial_source import resolve_named_expression
from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import VideoClipInfo
from immich_memories.api.person_scope import photos_in_window, videos_in_window
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.security import sanitize_error_message
from immich_memories.ui.nicegui_compat import io_bound_result
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def _set_phase_status(status_label, event: PhaseEvent) -> None:
    """Render the shared operational message without persisting UI-only discovery."""
    status_label.set_text(event.message)


def _ui_phase(
    phase: OperationalPhase,
    message: str,
    *,
    current: int = 0,
    total: int = 0,
) -> PhaseEvent:
    return PhaseEvent(phase, current, total, message, 0.0)


def _person_fetch_args(state) -> tuple[list[str], dict]:
    """Keep explicit expression handling separate from the legacy flat query."""
    if state.memory_preset_params.get("person_expression") is not None or getattr(
        state, "person_expression_error", None
    ):
        expression = state.resolved_person_expression()
        return [], {"person_expression": expression}
    return state.person_ids, {}


def _fetch_album(state) -> tuple[list[VideoClipInfo], list]:
    """Read the chosen album as clip and photo pools.

    Album mode replaces date-range discovery entirely: the album is the pool.
    """
    from immich_memories.analysis.album_source import (
        album_target_minutes,
        fetch_album_media,
    )
    from immich_memories.api.immich import SyncImmichClient

    _, expression_args = _person_fetch_args(state)
    if expression_args:
        raise ValueError("Grouped people conditions are not supported for album memories")
    with SyncImmichClient(
        base_url=state.immich_url,
        api_key=state.immich_api_key,
        api_version=state.immich_api_version,
    ) as client:
        album = client.resolve_album(state.album_id)
        media = fetch_album_media(
            client,
            album,
            config=state.config,
            use_live_photos=state.include_live_photos,
            use_photos=state.include_photos,
        )
    if media.date_range is not None:
        state.date_ranges = [media.date_range]
    clips, _ = _build_clips(media.videos)
    clips.sort(key=lambda clip: clip.asset.file_created_at)
    photos = media.photos.copy()
    if state.duration_mode == "auto":
        state.target_duration = album_target_minutes(clips, photos)
    return clips, photos


def _dedup_by_id(assets: list) -> list:
    """First occurrence wins, order preserved.

    Windows can overlap — a holiday window two days either side of the date
    collides with itself when two requested years are consecutive leap-adjusted
    neighbours, and On This Day windows overlap outright if years_back exceeds
    the gap. The same asset must not be analysed twice.
    """
    seen: set[str] = set()
    unique = []
    for asset in assets:
        if asset.id not in seen:
            seen.add(asset.id)
            unique.append(asset)
    return unique


def _scope_event_media(assets: list, state) -> list:
    """Narrow fetched metadata before clip probing or photo thumbnail loading."""
    if state.memory_type != "special_day":
        return assets
    from immich_memories.analysis.special_event_scope import (
        select_source_members,
        validate_special_event_scope,
    )

    params = state.memory_preset_params
    members = validate_special_event_scope(params.get("event_id"), params.get("asset_ids", ()))
    return list(select_source_members(assets, members if members else None))


def _fetch_assets(state) -> list:
    """Blocking: fetch video assets from Immich API, one query per window."""
    person_ids, expression_args = _person_fetch_args(state)
    with SyncImmichClient(
        base_url=state.immich_url,
        api_key=state.immich_api_key,
        api_version=state.immich_api_version,
    ) as client:
        if expression_args:
            expression_args["person_expression"] = resolve_named_expression(
                state.person_expression, client.get_all_people(with_hidden=True)
            )
        assets: list = []
        for date_range in state.date_ranges:
            assets.extend(
                videos_in_window(
                    client,
                    person_ids,
                    date_range,
                    person_match=state.person_match,
                    **expression_args,
                )
            )
        return _scope_event_media(_dedup_by_id(assets), state)


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
    """Retain raw metadata so the story selector can inspect every requested source."""
    clips = []
    for asset in assets:
        duration = asset.duration_seconds or 0
        clips.append(VideoClipInfo(asset=asset, duration_seconds=duration))
    return clips, 0


def _fetch_photos(state) -> list:
    """Fetch photo assets (blocking), one query per window."""
    person_ids, expression_args = _person_fetch_args(state)
    with SyncImmichClient(
        base_url=state.immich_url,
        api_key=state.immich_api_key,
        api_version=state.immich_api_version,
    ) as client:
        if expression_args:
            expression_args["person_expression"] = resolve_named_expression(
                state.person_expression, client.get_all_people(with_hidden=True)
            )
        photos: list = []
        for date_range in state.date_ranges:
            photos.extend(
                photos_in_window(
                    client,
                    person_ids,
                    date_range,
                    person_match=state.person_match,
                    **expression_args,
                )
            )
        return _scope_event_media(_dedup_by_id(photos), state)


def _set_initial_selection(clips: list[VideoClipInfo], state) -> None:
    """Make every discovered clip eligible for the user's review."""
    state.selected_clip_ids = {c.asset.id for c in clips}


async def _finish_load(state, clips, photo_assets, status_label, progress_bar) -> None:
    """Shared tail of both loading paths: commit the pools, then fetch thumbnails."""
    state.clips = clips
    _set_initial_selection(clips, state)
    state.photo_assets = photo_assets
    state.selected_photo_ids = {a.id for a in photo_assets}

    total = f"Found {len(clips)} videos"
    if photo_assets:
        total += f" and {len(photo_assets)} photos"
    where = f" in {state.album_name}" if state.album_name else ""
    _set_phase_status(
        status_label,
        _ui_phase(OperationalPhase.DOWNLOAD, f"{total}{where}. Loading thumbnails..."),
    )
    if progress_bar is not None:
        progress_bar.value = 0.1
    await _load_thumbnails_async(clips, status_label, progress_bar)
    _hydrate_and_report_cached_analysis(state, clips, status_label)
    if photo_assets:
        await _load_photo_thumbnails_async(photo_assets, status_label)


async def _collect_date_range_media(state, status_label, progress_bar):
    """Discover the clip and photo pools for a date-range memory."""
    if not state.date_ranges:
        raise ValueError("No date range configured")

    assets = _filter_near_home(await io_bound_result(_fetch_assets, state), state)
    _set_phase_status(
        status_label,
        _ui_phase(
            OperationalPhase.DISCOVERY,
            f"Found {len(assets)} assets. Filtering...",
            current=len(assets),
            total=len(assets),
        ),
    )
    progress_bar.value = 0.05

    clips, _ = _build_clips(assets)

    photo_assets = []
    if state.include_photos:
        status_label.set_text("Fetching photos...")
        photo_assets = await io_bound_result(_fetch_photos, state)
        logger.info(f"Found {len(photo_assets)} photos")

    # A Live Photo's video half is part of a photograph. It is dropped from the
    # video pool rather than fetched separately, so a burst can never compete
    # against the still it belongs to.
    clips = [
        c
        for c in clips
        if c.asset.id not in {p.live_photo_video_id for p in photo_assets if p.live_photo_video_id}
    ]

    clips.sort(key=lambda c: c.asset.file_created_at)
    return clips, photo_assets


def ensure_caches(state) -> None:
    """Open the analysis and thumbnail caches once per session, on first need."""
    from immich_memories.config import get_config

    if state.analysis_cache is None:
        from immich_memories.cache import VideoAnalysisCache

        state.analysis_cache = VideoAnalysisCache(db_path=get_config().cache.database_path)
    if state.thumbnail_cache is None:
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        config = get_config()
        state.thumbnail_cache = ThumbnailCache(
            cache_dir=config.cache.cache_path / "thumbnails",
            max_size_mb=config.cache.thumbnail_cache_max_size_mb,
        )


async def load_pool(state, status_label, progress_bar) -> None:
    """Discover the brief's media and commit it as the session's pool.

    An album brief takes the album whole; every other brief fetches each of its
    windows. Either way the pool lands on the state with everything eligible.
    """
    _set_phase_status(
        status_label,
        _ui_phase(OperationalPhase.DISCOVERY, "Fetching videos from Immich..."),
    )
    progress_bar.value = 0.02
    if state.album_id:
        clips, photo_assets = await io_bound_result(_fetch_album, state)
    else:
        clips, photo_assets = await _collect_date_range_media(state, status_label, progress_bar)
    await _finish_load(state, clips, photo_assets, status_label, progress_bar)


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
            await load_pool(state, status_label, progress_bar)

            loading_dialog.close()
            ui.navigate.to("/step2")

        except Exception as e:  # WHY: UI graceful degradation
            loading_dialog.close()
            ui.notify(f"Failed to load videos: {sanitize_error_message(str(e))}", type="negative")
            logger.exception("Failed to load clips")

    ui.timer(0.1, do_load, once=True)


async def _load_thumbnails_async(
    clips: list[VideoClipInfo],
    status_label: ui.label,
    progress_bar: ui.linear_progress | None = None,
) -> None:
    """Fetch the thumbnails the cache lacks, with live progress."""
    state = get_app_state()
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        raise RuntimeError("Thumbnail cache not initialized")

    cached_thumbnail_ids = thumbnail_cache.cached_ids([c.asset.id for c in clips], "preview")
    need_thumbs = [c for c in clips if c.asset.id not in cached_thumbnail_ids]
    total_work = len(need_thumbs)

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
    # One client for the whole fetch rather than one per batch of ten: each
    # carries an httpx connection pool and a private event loop, and 500
    # thumbnails meant fifty of them, all discarded after ten requests.
    for i in range(0, len(need_thumbs), batch_size):
        batch = need_thumbs[i : i + batch_size]

        def fetch_thumb_batch(clips_batch=batch):
            with SyncImmichClient(
                base_url=state.immich_url,
                api_key=state.immich_api_key,
                api_version=state.immich_api_version,
            ) as client:
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


def _hydrate_compatible_cached_analysis(state, clips: list[VideoClipInfo]) -> int:
    """Surface current-model cache entries; stale entries deliberately remain misses."""
    config = state.config
    analysis_cache = state.analysis_cache
    state.cached_analysis_ids = set()
    if config is None or analysis_cache is None:
        return 0

    for clip in clips:
        cached = analysis_cache.get_analysis(clip.asset.id)
        if not is_compatible_analysis_cache(cached, config):
            continue
        best_segment = cached.get_best_segment()
        if best_segment is None:
            continue
        apply_cached_segment(clip, best_segment)
        state.clip_segments[clip.asset.id] = (best_segment.start_time, best_segment.end_time)
        state.cached_analysis_ids.add(clip.asset.id)

    return len(state.cached_analysis_ids)


def _hydrate_and_report_cached_analysis(state, clips, status_label) -> None:
    """Hydrate compatible analysis without adding branches to the loading workflow."""
    hydrated = _hydrate_compatible_cached_analysis(state, clips)
    if hydrated:
        status_label.set_text(f"Loaded {hydrated} current cached analyses")


async def _load_photo_thumbnails_async(
    photo_assets: list,
    status_label: ui.label,
) -> None:
    """Load thumbnails for photo assets from Immich."""
    state = get_app_state()
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        return

    photo_ids = [a.id for a in photo_assets]
    cached = thumbnail_cache.cached_ids(photo_ids, "preview")
    need = [a for a in photo_assets if a.id not in cached]

    if not need:
        return

    batch_size = 10
    for i in range(0, len(need), batch_size):
        batch = need[i : i + batch_size]

        def fetch_batch(assets_batch=batch):
            with SyncImmichClient(
                base_url=state.immich_url,
                api_key=state.immich_api_key,
                api_version=state.immich_api_version,
            ) as client:
                for asset in assets_batch:
                    with contextlib.suppress(Exception):
                        thumb = client.get_asset_thumbnail(asset.id, size="preview")
                        if thumb:
                            thumbnail_cache.put(asset.id, "preview", thumb)

        await run.io_bound(fetch_batch)
        status_label.set_text(f"Photo thumbnails: {min(i + batch_size, len(need))}/{len(need)}")
