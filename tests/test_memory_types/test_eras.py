"""Which era a moment belongs to, and how much material each era holds.

A multi-window memory queries its windows and then loses them: fetch
concatenates the results and everything after sees one flat list. The era is
recoverable from the clip's own date, so nothing has to be carried through
selection to get it back.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from immich_memories.memory_types.eras import era_of
from immich_memories.timeperiod import DateRange

# Two windows a decade apart, most recent first, as the builders order them.
NOW = DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 12, 31, 23, 59, 59))
THEN = DateRange(start=datetime(2016, 1, 1), end=datetime(2016, 12, 31, 23, 59, 59))
ERAS = [NOW, THEN]


def test_a_moment_resolves_to_the_era_holding_it() -> None:
    assert era_of(datetime(2026, 6, 1), ERAS) == 0
    assert era_of(datetime(2016, 6, 1), ERAS) == 1


def test_a_moment_in_no_era_belongs_to_none() -> None:
    """The years between two windows are not an era, and never a third one."""
    assert era_of(datetime(2021, 6, 1), ERAS) is None


def test_the_edges_of_an_era_are_inside_it() -> None:
    assert era_of(datetime(2016, 1, 1, 0, 0, 0), ERAS) == 1
    assert era_of(datetime(2016, 12, 31, 23, 59, 59), ERAS) == 1


def test_overlapping_eras_resolve_to_the_first_match() -> None:
    """Holiday and on-this-day windows can touch at the edges.

    Without a stated rule a shared day would count twice and inflate whichever
    era it landed in. Order decides, and the order is the memory's own.
    """
    early = DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 6, 30))
    late = DateRange(start=datetime(2026, 6, 1), end=datetime(2026, 12, 31))

    assert era_of(datetime(2026, 6, 15), [early, late]) == 0
    assert era_of(datetime(2026, 6, 15), [late, early]) == 0


def test_each_era_is_counted_in_the_memorys_own_order() -> None:
    """A run that prints one combined total hides an era that returned nothing."""
    from immich_memories.memory_types.eras import count_by_era

    moments = [
        datetime(2026, 3, 1),
        datetime(2026, 4, 1),
        datetime(2026, 5, 1),
        datetime(2016, 7, 1),
    ]

    assert count_by_era(moments, ERAS) == [3, 1]


def test_an_era_with_nothing_in_it_counts_zero_rather_than_vanishing() -> None:
    """Zero is the number the warning exists to notice."""
    from immich_memories.memory_types.eras import count_by_era

    assert count_by_era([datetime(2026, 3, 1)], ERAS) == [1, 0]


def test_moments_outside_every_era_are_not_counted_anywhere() -> None:
    from immich_memories.memory_types.eras import count_by_era

    assert count_by_era([datetime(2021, 3, 1)], ERAS) == [0, 0]


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2025-09-08T01:59:59+02:00", None),
        ("2025-09-08T02:00:00+02:00", 0),
        ("2025-09-10T23:59:59+00:00", 0),
        ("2025-09-11T01:59:59+02:00", 0),
        ("2025-09-11T02:00:00+02:00", None),
        ("2024-09-09T12:00:00", 1),
    ],
)
def test_on_this_day_era_uses_inclusive_instants_across_offsets(timestamp, expected):
    from immich_memories.memory_types.date_builders import build_on_this_day

    windows = build_on_this_day(date(2026, 9, 9), years_back=20)
    original = tuple(windows)

    assert era_of(datetime.fromisoformat(timestamp), windows) == expected
    assert tuple(windows) == original
    assert all(window.start.tzinfo is None for window in windows)


def test_aware_era_boundaries_accept_naive_utc_moments_and_keep_first_match():
    shifted = DateRange(
        datetime.fromisoformat("2025-09-09T00:00:00+02:00"),
        datetime.fromisoformat("2025-09-09T23:59:59+02:00"),
    )
    utc = DateRange(datetime(2025, 9, 8, 22), datetime(2025, 9, 9, 21, 59, 59))

    assert era_of(datetime(2025, 9, 8, 21, 59, 59), [shifted]) is None
    assert era_of(datetime(2025, 9, 8, 22), [shifted, utc]) == 0
    assert era_of(datetime(2025, 9, 9, 21, 59, 59), [shifted]) == 0
    assert era_of(datetime(2025, 9, 9, 22), [shifted]) is None
