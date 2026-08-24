"""Scanning a library for days worth resurfacing.

Lives here rather than in a script because it is meant to run on a schedule:
the point of the catalogue is a memory nobody asked for — five years to the
day since the wedding, ten since the race — and that needs the days found in
advance, not while a video is waiting to render.

What it skips matters as much as what it finds. A holiday already has its own
memory, and every day inside a trip clears the structural bar here without
being remarkable on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.special_day import (
    active_hours,
    ask_if_special,
    candidate_days,
    days_covered_by_trips,
    event_window,
    run_extent,
    sample_across_day,
)
from immich_memories.analysis.trip_detection import detect_trips, haversine_km
from immich_memories.config_models_automation import TripsConfig
from immich_memories.memory_types.date_builders import KNOWN_HOLIDAYS, resolve_holiday

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 6


@dataclass(frozen=True)
class DiscoveredDay:
    """A day the scan thinks is worth a memory of its own."""

    day: date
    title: str
    subtitle: str
    what: str
    photos: int
    window: tuple[datetime, datetime] | None
    # How long the day stayed awake, and when it did. The run is keyed by the
    # date it began and can end on another one, so its extent is the only
    # honest scope for a memory of it — the calendar day stops at midnight.
    active_hours: int = 0
    run_start: datetime | None = None
    run_end: datetime | None = None


def holidays_in(year: int, extra: Iterable[str] = ()) -> set[date]:
    """Dates a holiday memory already covers.

    Nothing is defined here: date_builders owns which holidays exist and when
    they fall, moving ones included. Adding one there is enough for it to be
    skipped here too.
    """
    covered: set[date] = set()
    for name in (*KNOWN_HOLIDAYS, *extra):
        try:
            covered.add(resolve_holiday(name, year))
        except ValueError:
            logger.debug("Not a holiday this build knows: %r", name)
    return covered


def _shot_here(assets: list, analysis_config: Any) -> list:
    """Whatever of this year the library's own camera actually made."""
    from immich_memories.analysis.source_filter import not_shot_here

    if analysis_config is None:
        return assets
    patterns = getattr(analysis_config, "exclude_filename_patterns", ())
    stills_need_a_camera = getattr(analysis_config, "exclude_stills_without_camera_exif", False)
    kept = [
        asset
        for asset in assets
        if not not_shot_here(asset, patterns=patterns, stills_need_a_camera=stills_need_a_camera)
    ]
    if len(kept) < len(assets):
        logger.info(
            "Source filter: %d of %d assets were not shot here",
            len(assets) - len(kept),
            len(assets),
        )
    return kept


def _kept_away_from_home(items: list, home: tuple[float, float], min_km: float) -> bool:
    """Did the day's located pictures happen somewhere other than home?

    The holiday memory covers the holiday as it is actually kept — at home,
    with the people who keep it. A day that merely falls on the same date and
    was spent 67 km away at a race circuit is not that holiday, and dropping
    it on the date alone lost a day that would have ranked third in its year.

    No coordinates at all is not evidence against the holiday, so those days
    stay skipped exactly as before.
    """
    away = at_home = 0
    for asset in items:
        exif = getattr(asset, "exif_info", None)
        lat = getattr(exif, "latitude", None) if exif else None
        lon = getattr(exif, "longitude", None) if exif else None
        if lat is None or lon is None:
            continue
        if haversine_km(lat, lon, *home) >= min_km:
            away += 1
        else:
            at_home += 1
    return away > at_home


def _drop_the_holidays_it_actually_was(
    candidates: dict[date, list],
    holidays: set[date],
    home: tuple[float, float] | None,
    min_km: float,
) -> dict[date, list]:
    """Keep the days whose evidence disagrees with the holiday they fall on."""
    kept: dict[date, list] = {}
    for day, items in candidates.items():
        if day not in holidays:
            kept[day] = items
        elif home and _kept_away_from_home(items, home, min_km):
            logger.info("%s falls on a holiday but was spent away from home; keeping it", day)
            kept[day] = items
        else:
            logger.info("Skipping %s: a holiday, and the day never left home", day)
    return kept


