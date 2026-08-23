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
    ask_if_special,
    candidate_days,
    days_covered_by_trips,
    event_window,
    sample_across_day,
)
from immich_memories.analysis.trip_detection import detect_trips
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


def scan_year(
    assets: list,
    *,
    llm_config: Any,
    home: tuple[float, float] | None,
    thumbnail_for: Any = None,
    ask: int = 6,
    extra_holidays: Iterable[str] = (),
) -> list[DiscoveredDay]:
    """Find the days in one year's assets that stand out, and name them."""
    if not assets:
        return []

    year = assets[0].file_created_at.year
    away = days_covered_by_trips(detect_trips(assets, *home)) if home else set()
    holidays = holidays_in(year, extra_holidays)

    candidates = {
        day: items
        for day, items in candidate_days(assets, away_days=away).items()
        if day not in holidays
    }
    logger.info(
        "%d: %d candidate days after skipping %d on trips and %d holidays",
        year,
        len(candidates),
        len(away),
        len(holidays),
    )

    found: list[DiscoveredDay] = []
    for day, items in sorted(candidates.items(), key=lambda kv: -len(kv[1]))[:ask]:
        thumbnails = _thumbnails_for(items, thumbnail_for)
        verdict = ask_if_special(items, llm_config, thumbnails=thumbnails)
        if not verdict.special or not (verdict.title or verdict.what):
            continue
        found.append(
            DiscoveredDay(
                day=day,
                title=verdict.title,
                subtitle=verdict.subtitle,
                what=verdict.what,
                photos=len(items),
                window=event_window(items),
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


def anniversaries_due(
    catalogue: Iterable[DiscoveredDay],
    on: date,
    *,
    window_days: int = 3,
) -> list[tuple[DiscoveredDay, int]]:
    """Discovered days whose anniversary falls near a date, roundest first.

    Ten years reads louder than nine, which is the whole appeal of arriving
    unannounced.
    """
    due: list[tuple[DiscoveredDay, int]] = []
    for entry in catalogue:
        years = on.year - entry.day.year
        if years < 1:
            continue
        try:
            this_year = entry.day.replace(year=on.year)
        except ValueError:  # 29 February
            this_year = entry.day.replace(year=on.year, day=28)
        if abs((this_year - on).days) <= window_days:
            due.append((entry, years))
    return sorted(
        due, key=lambda pair: (0 if pair[1] % 10 == 0 else 1 if pair[1] % 5 == 0 else 2, -pair[1])
    )
