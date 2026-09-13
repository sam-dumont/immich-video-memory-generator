"""One page in the DOM at a time: the pager's arithmetic behind the media pool and the review page."""

from __future__ import annotations

import pytest

from immich_memories.ui.pages.paging import Pager


def test_a_pool_that_fits_on_one_page_has_one_page() -> None:
    pager = Pager(total=6, page_size=20)
    assert pager.pages == 1
    assert pager.bounds(0) == (0, 6)
    assert pager.label(0) == "1–6 of 6"


def test_pages_are_cut_at_the_page_size_and_the_last_one_is_short() -> None:
    pager = Pager(total=42, page_size=20)
    assert pager.pages == 3
    assert [pager.bounds(page) for page in range(3)] == [(0, 20), (20, 40), (40, 42)]
    assert pager.label(1) == "21–40 of 42"
    assert pager.label(2) == "41–42 of 42"


@pytest.mark.parametrize(("page", "clamped"), [(-1, 0), (7, 2)])
def test_a_page_outside_the_range_is_clamped(page: int, clamped: int) -> None:
    pager = Pager(total=42, page_size=20)
    assert pager.clamp(page) == clamped


def test_an_empty_pool_still_has_a_first_page() -> None:
    pager = Pager(total=0, page_size=20)
    assert pager.pages == 1
    assert pager.bounds(0) == (0, 0)
    assert pager.label(0) == "0 of 0"
