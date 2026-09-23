"""A special day is its run of photographs, not the calendar date it began on.

Fixture days are invented. The real catalogue names real people and places, and
none of that belongs in a test file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from immich_memories.automation.catalogue import (
    SCOPE_FROM_DATE,
    SCOPE_FROM_RUN,
    SCOPE_FROM_WINDOW,
    SCOPE_RUN_MEETS_ANOTHER_DAY,
    SCOPE_RUN_TOO_LONG,
    scope_of,
)
from immich_memories.automation.special_day_scan import DiscoveredDay

_OVERNIGHT = (
    datetime(2016, 6, 12, 0, 57, tzinfo=UTC),
    datetime(2016, 6, 13, 19, 29, tzinfo=UTC),
)


def _row(**overrides) -> DiscoveredDay:
    return DiscoveredDay(
        **{
            "day": date(2016, 6, 12),
            "title": "A long night out",
            "subtitle": "",
            "what": "a long evening",
            "photos": 379,
            "window": None,
            "active_hours": 21,
            "run_start": _OVERNIGHT[0],
            "run_end": _OVERNIGHT[1],
            **overrides,
        }
    )


def test_a_run_that_crosses_midnight_is_the_scope() -> None:
    scope = scope_of(_row())

    assert scope.run == _OVERNIGHT
    assert scope.window is None
    assert scope.origin == SCOPE_FROM_RUN


def test_a_row_with_no_run_recorded_falls_back_to_its_date() -> None:
    scope = scope_of(_row(run_start=None, run_end=None))

    assert (scope.run, scope.window) == (None, None)
    assert scope.origin == SCOPE_FROM_DATE


def test_a_window_holding_most_of_its_run_is_still_honoured() -> None:
    window = (datetime(2016, 6, 12, 3, 0, tzinfo=UTC), datetime(2016, 6, 13, 18, 0, tzinfo=UTC))

    scope = scope_of(_row(window=window, window_photos=340))

    assert scope.window == window
    assert scope.run == _OVERNIGHT, "the run is carried even when the window trims inside it"
    assert scope.origin == SCOPE_FROM_WINDOW


def test_a_window_holding_a_sliver_of_a_long_run_loses_to_the_run() -> None:
    # The measured shape: five hours of a 42-hour run, 24 of 379 pictures.
    sliver = (datetime(2016, 6, 12, 1, 53, tzinfo=UTC), datetime(2016, 6, 12, 6, 59, tzinfo=UTC))

    scope = scope_of(_row(window=sliver, window_photos=24))

    assert scope.window is None
    assert scope.run == _OVERNIGHT
    assert scope.origin == SCOPE_FROM_RUN


def test_an_absurdly_long_run_refuses_to_extend() -> None:
    scope = scope_of(_row(run_end=datetime(2016, 6, 17, 19, 29, tzinfo=UTC), active_hours=24))

    assert scope.run is None
    assert scope.origin == SCOPE_RUN_TOO_LONG


def test_a_run_reaching_into_another_catalogued_day_refuses_to_extend() -> None:
    scope = scope_of(_row(), other_days={date(2016, 6, 13)})

    assert scope.run is None
    assert scope.origin == SCOPE_RUN_MEETS_ANOTHER_DAY


def test_the_day_that_owns_the_run_is_not_read_as_another_day() -> None:
    scope = scope_of(_row(), other_days={date(2016, 6, 12)})

    assert scope.run == _OVERNIGHT
