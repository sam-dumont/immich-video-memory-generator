"""What a memory asks Immich for, given its windows and the people in it.

The rules here answer to the API rather than to the pipeline: one person
filters by ``person_id``, several ask for the moments holding all of them, and
windows that touch must not hand the same asset over twice. Live Photos arrive
as clips and take their own video out of the plain video list, so nothing is
offered to selection in both forms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from immich_memories.cli._helpers import print_info, print_success, print_warning
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config


def _window_label(date_range: DateRange) -> str:
    """Name a window by its year, or by its dates when it is not a whole one."""
    if date_range.start.year == date_range.end.year:
        whole_year = (date_range.start.month, date_range.start.day) == (1, 1) and (
            date_range.end.month,
            date_range.end.day,
        ) == (12, 31)
        if whole_year:
            return str(date_range.start.year)
    return f"{date_range.start:%Y-%m-%d}..{date_range.end:%Y-%m-%d}"


def _anchor_name(assets: list, person_ids: list[str]) -> str | None:
    """The display name of the single person this fetch was narrowed to.

    Read off whichever window did return material: Immich filters server-side,
    so the empty window holds no Person object to take the name from.
    """
    if len(person_ids) != 1:
        return None
    wanted = person_ids[0]
    for asset in assets:
        for person in getattr(asset, "people", None) or []:
            if person.id == wanted and getattr(person, "name", None):
                return str(person.name)
    return None


def _empty_window_warning(label: str, anchor: str | None) -> str:
    """Two different diagnoses that look identical from the count alone.

    An unfiltered window with nothing in it means the library has nothing from
    that period. A window narrowed to one person means the period is fine and
    *they* are not in it — which is the memory type failing, not the library.
    """
    if anchor:
        return (
            f"{anchor} does not appear in {label} — a memory anchored on them "
            f"cannot span both windows"
        )
    return f"no videos found for {label} — that window contributes nothing"


def _report_per_window(assets: list, date_ranges: list[DateRange], person_ids: list[str]) -> None:
    """Say what each window contributed, and shout when one contributed nothing.

    A combined total hides the failure that matters on a multi-window memory: a
    then-and-now whose older half is empty still renders, as a memory of the
    recent half alone, and without this it looks like a clean run.
    """
    if len(date_ranges) < 2:
        return

    from immich_memories.memory_types.eras import count_by_era

    counts = count_by_era([a.file_created_at for a in assets], date_ranges)
    labels = [_window_label(r) for r in date_ranges]
    anchor = _anchor_name(assets, person_ids)
    print_info(" · ".join(f"{label}: {n}" for label, n in zip(labels, counts, strict=True)))
    for label, n in zip(labels, counts, strict=True):
        if n == 0:
            print_warning(_empty_window_warning(label, anchor))


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
    _report_per_window(assets, date_ranges, person_ids)

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
