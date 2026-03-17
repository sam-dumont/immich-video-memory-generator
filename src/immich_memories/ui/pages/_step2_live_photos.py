"""Live Photo fetching helpers for Step 2 clip loading."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from immich_memories.api.models import VideoClipInfo

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)


def expand_to_neighbors(
    tagged: list,
    all_live: list,
    *,
    merge_window_seconds: float = 10.0,
) -> list:
    """Include untagged live photos that are near tagged ones.

    If photos 1, 2, 3 were taken within the merge window and only 2 is
    tagged with the person, 1 and 3 are clearly from the same moment and
    should be included too.
    """
    window = merge_window_seconds

    tagged_ids = {a.id for a in tagged}
    tagged_times = [a.file_created_at for a in tagged]

    result_ids = tagged_ids.copy()
    for asset in all_live:
        if asset.id in result_ids:
            continue
        for t in tagged_times:
            diff = abs((asset.file_created_at - t).total_seconds())
            if diff <= window:
                result_ids.add(asset.id)
                break

    by_id = {a.id: a for a in all_live}
    by_id.update({a.id: a for a in tagged})
    result = [by_id[aid] for aid in result_ids if aid in by_id]
    result.sort(key=lambda a: a.file_created_at)
    return result


def _search_live_photos_multi_person(
    client: SyncImmichClient,
    date_range: DateRange,
    person_ids: list[str],
    *,
    merge_window_seconds: float = 10.0,
) -> list:
    """Intersect live photos across multiple persons."""
    from immich_memories.api.models import Asset

    per_person: list[set[str]] = []
    assets_by_id: dict[str, Asset] = {}
    for pid in person_ids:
        results = search_live_photos(
            client, date_range, person_id=pid, merge_window_seconds=merge_window_seconds
        )
        per_person.append({a.id for a in results})
        assets_by_id.update({a.id: a for a in results})
    common = per_person[0]
    for s in per_person[1:]:
        common &= s
    result = [assets_by_id[aid] for aid in common]
    result.sort(key=lambda a: a.file_created_at)
    return result


def _search_live_photos_for_person(
    client: SyncImmichClient,
    date_range: DateRange,
    person_id: str,
    *,
    merge_window_seconds: float = 10.0,
) -> list:
    """Fetch live photos tagged with a specific person, then expand to neighbors."""
    from immich_memories.api.models import Asset

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

    all_live = client.get_live_photos_for_date_range(date_range)
    merged = expand_to_neighbors(tagged, all_live, merge_window_seconds=merge_window_seconds)
    logger.info(
        f"Live photo person search: {len(tagged)} tagged, "
        f"{len(merged)} after neighbor expansion (from {len(all_live)} total)"
    )
    return merged


def search_live_photos(
    client: SyncImmichClient,
    date_range: DateRange,
    person_id: str | None = None,
    person_ids: list[str] | None = None,
    *,
    merge_window_seconds: float = 10.0,
) -> list:
    """Search for live photo assets, handling person filtering.

    When a person is selected, we search by person with NO asset type
    filter, then keep only ``is_live_photo`` results.  This works around
    an Immich quirk where IMAGE+person search omits ``livePhotoVideoId``.

    Without a person filter we use the dedicated live photo endpoint.
    """
    if person_ids and len(person_ids) >= 2:
        return _search_live_photos_multi_person(
            client, date_range, person_ids, merge_window_seconds=merge_window_seconds
        )
    if person_id:
        return _search_live_photos_for_person(
            client, date_range, person_id, merge_window_seconds=merge_window_seconds
        )
    return client.get_live_photos_for_date_range(date_range)


def fetch_live_photo_clips(
    client: SyncImmichClient,
    date_range: DateRange,
    person_id: str | None = None,
    person_ids: list[str] | None = None,
    *,
    config=None,
) -> tuple[list[VideoClipInfo], set[str]]:
    """Fetch Live Photo assets and convert to VideoClipInfo clips.

    Returns:
        Tuple of (live_photo_clips, live_video_ids).
    """
    from immich_memories.processing.live_photo_merger import cluster_live_photos

    if config is None:
        from immich_memories.config import get_config

        config = get_config()

    merge_window = config.analysis.live_photo_merge_window_seconds

    try:
        live_assets = search_live_photos(
            client,
            date_range,
            person_id=person_id,
            person_ids=person_ids,
            merge_window_seconds=merge_window,
        )
    except Exception:
        logger.warning("Failed to fetch live photos", exc_info=True)
        return [], set()

    if not live_assets:
        logger.info("No live photos found in date range")
        return [], set()

    live_video_ids = {a.live_photo_video_id for a in live_assets if a.live_photo_video_id}

    clusters = cluster_live_photos(
        live_assets,
        merge_window_seconds=merge_window,
    )

    clips: list[VideoClipInfo] = []
    for cluster in clusters:
        first_asset = cluster.assets[0]
        video_id = first_asset.live_photo_video_id
        if not video_id:
            continue

        burst_ids = cluster.video_asset_ids if cluster.count > 1 else None
        burst_trims = cluster.trim_points() if cluster.count > 1 else None

        clip = VideoClipInfo(
            asset=first_asset,
            duration_seconds=cluster.estimated_duration,
            live_burst_video_ids=burst_ids,
            live_burst_trim_points=burst_trims,
        )
        if cluster.is_favorite:
            clip.asset.is_favorite = True

        clips.append(clip)

    logger.info(
        f"Live Photos: {len(live_assets)} photos → {len(clusters)} clusters → "
        f"{len(clips)} clips ({len(live_video_ids)} video components to filter)"
    )
    return clips, live_video_ids
