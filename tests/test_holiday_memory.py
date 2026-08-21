"""Holiday memories: the same date, across the years.

A holiday is the one date people photograph every single year, which makes it
the strongest across-years memory in a library — and the only Phase 2 type that
needs no new selection machinery, just the right date ranges.

Moving holidays are computed rather than tabulated, because a table would be
wrong the year after it was written.
"""

from __future__ import annotations

from datetime import date

import pytest

from immich_memories.memory_types.date_builders import build_holiday, resolve_holiday


def test_a_fixed_holiday_resolves_to_its_date() -> None:
    assert resolve_holiday("christmas", 2025) == date(2025, 12, 25)


def test_an_explicit_month_day_is_accepted() -> None:
    """Not every household's holiday is on the list."""
    assert resolve_holiday("07-04", 2025) == date(2025, 7, 4)


def test_easter_is_computed_not_tabulated() -> None:
    """Known-good dates; a hardcoded table would rot after one year."""
    assert resolve_holiday("easter", 2024) == date(2024, 3, 31)
    assert resolve_holiday("easter", 2025) == date(2025, 4, 20)
    assert resolve_holiday("easter", 2026) == date(2026, 4, 5)


def test_thanksgiving_is_the_fourth_thursday_of_november() -> None:
    assert resolve_holiday("thanksgiving", 2025) == date(2025, 11, 27)
    assert resolve_holiday("thanksgiving", 2026) == date(2026, 11, 26)


def test_an_unknown_holiday_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="christmasss"):
        resolve_holiday("christmasss", 2025)


def test_the_window_covers_the_days_around_the_holiday() -> None:
    """Christmas Eve and Boxing Day belong to Christmas."""
    ranges = build_holiday("christmas", 2025, years_back=1, window_days=2)

    assert len(ranges) == 1
    assert ranges[0].start.date() == date(2025, 12, 23)
    assert ranges[0].end.date() == date(2025, 12, 27)


def test_each_year_gets_its_own_range() -> None:
    ranges = build_holiday("christmas", 2025, years_back=3, window_days=1)

    assert [r.start.year for r in ranges] == [2025, 2024, 2023]


def test_a_moving_holiday_moves_between_years() -> None:
    """The whole point of computing it: Easter is not on a fixed date."""
    ranges = build_holiday("easter", 2025, years_back=2, window_days=0)

    assert [r.start.date() for r in ranges] == [date(2025, 4, 20), date(2024, 3, 31)]


def test_a_holiday_that_has_not_happened_yet_is_skipped() -> None:
    """Found by rendering: in August, `--years-back 4` included Christmas of the
    current year — a window that cannot contain a photo.

    An explicit --year is honoured as given; it is only the default that has to
    know what today is.
    """
    ranges = build_holiday("christmas", 2026, years_back=3, window_days=1, today=date(2026, 8, 21))

    assert [r.start.year for r in ranges] == [2025, 2024, 2023]


def test_a_holiday_already_past_this_year_is_included() -> None:
    ranges = build_holiday("easter", 2026, years_back=2, window_days=0, today=date(2026, 8, 21))

    assert [r.start.year for r in ranges] == [2026, 2025]


def test_an_explicit_year_is_still_honoured() -> None:
    """Asking for Christmas 2026 in August is a choice, not a mistake."""
    ranges = build_holiday("christmas", 2026, years_back=1, window_days=1)

    assert [r.start.year for r in ranges] == [2026]
