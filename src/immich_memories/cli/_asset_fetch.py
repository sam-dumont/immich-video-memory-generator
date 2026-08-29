"""What a memory asks Immich for, given its windows and the people in it.

The rules here answer to the API rather than to the pipeline: one person
filters by ``person_id``, several use the requested AND/OR rule, and windows
that touch must not hand the same asset over twice. Live Photos arrive
as clips and take their own video out of the plain video list, so nothing is
offered to selection in both forms.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from immich_memories.api.person_scope import photos_in_window, videos_in_window
from immich_memories.cli._helpers import print_info, print_success, print_warning
from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay


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


def _report_per_window(
    assets: list,
    date_ranges: list[DateRange],
    person_ids: list[str],
    history_from: int | None = None,
) -> None:
    """Say what each window contributed, and shout when one contributed nothing.

    A combined total hides the failure that matters on a multi-window memory: a
    then-and-now whose older half is empty still renders, as a memory of the
    recent half alone, and without this it looks like a clean run.

    ``history_from`` marks where a memory's expected-sparse tail begins. A
    birthday memory looks at the same single day in several earlier years, and
    most of those days hold nothing — warning once per empty year would bury
    the one warning that means something, so the tail is counted instead of
    listed. Everything before the mark keeps its own warning.
    """
    if len(date_ranges) < 2:
        return

    from immich_memories.memory_types.eras import count_by_era

    counts = count_by_era([a.file_created_at for a in assets], date_ranges)
    labels = [_window_label(r) for r in date_ranges]
    anchor = _anchor_name(assets, person_ids)

    reported = len(counts) if history_from is None else history_from
    print_info(
        " · ".join(
            f"{label}: {n}" for label, n in zip(labels[:reported], counts[:reported], strict=True)
        )
    )
    for label, n in zip(labels[:reported], counts[:reported], strict=True):
        if n == 0:
            print_warning(_empty_window_warning(label, anchor))

    history = counts[reported:]
    if history:
        _report_history(history, anchor)


def _report_history(counts: list[int], anchor: str | None) -> None:
    """One line for the whole tail, and a warning only if none of it landed."""
    held = sum(1 for n in counts if n)
    print_info(f"history: {held} of {len(counts)} earlier windows hold material")
    if held == 0:
        who = f"{anchor} appears in none" if anchor else "nothing was found in any"
        print_warning(f"{who} of the {len(counts)} earlier windows — the memory has no history")


def fetch_photos(
    *,
    client: SyncImmichClient,
    date_ranges: list[DateRange],
    person_ids: list[str],
    person_match: str = "and",
) -> list:
    """Fetch every photograph in the memory's windows, honouring the person filter.

    Several people use the explicit AND/OR rule videos follow. Person-scoped
    discovery returns only assets Immich tagged with a requested person; it
    does not pull untagged temporal neighbours into the pool.

    A window that cannot be read costs that window, not the run. Live Photos
    used to be fetched through a wrapper that said so out loud; their stills
    arrive here now, and in a large library there is always an asset mid-import
    or just deleted, so a 404 is a Tuesday rather than an edge case.
    """
    from immich_memories.api.immich import ImmichAPIError

    photos: list = []
    seen: set[str] = set()
    for dr in date_ranges:
        try:
            batch = photos_in_window(
                client,
                person_ids,
                dr,
                person_match=person_match,
            )
        except (ImmichAPIError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("Failed to fetch photos for one window: %s", exc, exc_info=True)
            continue
        for photo in batch:
            if photo.id not in seen:
                seen.add(photo.id)
                photos.append(photo)

    return photos


def fetch_videos(
    *,
    client: SyncImmichClient,
    progress: ProgressDisplay,
    date_ranges: list[DateRange],
    person_ids: list[str],
    person_match: str = "and",
    history_from: int | None = None,
) -> list:
    """Fetch the video assets for the memory's windows.

    Live Photos are not fetched here any more. Their stills come back with the
    photographs, because that is what they are; the video half is dropped from
    this pool once the photographs are known
    (live_photo_pipeline.drop_live_photo_components).

    ``history_from`` names where the memory's expected-sparse windows start, so
    the per-window report can summarise them rather than warn about each.
    """
    task = progress.add_task("Fetching videos...", total=None)

    all_assets = []
    for dr in date_ranges:
        all_assets.extend(videos_in_window(client, person_ids, dr, person_match=person_match))

    # Deduplicate across date ranges
    seen: dict[str, object] = {}
    assets = []
    for a in all_assets:
        if a.id not in seen:
            seen[a.id] = True
            assets.append(a)

    progress.update(task, completed=True)
    print_success(f"Found {len(assets)} videos")
    _report_per_window(assets, date_ranges, person_ids, history_from)

    return assets
