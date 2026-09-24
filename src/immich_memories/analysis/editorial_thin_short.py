"""A short model-tier film reads a few more episodes before it gives up the seconds.

A newcomer seat needs a notable record, a record comes only from an episode reading, and a cold
run reads only the episodes the draft's shots sit in. So a story the draft never reached can
never earn a seat, and a film the gates and the vote left short stays short even when the
library holds a moment worth a place. Measured on a cold month: 27 s of a 60 s film, and two
whole weeks no shot came from.

When the polished cut is short by S seconds, this reads at most E = 2 * ceil(S / 3.5) unread
episodes of the stories the cut holds no shot of, cheapest signals first, and banks them. A story
whose reading records something may then take a newcomer seat, at most ceil(S / 3.5) of them. A
story whose reading records nothing gets no seat: short beats a guess. The no-model tier never
comes here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS
from immich_memories.analysis.editorial_story_replies import close_family_on
from immich_memories.analysis.editorial_structure_budget import MIN_CARRIER_SECONDS
from immich_memories.analysis.editorial_thin_catalogue import (
    TIERS,
    BankedCatalogue,
    ThinCatalogue,
)

# One reading call pages about three small episodes; the budget pays for that many calls.
EPISODES_PER_READING = 3


@dataclass(frozen=True)
class ShortReads:
    """What a short film may read: the library's unread episodes, and their records.

    `unread(asset_ids)` maps each canonical episode these pictures sit in that this run has not
    read to its members among them. `records(asset_ids)` reads and banks those pictures'
    episodes and returns what the readings recorded, by picture. `standing(asset_id)` is the
    no-model reader's standing, 0 to 2.
    """

    unread: Callable[[Sequence[str]], Mapping[str, Sequence[str]]]
    records: Callable[[Sequence[str]], Mapping[str, str]]
    standing: Callable[[str], int]


def seats_for(short_seconds: float) -> int:
    """How many newcomers the missing seconds can hold, at production's shortest carrier."""
    if short_seconds < MIN_CARRIER_SECONDS:
        return 0
    return math.ceil(short_seconds / MIN_CARRIER_SECONDS)


def short_budget(episodes_read: int, seats: int) -> int:
    """The calls a short film's extra reading may add: its reading calls and four per seat."""
    return math.ceil(episodes_read / EPISODES_PER_READING) + 4 * seats


def episodes_to_read(
    cut: Sequence[Mapping[str, Any]],
    *,
    catalogue: ThinCatalogue,
    offers: Callable[[str], Sequence[Mapping[str, Any]]],
    reads: ShortReads,
    line_of: Callable[[str], str],
    limit: int,
    close_family: Callable[[str], Mapping[str, str]] = close_family_on,
) -> list[Sequence[str]]:
    """The unread episodes of the stories the cut has no shot of, best first, at most `limit`.

    The order is the film's cheap evidence: a week the cut does not reach, then a day it does
    not reach, then the story's worthiness tier, then close family on the pictures, then motion,
    then how many pictures the no-model reader stands at two. `close_family` says who counts as
    close family: in a film about people, the subject's own family too.
    """
    held = {str(row.get("story_episode") or "") for row in cut}
    units: dict[str, tuple[Mapping[str, Any], str]] = {}
    for story in catalogue.stories:
        if story.key in held:
            continue
        for unit in offers(story.key):
            units.setdefault(unit["asset_id"], (unit, story.tier))
    if not units or limit <= 0:
        return []
    days = {_day(row["taken"]) for row in cut}
    weeks = {day.isocalendar()[:2] for day in days}

    def rank(members: Sequence[str]) -> tuple:
        rows = [units[asset] for asset in members if asset in units]
        taken = {_day(unit["taken"]) for unit, _tier in rows}
        return (
            all(day.isocalendar()[:2] in weeks for day in taken),
            all(day in days for day in taken),
            min((TIERS.index(tier) for _unit, tier in rows), default=len(TIERS)),
            not any(close_family(line_of(unit["asset_id"])) for unit, _tier in rows),
            not any(unit.get("kind") in MOTION_KINDS for unit, _tier in rows),
            -sum(reads.standing(unit["asset_id"]) >= 2 for unit, _tier in rows),
            min(taken, default=date.max),
        )

    episodes = reads.unread(list(units))
    return sorted(episodes.values(), key=rank)[:limit]


def with_records(catalogue: ThinCatalogue, records: Mapping[str, str]) -> BankedCatalogue:
    """The same catalogue, holding what the extra readings recorded as well."""
    held = {
        asset: record
        for story in catalogue.stories
        for asset in story.asset_ids
        if (record := catalogue.notable_record_of(asset))
    }
    return BankedCatalogue(
        thesis=catalogue.thesis,
        stories=tuple(catalogue.stories),
        hints=catalogue.hints,
        records=held | dict(records),
    )


def _day(taken: Any) -> date:
    return date.fromisoformat(str(taken)[:10])