def scan_year(
    assets: list,
    *,
    llm_config: Any,
    home: tuple[float, float] | None,
    thumbnail_for: Any = None,
    ask: int = 6,
    extra_holidays: Iterable[str] = (),
    analysis_config: Any = None,
    trips_config: TripsConfig | None = None,
) -> list[DiscoveredDay]:
    """Find the days in one year's assets that stand out, and name them.

    Anything generation would throw away is removed first, so the scan judges
    the same library a memory could actually be cut from. Measured on a real
    day the scan called special: 37 of its 223 assets were received or
    downloaded rather than shot, and they counted toward the day's volume and
    its active hours and could be sampled into the prompt — so the model
    narrated pictures nobody in the library had taken.
    """
    if not assets:
        return []

    assets = _shot_here(assets, analysis_config)
    if not assets:
        return []

    trips = trips_config or TripsConfig()
    year = assets[0].file_created_at.year
    # Dates only: the trip's name is never read here, and asking for one
    # is a live request per trip for every year of the scan.
    away = (
        days_covered_by_trips(
            detect_trips(
                assets,
                *home,
                min_distance_km=trips.min_distance_km,
                min_duration_days=trips.min_duration_days,
                max_gap_days=trips.max_gap_days,
                name_locations=False,
            )
        )
        if home
        else set()
    )
    holidays = holidays_in(year, extra_holidays)

    off_trip = candidate_days(assets, away_days=away)
    candidates = _drop_the_holidays_it_actually_was(off_trip, holidays, home, trips.min_distance_km)
    logger.info(
        "%d: %d candidate days, %d dates covered by trips, %d dropped as the holiday they fell on",
        year,
        len(candidates),
        len(away),
        len(off_trip) - len(candidates),
    )

    found: list[DiscoveredDay] = []
    for day, items in sorted(candidates.items(), key=lambda kv: -len(kv[1]))[:ask]:
        thumbnails = _thumbnails_for(items, thumbnail_for)
        verdict = ask_if_special(items, llm_config, thumbnails=thumbnails)
        if not verdict.special or not (verdict.title or verdict.what):
            continue
        started, ended = run_extent(items) or (None, None)
        found.append(
            DiscoveredDay(
                day=day,
                title=verdict.title,
                subtitle=verdict.subtitle,
                what=verdict.what,
                photos=len(items),
                # The model read the day's own timestamps and what was in the
                # frames; event_window only knows where the pictures were.
                window=verdict.window or event_window(items),
                active_hours=active_hours(items),
                run_start=started,
                run_end=ended,
            )
        )
    return found


def _thumbnails_for(items: list, thumbnail_for: Any) -> list[tuple[Any, bytes]]:
    """The day's sample, each asset paired with the picture drawn for it.

    Paired rather than two parallel lists: a thumbnail that fails to download
    has to take its line out of the prompt with it, or every line after it
    describes the picture before it.
    """
    if thumbnail_for is None:
        return []
    tiles: list[tuple[Any, bytes]] = []
    for asset in sample_across_day(items, count=SAMPLE_SIZE):
        try:
            tiles.append((asset, thumbnail_for(asset.id)))
        except Exception as exc:  # noqa: BLE001, PERF203 - one missing tile is not a failure
            logger.debug("No thumbnail for %s: %s", asset.id, type(exc).__name__)
    return tiles


def same_day_in(day: date, year: int) -> date:
    """The same calendar day in another year; 29 February falls back to the 28th."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def anniversaries_due(
    catalogue: Iterable[DiscoveredDay],
    on: date,
    *,
    window_days: int = 3,
) -> list[tuple[DiscoveredDay, int]]:
    """Discovered days whose anniversary falls near a date, roundest first.

    Ten years reads louder than nine, which is the whole appeal of arriving
    unannounced.

    The candidate is looked for in the years either side of the check as well
    as its own. A day at the end of December has its anniversary a few days
    before a check in early January, and trying only the check's own year put
    that candidate 364 days away — while counting the years to the calendar
    year rather than to the anniversary itself, which read eleven years for a
    tenth.
    """
    due: list[tuple[DiscoveredDay, int]] = []
    for entry in catalogue:
        for year in (on.year - 1, on.year, on.year + 1):
            years = year - entry.day.year
            if years < 1:
                continue
            if abs((same_day_in(entry.day, year) - on).days) <= window_days:
                due.append((entry, years))
                break
    return sorted(
        due, key=lambda pair: (0 if pair[1] % 10 == 0 else 1 if pair[1] % 5 == 0 else 2, -pair[1])
    )
