"""Which of a memory's date ranges a moment belongs to.

A multi-range memory queries its windows separately and then concatenates the
results, so everything downstream of the fetch sees one flat list. Nothing
records where each asset came from — and nothing needs to, because a clip
carries the date that decides it.

Deriving the era rather than tagging it keeps this out of the objects that flow
through selection, which every memory type shares.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from immich_memories.timeperiod import DateRange


def era_of(when: datetime, ranges: Sequence[DateRange]) -> int | None:
    """The index of the range holding this moment, or None if none do.

    Ranges are inclusive at both ends, and the first match wins: a then-and-now's
    two windows are disjoint, but holiday and on-this-day windows can touch at
    the edges, where a shared day counted twice would inflate whichever era it
    fell in. The memory's own ordering breaks the tie.
    """
    for index, date_range in enumerate(ranges):
        if date_range.start <= when <= date_range.end:
            return index
    return None


def count_by_era(moments: Sequence[datetime], ranges: Sequence[DateRange]) -> list[int]:
    """How much material each era holds, in the memory's own order.

    Always returns one entry per range, so an era that contributed nothing reads
    as a zero rather than disappearing from the report — which is the whole
    point, since a then-and-now with an empty half still renders and would
    otherwise look like a clean run.
    """
    counts = [0] * len(ranges)
    for moment in moments:
        era = era_of(moment, ranges)
        if era is not None:
            counts[era] += 1
    return counts
