"""What a memory asks Immich for, given its windows and the people in it.

The rules here answer to the API rather than to the pipeline: one person
filters by ``person_id``, several ask for the moments holding all of them, and
windows that touch must not hand the same asset over twice. Live Photos arrive
as clips and take their own video out of the plain video list, so nothing is
offered to selection in both forms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from immich_memories.cli._helpers import print_success
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config


def fetch_photos(
    *,
    client: SyncImmichClient,
    date_ranges: list[DateRange],
    person_ids: list[str],
) -> list:
    """Fetch still photos for the memory's windows, honouring the person filter.

    Several people means the photos holding all of them, the same rule videos
    and Live Photos already follow.
    """
    photos: list = []
    seen: set[str] = set()
    for dr in date_ranges:
        batch = client.get_photos_for_date_range(
            dr,
            person_id=person_ids[0] if len(person_ids) == 1 else None,
            person_ids=person_ids if len(person_ids) > 1 else None,
        )
        for photo in batch:
            if photo.id not in seen:
                seen.add(photo.id)
                photos.append(photo)
    return photos


def fetch_videos_and_live_photos(
    *,
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    date_ranges: list[DateRange],
    person_ids: list[str],
    use_live_photos: bool,
) -> tuple[list, list]:
    """Fetch video assets and optionally live photo clips.

    Returns (assets, live_photo_clips).
    """
    task = progress.add_task("Fetching videos...", total=None)

    all_assets = []
    for dr in date_ranges:
        if len(person_ids) > 1:
            # Naming several people asks for the moments that hold all of them,
            # not the union of their solo reels. Live photos already intersect.
            batch = client.get_videos_for_all_persons(person_ids, dr)
        elif len(person_ids) == 1:
            batch = client.get_videos_for_person_and_date_range(person_ids[0], dr)
        else:
            batch = client.get_videos_for_date_range(dr)
        all_assets.extend(batch)

    # Deduplicate across date ranges
    seen: dict[str, object] = {}
    assets = []
    for a in all_assets:
        if a.id not in seen:
            seen[a.id] = True
            assets.append(a)

    progress.update(task, completed=True)
    print_success(f"Found {len(assets)} videos")

    live_photo_clips: list = []
    if use_live_photos:
        from immich_memories.analysis.live_photo_pipeline import fetch_live_photo_clips

        lp_task = progress.add_task("Fetching live photos...", total=None)
        all_lp_clips: list = []
        all_lp_video_ids: set[str] = set()
        for dr in date_ranges:
            lp_clips, lp_vid_ids = fetch_live_photo_clips(
                client,
                dr,
                person_id=person_ids[0] if len(person_ids) == 1 else None,
                person_ids=person_ids if len(person_ids) > 1 else None,
                config=config,
            )
            all_lp_clips.extend(lp_clips)
            all_lp_video_ids.update(lp_vid_ids)

        if all_lp_video_ids:
            assets = [a for a in assets if a.id not in all_lp_video_ids]
        live_photo_clips = all_lp_clips
        progress.update(lp_task, completed=True)
        if live_photo_clips:
            print_success(f"Found {len(live_photo_clips)} live photo clips")

    return assets, live_photo_clips
