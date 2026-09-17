"""Words to slots: how a story's weight, and the partitions it falls in, become picture slots.

The only arithmetic of the story-first selection lives here. The synthesis weighs each story in
words; this module turns those words into a number of pictures, capped by the moments the story
actually holds, and — where a product limits how much any one calendar partition may carry —
reserves that physical capacity in the same order.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, _spaced

DEEPENING_WEIGHTS = ("dominant", "major", "minor")


def weight_caps(slots: int) -> dict[str, int]:
    """Words to slots. Dominant may take half, major a quarter, minor two, a glimpse one."""
    return {
        "dominant": max(1, math.ceil(slots / 2)),
        "major": max(1, math.ceil(slots / 4)),
        "minor": 2 if slots >= 8 else 1,
        "glimpse": 1,
        "none": 0,
    }


class _Grants:
    """The slots handed out so far, and what each story may still take."""

    def __init__(
        self,
        stories: Sequence[Mapping[str, Any]],
        slots: int,
        capacity: Mapping[str, int],
        already: Mapping[str, int],
        reserve_slot: Callable[[Mapping[str, Any]], bool] | None,
    ) -> None:
        self.stories = stories
        self.already = already
        self.capacity = capacity
        self.granted = {s["key"]: 0 for s in stories}
        self.remaining = slots
        self._reserve = reserve_slot

    def of_weight(self, weight: str) -> list[Mapping[str, Any]]:
        return [s for s in self.stories if s["weight"] == weight]

    def room(self, s) -> int:
        return max(
            0,
            self.capacity.get(s["key"], 0) - self.granted[s["key"]] - self.already.get(s["key"], 0),
        )

    def grant(self, s) -> None:
        self.granted[s["key"]] += 1
        self.remaining -= 1

    def take(self, s, cap: int) -> bool:
        """Dominant and major stories need presence before depth. The dominant allowance is
        up to half, not a reservation that can erase another must-show occasion."""
        if (
            self.remaining > 0
            and self.room(s) > 0
            and self.granted[s["key"]] + self.already.get(s["key"], 0) < cap
            and (self._reserve is None or self._reserve(s))
        ):
            self.grant(s)
            return True
        return False

    def reserved(self, s) -> bool:
        return self._reserve is None or self._reserve(s)

    def take_each(self, weight: str, cap: int) -> None:
        """Presence, in funding order. A trip takes the depth it reserved at its own turn."""
        for s in self.stories:
            if s["weight"] != weight:
                continue
            wanted = max(cap, s.get("reserve") or 0)
            while self.take(s, wanted):
                pass

    def take_one_per_day(self, weight: str, cap: int = 1) -> None:
        """Texture is a glance at a day, not a series; a day's second one waits its turn."""
        days: set[str] = set()
        for s in self.stories:
            if s["weight"] == weight and s.get("first_day", "") not in days and self.take(s, cap):
                days.add(s.get("first_day", ""))

    def fill(self, weight: str, cap: int) -> None:
        group = self.of_weight(weight)
        progressed = True
        while self.remaining > 0 and progressed:
            offered = [self.take(s, max(cap, s.get("reserve") or 0)) for s in group]
            progressed = any(offered)


def _minor_breadth(plan: _Grants, cap: int) -> None:
    """Breadth over minors goes by day: a day gets its second minor only after every other minor
    day has one, so six small occasions of one afternoon do not take six of the film's pictures."""
    minors = plan.of_weight("minor")
    progressed = True
    while plan.remaining > 0 and progressed:
        by_day: dict[str, int] = {}
        for s in minors:
            day = s.get("first_day", "")
            by_day[day] = (
                by_day.get(day, 0) + plan.granted[s["key"]] + plan.already.get(s["key"], 0)
            )
        ordered = sorted(
            minors, key=lambda s: (by_day.get(s.get("first_day", ""), 0), minors.index(s))
        )
        offered = [plan.take(s, cap) for s in ordered]
        progressed = any(offered)


def _deepen(plan: _Grants) -> None:
    """Leftover slots deepen the weighed stories one moment at a time while they have moments."""
    deepen = [s for s in plan.stories if s["weight"] in DEEPENING_WEIGHTS]
    progressed = True
    while plan.remaining > 0 and progressed:
        progressed = False
        for s in deepen:
            if plan.remaining > 0 and plan.room(s) > 0 and plan.reserved(s):
                plan.grant(s)
                progressed = True


