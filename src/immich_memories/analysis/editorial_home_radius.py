"""Where home is, and whether some captures were photographed near it."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

HOME_RADIUS_KM = 10.0


def home_of(trips) -> tuple[float, float] | None:
    """The configured home base, or None when this install never set one.

    (0, 0) is the field default, not Null Island. Reading it as a coordinate puts
    every geotagged happening ten thousand kilometres away, which turns "outside the
    home radius" into a constant on any install that never named a home.
    """
    latitude, longitude = trips.homebase_latitude, trips.homebase_longitude
    if latitude is None or longitude is None or latitude == longitude == 0.0:
        return None
    return latitude, longitude


def near_home_of(
    home: tuple[float, float] | None, points: Iterable[Sequence[float] | None]
) -> bool | None:
    """Whether the mean of these captures sits inside the home radius; None when unknown."""
    known = [p for p in points if p is not None and p[0] is not None and p[1] is not None]
    if not known or home is None:
        return None
    latitude = sum(p[0] for p in known) / len(known)
    longitude = sum(p[1] for p in known) / len(known)
    la1, lo1, la2, lo2 = map(math.radians, (home[0], home[1], latitude, longitude))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h)) <= HOME_RADIUS_KM
