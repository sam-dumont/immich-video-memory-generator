"""Which days the no-model tier keeps: the ones loud on one recorded fact, strongest first.

Without a reader, a day is kept when one fact the library records stands out: it was spent away
from home, the owner starred several of its pictures, it is mostly video, or it ran long with the
owner's close family in it. Kept like that, a family-heavy year kept two hundred days, which is
no suggestion at all, so the days are ranked and each year keeps its strongest few (#1210):
away from home first, the furthest first; then the most favourites; then the largest video share;
then the longest day weighted by how much of it holds close family. Picture count breaks ties.
Family ranks a long day, it does not gate one; a long day with no close family in it at all is
the busy ordinary day at home, and never counts.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from immich_memories.analysis.special_day import MIN_ACTIVE_HOURS, MIN_PHOTOS, active_hours
from immich_memories.analysis.special_day_sequence import close_family_on
from immich_memories.analysis.trip_detection import haversine_km

_FAVOURITES_OF_AN_OCCASION = 3
_VIDEOS_OF_AN_OCCASION = 3
_VIDEO_SHARE_OF_AN_OCCASION = 0.5


@dataclass(frozen=True)
class LoudFact:
    """What one fact says a run was, and how strongly, for ranking against a year's others."""

    what: str
    order: int
    strength: float
    pictures: int

    @property
    def rank(self) -> tuple[int, float, int]:
        return (self.order, -self.strength, -self.pictures)


def loud_fact(
    items: list,
    *,
    home: tuple[float, float] | None,
    away_km: float,
    family: Mapping[str, str],
) -> LoudFact | None:
    """The first loud fact of a run in ranking order, or None when none is loud."""
    if home and (distance := _distance_away(items, home, away_km)) is not None:
        return LoudFact("a day away from home", 0, distance, len(items))
    stars = sum(1 for a in items if getattr(a, "is_favorite", False))
    if stars >= _FAVOURITES_OF_AN_OCCASION:
        return LoudFact(f"{stars} favourites", 1, stars, len(items))
    videos = sum(1 for a in items if getattr(a, "is_video", False))
    if videos >= _VIDEOS_OF_AN_OCCASION and videos >= _VIDEO_SHARE_OF_AN_OCCASION * len(items):
        return LoudFact("a day mostly on video", 2, videos / len(items), len(items))
    hours = active_hours(items)
    _roles, with_family = close_family_on(items, family)
    if len(items) >= MIN_PHOTOS and hours >= MIN_ACTIVE_HOURS and with_family:
        what = f"a long day with close family, {hours} active hours"
        return LoudFact(what, 3, hours * with_family / len(items), len(items))
    return None


def ranked_occasions(
    runs: Mapping[date, list],
    *,
    home: tuple[float, float] | None,
    away_km: float,
    family: Mapping[str, str],
) -> dict[date, str]:
    """Every run loud on one fact, strongest first, with what that fact says it was."""
    loud = {
        day: fact
        for day, items in runs.items()
        if (fact := loud_fact(items, home=home, away_km=away_km, family=family))
    }
    return {day: loud[day].what for day in sorted(loud, key=lambda day: loud[day].rank)}


def _distance_away(items: list, home: tuple[float, float], away_km: float) -> float | None:
    """How far from home the run was, when most of its located pictures were away from it."""
    distances = [
        haversine_km(lat, lon, *home)
        for a in items
        if (exif := getattr(a, "exif_info", None)) is not None
        and (lat := getattr(exif, "latitude", None)) is not None
        and (lon := getattr(exif, "longitude", None)) is not None
    ]
    away = [km for km in distances if km >= away_km]
    return statistics.median(away) if len(away) > len(distances) - len(away) else None