def allocate_slots(
    stories: Sequence[Mapping[str, Any]],
    slots: int,
    capacity: Mapping[str, int],
    already: Mapping[str, int] | None = None,
    *,
    reserve_slot: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, int]:
    """Slots per story from its weight, capped by the moments it holds. Stories come in weight
    order. A dominant or major story with a `reserve` (a trip) takes that many where the others
    take their first picture. Leftover slots deepen dominant, then major, then minor stories one
    moment at a time while they have moments; a glimpse stays one picture and "none" is never
    funded."""
    counted = dict(already or {})
    plan = _Grants(stories, slots, capacity, counted, reserve_slot)
    caps = weight_caps(slots + sum(counted.values()))
    plan.take_each("dominant", 1)
    plan.take_each("major", 1)
    plan.fill("dominant", caps["dominant"])
    # Minors: one per day first; a day's second minor waits until the majors have their depth, so
    # six small occasions of one afternoon do not each take a picture before the year's occasions.
    plan.take_one_per_day("minor")
    plan.fill("major", caps["major"])
    plan.take_each("minor", 1)
    _minor_breadth(plan, caps["minor"])
    plan.take_one_per_day("glimpse", caps["glimpse"])
    _deepen(plan)
    return plan.granted


def allocate_partition_slots(
    stories: Sequence[Mapping[str, Any]],
    slots: int,
    choices: Mapping[str, Mapping[str | None, Sequence[DepictedChoice]]],
    *,
    limit: int,
    used: Mapping[str | None, int],
    already: Mapping[str, int] | None = None,
) -> tuple[dict[str, int], dict[str, dict[str | None, int]]]:
    """Reserve physical partition capacity in the existing story priority order."""
    reserved: dict[str | None, int] = dict(used)
    grants: dict[str, dict[str | None, int]] = {s["key"]: {} for s in stories}

    def reserve(story):
        key = story["key"]
        for part, available in choices[key].items():
            if part is not None and reserved.get(part, 0) >= limit:
                continue
            if grants[key].get(part, 0) >= len(available):
                continue
            grants[key][part] = grants[key].get(part, 0) + 1
            reserved[part] = reserved.get(part, 0) + 1
            return True
        return False

    capacity = {
        key: sum(map(len, parts.values())) + (already or {}).get(key, 0)
        for key, parts in choices.items()
    }
    counts = allocate_slots(stories, slots, capacity, already=already, reserve_slot=reserve)
    return counts, grants


class PartitionedSlots:
    """Which calendar partition a picture falls in, and how the film's slots divide across them.

    Without a product partition limit this is a pass-through: one nameless partition holding
    every choice, and the plain weight allocation.
    """

    def __init__(
        self,
        unit_by_asset: Mapping[str, Any],
        *,
        partition_of: Callable[[str], str | None] | None = None,
        limit: int | None = None,
    ) -> None:
        self._unit_by_asset = unit_by_asset
        self._partition_of = partition_of
        self.limit = limit

    def of_asset(self, asset: str) -> str | None:
        if self.limit is None or self._partition_of is None:
            return None
        return self._partition_of(self._unit_by_asset[asset][1]["taken"])

    def split(self, choices: Sequence[DepictedChoice]) -> dict[str | None, list[DepictedChoice]]:
        """One choice per partition its members fall in, carried by that partition's own pictures."""
        if self.limit is None:
            return {None: list(choices)}
        parts: dict[str | None, list[DepictedChoice]] = {}
        for choice in choices:
            members: dict[str | None, list[str]] = {}
            for asset in choice.members:
                members.setdefault(self.of_asset(asset), []).append(asset)
            for part, assets in members.items():
                parts.setdefault(part, []).append(
                    DepictedChoice(
                        key=choice.key,
                        episode=choice.episode,
                        taken=self._unit_by_asset[assets[0]][1]["taken"],
                        content=choice.content,
                        primary=assets[0],
                        alternatives=assets[1:],
                    )
                )
        return parts

    def allocate(
        self,
        stories: Sequence[Mapping[str, Any]],
        choices: Mapping[str, Sequence[DepictedChoice]],
        budget: int,
        *,
        carriers: Sequence[Mapping[str, Any]] = (),
        already: Mapping[str, int] | None = None,
    ) -> tuple[dict[str, int], dict[str, dict[str | None, int]]]:
        # Nearby alternatives compete for the same physical slot, but remain in
        # the candidate pool until the pick. Counting them as depth would overfund it.
        capacity_choices = {
            key: _spaced(value, self._unit_by_asset, already=carriers)
            for key, value in choices.items()
        }
        if self.limit is None:
            counts = allocate_slots(
                stories, budget, {k: len(v) for k, v in capacity_choices.items()}, already=already
            )
            return counts, {key: {None: count} for key, count in counts.items()}
        used: dict[str | None, int] = {}
        for carrier in carriers:
            part = self.of_asset(carrier["asset_id"])
            used[part] = used.get(part, 0) + 1
        return allocate_partition_slots(
            stories,
            budget,
            {key: self.split(value) for key, value in capacity_choices.items()},
            limit=self.limit,
            used=used,
            already=already,
        )
