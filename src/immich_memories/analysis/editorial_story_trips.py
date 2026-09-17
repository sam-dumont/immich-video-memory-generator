"""Trips carry their own weight: a journey away from home is one story of the film.

The app's trip detection runs over the film's own pool during planning, with the configured
home base and thresholds and no network: a trip is named from the place names its pictures
carry. Every day episode that holds one of a trip's pictures (or falls inside the trip and has
no position at all) belongs to that trip, whatever the reader grouped, and the trip enters the
weighing as one row that says how long it was and where it stopped.

How much of the film a trip may take grows with its share of the film's photographed days:
half the film for a trip that is the whole film, the square root of its share below that. The
square root is what keeps a ten-day trip in a five-minute year at about five pictures; a
share taken pro rata would give it two.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from immich_memories.analysis.trip_detection import detect_trips
from immich_memories.api.models import Asset

NO_HOME_BASE = (
    "no home base configured (trips.homebase_latitude and trips.homebase_longitude), "
    "so no day is away from home and no story is a trip"
)
JOURNEY = "the film is a trip; its days and stops are its stories"


@dataclass(frozen=True)
class FilmTrip:
    """One detected journey inside the film: its pictures, days and name."""

    key: str
    place: str
    assets: frozenset[str]
    days: frozenset[str]

    def record(self) -> dict[str, Any]:
        return {"key": self.key, "place": self.place, "pictures": len(self.assets)}


@dataclass
class FilmTrips:
    """The trips of one film, or why it has none."""

    trips: tuple[FilmTrip, ...] = ()
    status: str = "detected"
    positioned: frozenset[str] = field(default_factory=frozenset)
    folded: list[dict[str, Any]] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trips": [trip.record() for trip in self.trips],
            "stories": self.folded,
        }


def detect_film_trips(assets: Iterable[Asset], trips_config, *, journey: bool) -> FilmTrips:
    """Detect the trips inside a film's pool with the configured home base.

    A trip film is already one journey, so its days stay the stories. Without a configured
    home base nothing can be away from home, and the film has no trip stories.
    """
    if journey:
        return FilmTrips(status=JOURNEY)
    home = (trips_config.homebase_latitude, trips_config.homebase_longitude)
    if home == (0.0, 0.0):
        return FilmTrips(status=NO_HOME_BASE)
    pool = sorted(assets, key=lambda asset: asset.file_created_at)
    found = detect_trips(
        pool,
        home[0],
        home[1],
        trips_config.min_distance_km,
        trips_config.min_duration_days,
        trips_config.max_gap_days,
        name_locations=True,
        geocoder=None,
    )
    positioned = frozenset(
        asset.id
        for asset in pool
        if asset.exif_info is not None
        and asset.exif_info.latitude is not None
        and asset.exif_info.longitude is not None
    )
    day_of = {asset.id: asset.file_created_at.isoformat()[:10] for asset in pool}
    return FilmTrips(
        tuple(
            FilmTrip(
                f"T{number}",
                trip.location_name,
                frozenset(trip.asset_ids),
                frozenset(day_of[asset] for asset in trip.asset_ids),
            )
            for number, trip in enumerate(found, 1)
        ),
        positioned=positioned,
    )


def trip_allowance(days: int, film_days: int, slots: int) -> int:
    """How many pictures a trip of `days` photographed days may take of the film's `slots`.

    Both numbers come from the film: its slots and the days it holds pictures of. The halving is
    the dominant weight's own cap (`weight_caps`), which is what a trip filling the whole film
    would be; the square root is the curve between that and nothing which meets the owner's 09-04
    calibration, a ten-day trip in a five-minute year at about five pictures. Nothing here is set
    per film, per product or per duration.
    """
    if days <= 0 or film_days <= 0 or slots <= 0:
        return 0
    share = min(1.0, days / film_days)
    return max(1, round(slots / 2 * math.sqrt(share)))


def reserve_trip_depth(stories: Sequence[dict], *, slots: int, film_days: int) -> None:
    """Each trip story reserves its allowance, taken where a story takes its first picture."""
    for story in stories:
        if story.get("trip"):
            story["reserve"] = trip_allowance(story["seen"]["days"], film_days, slots)


def trip_fold(
    trips: FilmTrips | None, moment_assets: Mapping[str, Sequence[str]], *, rules: bool
) -> tuple[FilmTrips, TripStories | None]:
    """The trips a reading folds, or none: the rules reader keeps consecutive days together."""
    trips = trips or FilmTrips(status="trip detection was not asked for this film")
    if not trips.trips:
        return trips, None
    if rules:
        trips.status = "detected; the rules reader keeps consecutive days together itself"
        return trips, None
    return trips, TripStories(
        trips, assets_of=lambda e: [a for m in e.moments for a in moment_assets.get(m, ())]
    )


class TripStories:
    """Fold the reader's stories so that every detected trip is one story.

    `assets_of(episode)` is the pool pictures of a day episode.
    """

    def __init__(self, trips: FilmTrips, *, assets_of: Callable[[Any], Sequence[str]]) -> None:
        self._trips = trips
        self._assets_of = assets_of

    def _trip_of(self, episode, day: str) -> FilmTrip | None:
        pictures = list(self._assets_of(episode))
        for trip in self._trips.trips:
            if any(asset in trip.assets for asset in pictures):
                return trip
        if pictures and not any(asset in self._trips.positioned for asset in pictures):
            return next((trip for trip in self._trips.trips if day in trip.days), None)
        return None

    def __call__(self, stories: list[dict], episodes: Sequence[Any], hints) -> list[dict]:
        trip_of = {
            e.key: trip
            for e in episodes
            if (trip := self._trip_of(e, str((hints.get(e.key) or {}).get("day") or "")))
            is not None
        }
        folded: list[dict] = []
        placed: dict[str, dict] = {}
        for story in stories:
            outside = [key for key in story["episodes"] if key not in trip_of]
            for key in story["episodes"]:
                if key not in trip_of:
                    continue
                trip = trip_of[key]
                if trip.key not in placed:
                    placed[trip.key] = _trip_story(story, trip)
                    folded.append(placed[trip.key])
                placed[trip.key]["episodes"].append(key)
                placed[trip.key]["members"].append(story["title"])
            if len(outside) == len(story["episodes"]):
                folded.append(story)
            elif outside:
                folded.append(_rest(story, outside))
        for story in placed.values():
            _describe_trip(story, hints)
        self._trips.folded.extend(
            {
                "trip": story["trip"],
                "episodes": story["episodes"],
                "reader_titles": list(dict.fromkeys(story.pop("members"))),
            }
            for story in placed.values()
        )
        return folded


def _trip_story(first: Mapping[str, Any], trip: FilmTrip) -> dict[str, Any]:
    return {
        "key": first["key"],
        "title": f"Trip to {trip.place}" if trip.place else "Trip away from home",
        "episodes": [],
        "weight": "",
        "purpose": first.get("purpose") or "",
        "members": [],
        "trip": {"key": trip.key, "place": trip.place},
    }


def _rest(story: Mapping[str, Any], outside: list[str]) -> dict[str, Any]:
    """What a story straddling a trip keeps outside it."""
    return {**story, "key": f"{story['key']}-home", "episodes": outside}


def _place_name(hint: Mapping[str, Any]) -> str:
    return str(hint.get("place") or "").split(":", 1)[-1].split(",", 1)[0].strip()


def _describe_trip(story: dict[str, Any], hints) -> None:
    """The trip's days and stops, on the row the weighing reads and in the purpose the pick reads."""
    by_day = sorted(
        ((hints.get(key) or {}).get("day") or "", _place_name(hints.get(key) or {}))
        for key in story["episodes"]
    )
    days = sorted({day for day, _place in by_day if day})
    stops: list[list[Any]] = []
    for _day, place in by_day:
        if not place:
            continue
        if stops and stops[-1][0] == place:
            stops[-1][1] += 1
        else:
            stops.append([place, 1])
    span = f"{days[0]} to {days[-1]}" if days else "undated"
    where = "; ".join(f"{place} ({n} day{'s' if n > 1 else ''})" for place, n in stops)
    story["trip"] |= {"days": len(days), "first_day": days[:1], "stops": stops}
    summary = f"{len(days)} days away, {span}" + (f", stops: {where}" if where else "")
    story["trip"]["summary"] = summary
    reader = story["purpose"]
    story["purpose"] = (
        f"A trip of {summary}. Cover its whole span, stop by stop, if the pictures allow."
        + (f" {reader}" if reader else "")
    )[:300]
