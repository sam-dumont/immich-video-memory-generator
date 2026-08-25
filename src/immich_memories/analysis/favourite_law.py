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


def let_the_favourite_win(
    selected: list[Any],
    pool: list[Any],
    *,
    window_minutes: float = MOMENT_WINDOW_MINUTES,
    radius_metres: float = MOMENT_RADIUS_METRES,
) -> list[Any]:
    """Give a moment's seat to the photograph the owner starred.

    Substitute, never add. The cut has already been fitted to a runtime, and
    adding the favourite on top would grow it past that; the neighbour standing
    in for the moment gives up nothing but its place. Which favourite takes it
    is asset_merit.ranking_key, the same order everything else uses, so a
    moment holding several stars shows its best.

    The stage every other stage answers to. Selection narrows a pool through
    caps, distribution, fitting and dedup, each defensible on its own, and a
    favourite can be dropped by any of them while a neighbour survives the
    lot. Rather than teach six stages the rule, the rule is applied once to
    what they produced.
    """
    from immich_memories.analysis.asset_merit import ranking_key
    from immich_memories.analysis.moment_grouping import _group_by_time_and_place

    shipped_ids = {_id_of(item) for item in selected}
    by_id = {_id_of(item): item for item in pool}
    swaps: dict[str, str] = {}

    for moment in _group_by_time_and_place(
        [_asset_of(item) for item in pool], window_minutes, radius_metres
    ):
        favourites = [a for a in moment if getattr(a, "is_favorite", False)]
        if not favourites:
            continue
        here = [a.id for a in moment if a.id in shipped_ids and a.id not in swaps]
        if not here or any(a.id in shipped_ids for a in favourites):
            continue

        winner = max(favourites, key=lambda a: ranking_key(a, _score_of(by_id.get(a.id))))
        loser = min(here, key=lambda asset_id: _score_of(by_id.get(asset_id)))
        if winner.id in by_id:
            swaps[loser] = winner.id

    if not swaps:
        return selected
    return [by_id[swaps[_id_of(item)]] if _id_of(item) in swaps else item for item in selected]


def _asset_of(item: Any) -> Any:
    clip = getattr(item, "clip", item)
    return getattr(clip, "asset", clip)


def _id_of(item: Any) -> str:
    return str(getattr(_asset_of(item), "id", ""))


def _score_of(item: Any) -> float:
    return float(getattr(item, "score", 0.0) or 0.0)
