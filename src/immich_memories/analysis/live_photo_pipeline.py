"""What happens to a Live Photo on its way into the pool.

Almost nothing, now. A Live Photo is a photograph: its still arrives with the
photographs and competes as one, and whether the burst is worth showing as
motion is a rendering question asked later
(analysis/motion_rendering.py). All this has to do is keep the video half out
of the video pool, where it would compete as footage against its own still.

One thing does need doing: Immich tags ONE frame of a burst with a person, so
a memory filtered by person gets that frame alone and the burst has nothing to
stitch to. The neighbours are pulled back in here, or a person memory silently
loses the motion its moments actually had.

Shared between CLI and UI — no NiceGUI imports allowed here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from immich_memories.api.models import Asset

logger = logging.getLogger(__name__)


def drop_live_photo_components(videos: list[Asset], photos: list[Asset]) -> list[Asset]:
    """Remove the video half of a Live Photo from the video pool.

    A Live Photo's video component is part of a photograph, not footage
    somebody shot. Left in the pool it competes as a video against the still it
    belongs to, and the same instant can ship twice.

    The exclusion set comes from the photographs themselves, so nothing has to
    be fetched twice to learn it.
    """
    components = {p.live_photo_video_id for p in photos if p.live_photo_video_id}
    if not components:
        return videos
    kept = [v for v in videos if v.id not in components]
    if len(kept) != len(videos):
        logger.info(
            "Live Photos: %d video components dropped from the video pool",
            len(videos) - len(kept),
        )
    return kept


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


def with_burst_neighbours(
    client: Any,
    photos: list[Asset],
    *,
    date_ranges: list[Any],
    merge_window_seconds: float,
) -> list[Asset]:
    """Add the untagged frames of any burst a fetched photograph belongs to.

    Only asked when a Live Photo actually came back, so a person's plain
    photographs cost no extra request. A window that cannot be read costs its
    neighbours, not the run.
    """
    if not any(getattr(p, "live_photo_video_id", None) for p in photos):
        return photos

    from immich_memories.api.immich import ImmichAPIError

    all_live: list[Asset] = []
    for date_range in date_ranges:
        try:
            all_live.extend(client.get_live_photos_for_date_range(date_range))
        except (ImmichAPIError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("Could not read a window's Live Photos: %s", exc, exc_info=True)

    if not all_live:
        return photos

    known = {p.id for p in photos}
    expanded = expand_to_neighbors(photos, all_live, merge_window_seconds=merge_window_seconds)
    gained = [a for a in expanded if a.id not in known]
    if gained:
        logger.info(
            "Live Photos: %d untagged burst frame(s) pulled in beside a tagged one",
            len(gained),
        )
    return [*photos, *gained]
