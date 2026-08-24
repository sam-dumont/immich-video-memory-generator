"""Group assets into moments by time AND place.

A day is not a timeline. Measured on a real day: a racing circuit at 16:37,
a house 120km away at 16:49, the circuit again at 18:05. Not one person moving
impossibly fast — two devices, two people, one library. Grouping by time alone
interleaves their days into a story neither of them had, and then asks the
model to describe it.

So a moment is bounded by both: close in time, and close in place. Two devices
in the same place at the same time are one moment seen from two vantages, which
is what we want. Two devices in different places at the same time are parallel
threads, which is what the timeline was hiding.
"""

from __future__ import annotations

import math
from typing import Any

# How far apart two photographs can be and still be one moment. Wide enough for
# a venue, a beach or a circuit paddock; narrow enough that two towns are never
# confused for one place.
MOMENT_RADIUS_METRES = 2000.0

# How long a pause before a moment has ended. The same window the photo
# shortlist samples on, so both agree about what a moment is.
MOMENT_WINDOW_MINUTES = 10.0

# An episode is the block a moment sits in — an afternoon at a circuit, a
# party, a hike. Measured on a real day, ten minutes shattered a continuous
# afternoon into eleven pieces, ten of them a single photograph, and a sheet of
# one photograph can tell the model nothing. Ninety minutes held that afternoon
# together as one block of 37 with no singletons anywhere in the day.
EPISODE_WINDOW_MINUTES = 90.0

_EARTH_RADIUS_METRES = 6_371_000.0


def _coordinates(asset: Any) -> tuple[float, float] | None:
    exif = getattr(asset, "exif_info", None)
    latitude = getattr(exif, "latitude", None) if exif else None
    longitude = getattr(exif, "longitude", None) if exif else None
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


def metres_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Great-circle distance, for deciding whether two photographs share a place."""
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    half_lat = math.sin((lat2 - lat1) / 2) ** 2
    half_lon = math.sin((lon2 - lon1) / 2) ** 2
    inner = half_lat + math.cos(lat1) * math.cos(lat2) * half_lon
    return 2 * _EARTH_RADIUS_METRES * math.asin(math.sqrt(inner))


def _belongs_with(asset: Any, moment: list[Any], window_minutes: float, radius: float) -> bool:
    """Whether this asset continues that moment, in time and in place."""
    when = getattr(asset, "file_created_at", None)
    last_when = getattr(moment[-1], "file_created_at", None)
    if when is None or last_when is None:
        return False
    if abs((when - last_when).total_seconds()) > window_minutes * 60:
        return False
    here = _coordinates(asset)
    if here is None:
        # No GPS is not evidence of being elsewhere. Time alone decides, which
        # is what the pipeline did for every asset before places were read.
        return True
    for other in reversed(moment):
        there = _coordinates(other)
        if there is None:
            continue
        return metres_between(here, there) <= radius
    return True


def moments_by_time_and_place(
    assets: list[Any],
    window_minutes: float = MOMENT_WINDOW_MINUTES,
    radius_metres: float = MOMENT_RADIUS_METRES,
) -> list[list[Any]]:
    """Assets grouped into moments, each one close in both time and place.

    Assets are considered in time order, and an asset joins the most recent
    moment it fits. That "most recent" matters: when two threads run in
    parallel, their photographs interleave in time, and a scan that only ever
    looked at the immediately preceding asset would start a new moment on every
    alternation.
    """
    ordered = sorted(
        (a for a in assets if getattr(a, "file_created_at", None) is not None),
        key=lambda a: a.file_created_at,
    )
    moments: list[list[Any]] = []
    for asset in ordered:
        for moment in reversed(moments):
            if _belongs_with(asset, moment, window_minutes, radius_metres):
                moment.append(asset)
                break
        else:
            moments.append([asset])
    return moments
