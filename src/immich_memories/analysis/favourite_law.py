"""Within a moment, the favourite wins. Always.

A rule that lives in a prompt is a rule the next prompt edit deletes. This one
is stated as code and can be measured against any finished selection: name the
moments that shipped something else while the photograph the owner starred was
dropped.

The rule is NOT that every favourite ships. A memory has a runtime, and whole
moments go unshown. It is that no favourite is passed over in favour of
something standing beside it — same time, same place, same moment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.moment_grouping import (
    MOMENT_RADIUS_METRES,
    MOMENT_WINDOW_MINUTES,
)


@dataclass(frozen=True)
class LostFavourite:
    """A moment that shipped something else while its favourite was dropped."""

    favourites: tuple[str, ...]
    shipped: tuple[str, ...]


def moments_that_lost_their_favourite(
    pool: list[Any],
    shipped_ids: set[str],
    *,
    window_minutes: float = MOMENT_WINDOW_MINUTES,
    radius_metres: float = MOMENT_RADIUS_METRES,
) -> list[LostFavourite]:
    """Every moment that shipped a non-favourite while its favourite was dropped.

    An empty list is the law holding. Anything else is a moment where the owner
    said which photograph mattered and the memory shipped its neighbour.

    Grouping is imported private on purpose. moments_to_read drops foreign
    media before tiling, which is right for reading and wrong here: this
    measures the pool selection actually chose from, whatever is in it, and a
    filter applied here would hide violations rather than prevent them.
    """
    from immich_memories.analysis.moment_grouping import _group_by_time_and_place

    lost: list[LostFavourite] = []
    for moment in _group_by_time_and_place(pool, window_minutes, radius_metres):
        favourites = [a for a in moment if getattr(a, "is_favorite", False)]
        if not favourites:
            continue
        shipped = [a for a in moment if a.id in shipped_ids]
        # A moment nobody shipped is a moment the runtime could not hold, not a
        # favourite passed over.
        if not shipped:
            continue
        if any(a.id in shipped_ids for a in favourites):
            continue
        lost.append(
            LostFavourite(
                favourites=tuple(a.id for a in favourites),
                shipped=tuple(a.id for a in shipped),
            )
        )
    return lost


def _asset_of(item: Any) -> Any:
    clip = getattr(item, "clip", item)
    return getattr(clip, "asset", clip)
