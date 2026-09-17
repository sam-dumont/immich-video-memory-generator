"""Depth inside the moments a story already shows, for a film that is still short.

The five-minute spacing keeps a burst from taking several slots, and on a dense afternoon it also
keeps every depicted moment after the first of its capture group out of the cut: a 90 s special
day with 27 usable pictures and seven depicted moments shipped four. A single moment can hold
several small interesting ones, so a film still short after every selection pass spends its free
slots here, in the story's funding order: first the depicted moments the inventory found and no
pick took, alternating between capture groups, then further frames of the chosen moments, up to
three frames per moment. Each one is admitted only when the look-alike check confirms it shows
something new; nothing unchecked, and no refused variant, ever fills a slot this way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from itertools import zip_longest
from operator import attrgetter, itemgetter
from typing import Any, Protocol

RUNGS_PER_MOMENT = 3


class _Choice(Protocol):
    key: str
    taken: str

    @property
    def members(self) -> list[str]: ...


def _unchosen(
    ordered: Sequence[_Choice], used: set[str], group_of: Callable[[str], Any]
) -> Iterator[tuple[_Choice, str]]:
    """Depicted moments no pick took, alternating between capture groups."""
    groups: dict[Any, list[_Choice]] = {}
    for choice in ordered:
        if choice.key not in used:
            groups.setdefault(group_of(choice.members[0]), []).append(choice)
    for row in zip_longest(*groups.values()):
        yield from ((choice, choice.members[0]) for choice in row if choice is not None)


def _rungs(
    chosen: Sequence[_Choice], frames_of: Mapping[str, int]
) -> Iterator[tuple[_Choice, str]]:
    """Further members of the chosen moments, one rung at a time, up to three frames a moment."""
    for rung in range(1, RUNGS_PER_MOMENT):
        yield from (
            (choice, choice.members[rung])
            for choice in chosen
            if frames_of.get(choice.key, 0) < RUNGS_PER_MOMENT and rung < len(choice.members)
        )


def depth_ladder(
    choices: Sequence[_Choice],
    *,
    chosen: Sequence[str],
    used: set[str],
    group_of: Callable[[str], Any],
    frames_of: Mapping[str, int],
) -> Iterator[tuple[_Choice, str]]:
    """The (moment, picture) pairs a short film may still spend a slot on, best first."""
    ordered = sorted(choices, key=attrgetter("taken"))
    yield from _unchosen(ordered, used, group_of)
    wanted = set(chosen)
    yield from _rungs([c for c in ordered if c.key in wanted], frames_of)


def neighbours(
    candidate: Mapping[str, Any], kept: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The kept frames a depth candidate is compared with: its own moment's, and the kept frame
    just before and just after it in capture time."""
    same = [k for k in kept if k.get("depicted_moment") == candidate.get("depicted_moment")]
    before = [k for k in kept if k["taken"] <= candidate["taken"]]
    after = [k for k in kept if k["taken"] > candidate["taken"]]
    near = [max(before, key=itemgetter("taken"))] if before else []
    near += [min(after, key=itemgetter("taken"))] if after else []
    return list({k["asset_id"]: k for k in [*same, *near]}.values())
