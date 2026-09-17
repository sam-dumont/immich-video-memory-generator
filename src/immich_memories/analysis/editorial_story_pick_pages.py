"""Cut one story's pick into requests a reader can answer, and split its grant between them.

A story funded with dozens of slots cannot be picked in one reply: the reader is asked to
count its own answer against a large number, and a 30B miscounts it (a 51-slot story came
back with 53 labels). The shortlist is cut by request size, never by an editorial number, so
a pick that already fits one request is asked exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

from immich_memories.analysis.editorial_moment_inventory import pages
from immich_memories.analysis.editorial_story_reading import PAGE_CHARS


def pick_pages(rendered: Sequence[str], *, overhead: int) -> list[list[int]]:
    """Positions of the rows each request can carry, in the order they were rendered.

    ``overhead`` is the length of the surrounding prompt, so a page plus its preamble stays
    inside the same request budget every other reading stage uses.
    """
    budget = max(PAGE_CHARS - overhead, 1)
    grouped: list[list[int]] = []
    start = 0
    for page in pages(list(rendered), max_items=max(len(rendered), 1), max_chars=budget):
        grouped.append(list(range(start, start + len(page))))
        start += len(page)
    return grouped


def _lend_a_slot(shares: list[int], wanted: Sequence[int]) -> None:
    for index in wanted:
        if shares[index]:
            continue
        donor = max(range(len(shares)), key=shares.__getitem__)
        if shares[donor] <= 1:
            return
        shares[donor] -= 1
        shares[index] = 1


def page_shares(
    offered: Sequence[int], grant: int, *, favourite_pages: Sequence[int] = ()
) -> list[int]:
    """Split the grant between pages by how many choices each one offers.

    Whole slots go by proportion, the remainder to the earliest pages in order. A page that
    holds a favourite keeps one slot whenever a fuller page can spare it.
    """
    total = sum(offered)
    if grant <= 0 or total <= 0:
        return [0] * len(offered)
    grant = min(grant, total)
    shares = [grant * count // total for count in offered]
    for index in range(grant - sum(shares)):
        shares[index] += 1
    _lend_a_slot(shares, favourite_pages)
    return shares
