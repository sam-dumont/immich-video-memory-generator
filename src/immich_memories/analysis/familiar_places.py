"""Recurring GPS neighbourhoods for quiet location captions, without inference."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np

_EARTH_METRES = 6_371_000
FAMILIAR_RADIUS_METRES = 250


@dataclass(frozen=True)
class PlaceObservation:
    """One dated GPS observation; repeated pictures never count as extra visits."""

    latitude: float
    longitude: float
    day: date
    country: str = ""


def _position(latitude: float, longitude: float) -> tuple[float, float, float]:
    lat, lon = math.radians(latitude), math.radians(longitude)
    return math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)


def valid_coordinates(latitude: float | None, longitude: float | None) -> bool:
    """Null Island and non-finite/out-of-range EXIF cannot identify a home."""
    return (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and (latitude != 0 or longitude != 0)
    )


class PlaceHistory:
    """Query a 250 m circle around the actual asset, without city-wide clustering.

    Monthly attendance over two years, or visits spread through five years,
    count as recurring. A fortnight's holiday every summer does not.
    Neighbourhoods never grow by chaining nearby points across a whole town.
    """

    def __init__(self, observations: list[PlaceObservation]) -> None:
        self.observations = [
            row for row in observations if valid_coordinates(row.latitude, row.longitude)
        ]
        self._positions = np.array(
            [_position(row.latitude, row.longitude) for row in self.observations]
        ).reshape((-1, 3))

    def nearby(self, latitude: float, longitude: float) -> list[PlaceObservation]:
        """Return observations inside the circle, including across the date line."""
        if not valid_coordinates(latitude, longitude):
            return []
        delta = self._positions - _position(latitude, longitude)
        chord = 2 * math.sin(FAMILIAR_RADIUS_METRES / (2 * _EARTH_METRES))
        indices = np.flatnonzero(np.einsum("ij,ij->i", delta, delta) <= chord**2)
        return [self.observations[int(index)] for index in indices]

    def is_familiar(self, latitude: float, longitude: float) -> bool:
        days: dict[int, set[date]] = defaultdict(set)
        for row in self.nearby(latitude, longitude):
            days[row.day.year].add(row.day)
        spread = [
            (len({day.toordinal() // 7 for day in year}), len({day.month for day in year}))
            for year in days.values()
        ]
        regular = sum(weeks >= 12 and months >= 6 for weeks, months in spread)
        longstanding = sum(weeks >= 3 and months >= 3 for weeks, months in spread)
        return regular >= 2 or longstanding >= 5
