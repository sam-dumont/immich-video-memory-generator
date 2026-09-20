"""Cut one story's pick into requests a reader can answer, and split its grant between them.

A story funded with dozens of slots cannot be picked in one reply: the reader is asked to
count its own answer against a large number, and a 30B miscounts it (a 51-slot story came
back with 53 labels, and a 65-slot one with 101). So a page is cut twice: by request size,
and by how many labels its own answer would have to hold. A pick whose answer already fits
one reply is asked exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

from immich_memories.analysis.editorial_prompt_pages import pages
from immich_memories.analysis.editorial_story_reading import PAGE_CHARS

# How many labels one reply may have to name. The counting, not the reading, is what fails:
# a reader asked for at most 51 and at most 65 returned 53 and 101. The inventory already
# asks for at most 16 rows a request; two dozen keeps a pick's answer in that range while a
# large story still costs only a handful of extra requests.
MAX_LABELS_PER_ASK = 24


def pick_pages(rendered: Sequence[str], *, overhead: int, grant: int) -> list[list[int]]:
    """Positions of the rows each request can carry, in the order they were rendered.

    ``overhead`` is the length of the surrounding prompt, so a page plus its preamble stays
    inside the same request budget every other reading stage uses. ``grant`` is the story's
    whole allowance: a page's share of it follows its share of the rows, so bounding the rows
    a page holds is what keeps any one answer inside :data:`MAX_LABELS_PER_ASK`.
    """
    budget = max(PAGE_CHARS - overhead, 1)
    grouped: list[list[int]] = []
    start = 0
    for page in pages(
        list(rendered), max_items=_rows_per_page(len(rendered), grant), max_chars=budget
    ):
        grouped.append(list(range(start, start + len(page))))
        start += len(page)
    return grouped


def _rows_per_page(offered: int, grant: int) -> int:
    """The most rows whose proportional share of ``grant`` still fits one answer."""
    if grant <= MAX_LABELS_PER_ASK:
        return max(offered, 1)
    return max(MAX_LABELS_PER_ASK * offered // grant, 1)


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
