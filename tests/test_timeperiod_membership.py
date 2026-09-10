"""Date membership uses the same UTC convention as source acquisition."""

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.memory_types.date_builders import build_month
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset


@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        (datetime(2023, 6, 1, tzinfo=UTC), True),
        (datetime(2023, 6, 30, 23, 59, 59, tzinfo=UTC), True),
        (datetime(2023, 5, 31, 23, 59, 59, 999999, tzinfo=UTC), False),
        (datetime(2023, 6, 30, 23, 59, 59, 1, tzinfo=UTC), False),
        (datetime(2023, 6, 1, 2, tzinfo=timezone(timedelta(hours=2))), True),
        (datetime(2023, 6, 1, 1, 59, tzinfo=timezone(timedelta(hours=2))), False),
        (datetime(2023, 5, 31, 20, tzinfo=timezone(timedelta(hours=-4))), True),
        (datetime(2023, 6, 1), True),
        (datetime(2023, 5, 31, 23, 59, 59), False),
        (date(2023, 6, 1), True),
        (date(2023, 7, 1), False),
    ],
)
def test_month_membership_preserves_inclusive_instants(capture, expected):
    window = build_month(6, 2023)
    original = tuple(window)

    assert window.contains(capture) is expected

    assert tuple(window) == original
    assert window.start.tzinfo is window.end.tzinfo is None


def test_aware_bounds_preserve_offsets_and_assume_naive_capture_is_utc():
    offset = timezone(timedelta(hours=2))
    window = DateRange(
        datetime(2023, 6, 1, 2, tzinfo=offset),
        datetime(2023, 6, 1, 3, tzinfo=offset),
    )

    assert window.contains(datetime(2023, 6, 1))
    assert window.contains(date(2023, 6, 1))
    assert window.contains(datetime(2023, 6, 1, 1, tzinfo=UTC))
    assert not window.contains(datetime(2023, 6, 1, 1, 0, 0, 1))
    assert window.start.hour == 2 and window.start.tzinfo is offset


def test_same_zone_repeated_hour_is_compared_as_an_instant():
    zone = ZoneInfo("Europe/Brussels")
    first = datetime(2023, 10, 29, 2, 30, tzinfo=zone, fold=0)
    second = first.replace(fold=1)

    assert DateRange(first, first).contains(first)
    assert not DateRange(first, first).contains(second)


def test_calendar_month_source_accepts_aware_capture_and_rejects_overreturned_asset():
    """Exercise the actual source eligibility caller with build_month's naive bounds."""
    window = build_month(6, 2023)
    inside = make_asset(
        "inside", file_created_at=datetime.fromisoformat("2023-06-01T06:05:41+00:00")
    )
    outside = make_asset("outside", file_created_at=datetime(2023, 7, 1, tzinfo=UTC))
    original_capture = inside.file_created_at
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(date_ranges=(window,))),
        EditorialDependencies(source_fetcher=lambda _scope: (inside, outside)),
    )

    assert prepared.candidate_ids == ("inside",)
    assert prepared.excluded_ids == ("outside",)
    assert prepared.trace.as_dict()["editorial_passes"][0]["rejected"] == [
        {"asset_id": "outside", "reason": "outside date scope"}
    ]
    assert inside.file_created_at is original_capture
