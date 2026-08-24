"""Match the same place across two eras, away from home.

Measured on a real library: roughly a third of the older era has a partner in
the recent one within 500 m, and about seven in ten of those pairs sit in one
cluster — home. Home is the strongest then-and-now a personal library holds,
but every asset at one address shares a coordinate, so distance cannot tell the
kitchen from the garden there. Pairing on proximity inside that cluster would
match one to the other and present it as a find, so home is excluded outright
and left to the scene-matching half.

What remains is the away half: places visited in both eras, where distance is
the whole signal and means what it says.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from immich_memories.analysis.trip_detection import haversine_km
from immich_memories.memory_types.eras import era_of
from immich_memories.timeperiod import DateRange

# A pair has to be the same place, not the same neighbourhood. Measured: going
# from 500 m to 10 km buys six percentage points of coverage, so a wider radius
# trades precision for almost nothing.
DEFAULT_MAX_KM = 0.5

# Everything within this of the busiest cluster is treated as one address.
DEFAULT_HOME_RADIUS_KM = 1.0

# Fewer than this and the mode declines rather than shipping a token gesture.
DEFAULT_MIN_PAIRS = 2


@dataclass(frozen=True, slots=True)
class PlacedMoment:
    """A clip reduced to what pairing needs: when it happened and where."""

    asset_id: str
    when: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class EraPair:
    """One place, seen in both eras."""

    earlier_id: str
    later_id: str
    distance_km: float


def _dominant_cluster(
    moments: Sequence[PlacedMoment], radius_km: float
) -> list[PlacedMoment]:
    """The busiest single location in the material, which is where home is.

    Greedy rather than exhaustive: each moment seeds a cluster of everything
    within the radius, and the largest wins. Ties break on asset id so the same
    input always yields the same cluster.
    """
    best: list[PlacedMoment] = []
    for seed in sorted(moments, key=lambda m: m.asset_id):
        near = [
            m
            for m in moments
            if haversine_km(seed.latitude, seed.longitude, m.latitude, m.longitude) <= radius_km
        ]
        if len(near) > len(best):
            best = near
    return best


def pair_across_eras(
    moments: Sequence[PlacedMoment],
    ranges: Sequence[DateRange],
    *,
    max_km: float = DEFAULT_MAX_KM,
    home_radius_km: float = DEFAULT_HOME_RADIUS_KM,
    min_pairs: int = DEFAULT_MIN_PAIRS,
) -> list[EraPair]:
    """Places present in both of the memory's eras, home excluded.

    Returns an empty list rather than a short one when fewer than ``min_pairs``
    survive: a mode that found a single match should decline, not present it as
    though the memory were built on pairing.
    """
    if len(ranges) < 2:
        return []

    home = {m.asset_id for m in _dominant_cluster(moments, home_radius_km)}
    away = [m for m in moments if m.asset_id not in home]

    by_era: dict[int, list[PlacedMoment]] = {}
    for moment in away:
        era = era_of(moment.when, ranges)
        if era is not None:
            by_era.setdefault(era, []).append(moment)

    if len(by_era) < 2:
        return []

    # The memory's ranges are most-recent-first, so the highest index is oldest.
    earlier = by_era[max(by_era)]
    later = by_era[min(by_era)]

    candidates = sorted(
        (
            (haversine_km(a.latitude, a.longitude, b.latitude, b.longitude), a.asset_id, b.asset_id)
            for a in earlier
            for b in later
        ),
    )

    used: set[str] = set()
    pairs: list[EraPair] = []
    for distance, earlier_id, later_id in candidates:
        if distance > max_km:
            break
        if earlier_id in used or later_id in used:
            continue
        used.update((earlier_id, later_id))
        pairs.append(EraPair(earlier_id=earlier_id, later_id=later_id, distance_km=distance))

    return pairs if len(pairs) >= min_pairs else []
