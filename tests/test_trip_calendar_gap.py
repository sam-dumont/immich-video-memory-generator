"""A quiet calendar day should not split a trip according to the next shot's hour."""

from datetime import date

from immich_memories.analysis.trip_detection import detect_trips
from tests.test_trip_detection import _make_asset


def test_a_two_calendar_day_gap_preserves_the_whole_trip():
    assets = [
        _make_asset(41.39, 2.17, stamp)
        for stamp in (
            "2024-06-07T09:00:00",
            "2024-06-08T09:00:00",
            "2024-06-10T15:00:00",
            "2024-06-11T16:00:00",
        )
    ]

    trips = detect_trips(assets, 50.85, 4.35, name_locations=False)

    assert [(t.start_date, t.end_date, t.asset_count) for t in trips] == [
        (date(2024, 6, 7), date(2024, 6, 11), 4)
    ]
