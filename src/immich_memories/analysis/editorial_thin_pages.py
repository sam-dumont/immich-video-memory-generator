"""What a slot is offered, and in which order.

A slot is one seat the gates or the vote left empty, and the page is the story's own pictures the
cut does not already hold. The order is the whole rule: the picker is never told what to prefer,
it is shown the film's preferences as a sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS
from immich_memories.analysis.editorial_thin_catalogue import TIERS, ThinCatalogue, ThinStory


def motion_first(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Motion leads across a story's moments; inside one moment the owner's star wins its frame.

    Two rulings meet on this page. A video product would rather show the moment that moves, so a
    moment holding a clip leads. Inside one moment the favourite wins its own frame, so a still
    the owner starred outranks a clip of that same moment. Deciding that second one means
    comparing the pictures of one moment with each other, so a moment is offered whole; beyond
    that the moments keep the order they arrived in, and no gate or fit check is skipped to seat
    a clip.
    """
    ranked: list[dict[str, Any]] = [dict(row) for row in rows]
    place: dict[str, int] = {}
    moving: set[str] = set()
    for index, row in enumerate(ranked):
        moment = _moment_of(row)
        place.setdefault(moment, index)
        if row.get("kind") in MOTION_KINDS:
            moving.add(moment)
    return sorted(
        ranked,
        key=lambda row: (
            _moment_of(row) not in moving,
            place[_moment_of(row)],
            not row.get("favourite"),
            row.get("kind") not in MOTION_KINDS,
        ),
    )


def records_first(
    rows: Sequence[Mapping[str, Any]], record_of: Callable[[str], str]
) -> list[dict[str, Any]]:
    """The story's own page with whatever the catalogue recorded at the top of it.

    The eligible unit is the story the catalogue records something about, never the picture: a
    page filtered down to records leaves a record-owning story with nothing to offer whenever the
    recorded picture is one the cut already holds.
    """
    return records_lead(motion_first(rows), record_of)


def records_lead(
    rows: Sequence[Mapping[str, Any]], record_of: Callable[[str], str]
) -> list[dict[str, Any]]:
    """The same page with what the catalogue recorded moved to the top, every other order kept.

    A record is the catalogue saying a picture matters. A seat inside a story the draft already
    speaks for is offered it first, exactly as a newcomer seat is.
    """
    return sorted((dict(row) for row in rows), key=lambda row: not record_of(row["asset_id"]))


def gate_refill_page(
    rows: Sequence[Mapping[str, Any]],
    refused_moments: Sequence[str],
    moments_in_cut: set[str],
) -> list[dict[str, Any]]:
    """The page a gate-emptied slot is offered.

    The refused shot's own moment leads, then the story's moments the cut does not already hold,
    then the rest. Several refused shots of one story share one page, their moments leading in
    the order they were refused.
    """

    def rank(row: Mapping[str, Any]) -> tuple[int, int]:
        moment = row.get("moment")
        if moment and moment in refused_moments:
            return (0, list(refused_moments).index(moment))
        return (1 if moment not in moments_in_cut else 2, 0)

    return sorted(motion_first(rows), key=rank)


def newcomer_stories(
    catalogue: ThinCatalogue,
    *,
    held: set[str],
    covered_days: set[str],
    offers: Callable[[str], bool],
) -> list[ThinStory]:
    """Stories of the scope that own a banked record and have no voice in the cut.

    The worthiness tier leads, then a day the film has not reached, then the story holding more
    records, then its own day. A story whose shot a gate took is not offered a newcomer slot as
    well: it already has its seat back, and a second picture would be depth the draft never gave
    it.
    """
    eligible = [
        story
        for story in catalogue.stories
        if story.key not in held and catalogue.records_in(story) and offers(story.key)
    ]
    return sorted(
        eligible,
        key=lambda story: (
            TIERS.index(story.tier),
            story.first_day in covered_days,
            -catalogue.records_in(story),
            story.first_day,
        ),
    )


def _moment_of(row: Mapping[str, Any]) -> str:
    return str(row.get("moment") or row["asset_id"])
