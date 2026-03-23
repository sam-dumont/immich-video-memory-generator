"""Tests for CLI date resolution + trip selection — flexible filtering."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from immich_memories.cli._date_resolution import resolve_date_range
from immich_memories.cli._trip_display import _closest_trip_to_date, select_trips
from immich_memories.timeperiod import DateRange


class TestMonthNarrowsYearlyTypes:
    """--month should narrow any yearly memory type (year_in_review, person_spotlight, multi_person)."""

    def test_person_spotlight_with_month_returns_single_month(self):
        result = resolve_date_range(
            year=2026,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="person_spotlight",
            month=2,
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 2
        assert result.start.day == 1
        assert result.end.month == 2
        assert result.end.day == 28  # 2026 is not a leap year

    def test_year_in_review_with_month_returns_single_month(self):
        result = resolve_date_range(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="year_in_review",
            month=6,
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 6
        assert result.end.month == 6

    def test_multi_person_with_month_returns_single_month(self):
        result = resolve_date_range(
            year=2026,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="multi_person",
            month=3,
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 3
        assert result.end.month == 3

    def test_person_spotlight_without_month_returns_full_year(self):
        result = resolve_date_range(
            year=2026,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="person_spotlight",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 1
        assert result.start.day == 1
        assert result.end.month == 12
        assert result.end.day == 31

    def test_monthly_highlights_still_requires_month(self):
        """monthly_highlights must still require --month (no regression)."""
        import click

        with pytest.raises(click.UsageError, match="--month is required"):
            resolve_date_range(
                year=2025,
                start=None,
                end=None,
                period=None,
                birthday=None,
                memory_type="monthly_highlights",
            )


class TestStartEndOverridesPreset:
    """--start/--end should override any memory type's default date range."""

    def test_person_spotlight_with_start_end_override(self):
        result = resolve_date_range(
            year=2026,
            start="2026-02-01",
            end="2026-03-31",
            period=None,
            birthday=None,
            memory_type="person_spotlight",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 2
        assert result.start.day == 1
        assert result.end.month == 3
        assert result.end.day == 31

    def test_year_in_review_with_start_end_override(self):
        result = resolve_date_range(
            year=2025,
            start="2025-06-01",
            end="2025-12-31",
            period=None,
            birthday=None,
            memory_type="year_in_review",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 6
        assert result.end.month == 12
        assert result.end.day == 31

    def test_season_with_start_end_override(self):
        result = resolve_date_range(
            year=2025,
            start="2025-07-01",
            end="2025-07-31",
            period=None,
            birthday=None,
            memory_type="season",
            season="summer",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 7
        assert result.end.month == 7
        assert result.end.day == 31

    def test_monthly_highlights_with_start_end_override(self):
        result = resolve_date_range(
            year=2025,
            start="2025-07-10",
            end="2025-07-20",
            period=None,
            birthday=None,
            memory_type="monthly_highlights",
            month=7,
        )
        assert isinstance(result, DateRange)
        assert result.start.day == 10
        assert result.end.day == 20

    def test_start_period_override(self):
        result = resolve_date_range(
            year=2026,
            start="2026-03-01",
            end=None,
            period="2m",
            birthday=None,
            memory_type="person_spotlight",
        )
        assert isinstance(result, DateRange)
        assert result.start == datetime(2026, 3, 1)
        # period 2m from March 1 -> ~May 1

    def test_no_override_returns_preset_default(self):
        """Without --start/--end, the preset's default range is used."""
        result = resolve_date_range(
            year=2026,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="person_spotlight",
        )
        assert isinstance(result, DateRange)
        # Full year
        assert result.start.month == 1
        assert result.end.month == 12


class TestOnThisDayYearsBack:
    """--years-back for on_this_day (default: all years)."""

    def test_years_back_3_returns_3_ranges(self):
        result = resolve_date_range(
            year=None,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="on_this_day",
            years_back=3,
        )
        assert isinstance(result, list)
        assert len(result) == 3

    def test_years_back_none_returns_many_ranges(self):
        """Default (no --years-back) should look back all years (30-year max)."""
        result = resolve_date_range(
            year=None,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="on_this_day",
        )
        assert isinstance(result, list)
        # Should be 30 ranges (forever mode), not 5
        assert len(result) == 30

    def test_years_back_1_returns_1_range(self):
        result = resolve_date_range(
            year=None,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="on_this_day",
            years_back=1,
        )
        assert isinstance(result, list)
        assert len(result) == 1


class TestBackwardCompatibility:
    """Existing behaviors must not change."""

    def test_season_without_override_unchanged(self):
        result = resolve_date_range(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="season",
            season="summer",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 6
        assert result.end.month == 8

    def test_monthly_highlights_unchanged(self):
        result = resolve_date_range(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="monthly_highlights",
            month=7,
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 7
        assert result.end.month == 7

    def test_on_this_day_returns_list(self):
        result = resolve_date_range(
            year=None,
            start=None,
            end=None,
            period=None,
            birthday=None,
            memory_type="on_this_day",
            years_back=5,
        )
        assert isinstance(result, list)
        assert len(result) == 5

    def test_no_memory_type_year_only(self):
        result = resolve_date_range(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday=None,
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 1
        assert result.end.month == 12

    def test_no_memory_type_start_end(self):
        result = resolve_date_range(
            year=None,
            start="2025-06-01",
            end="2025-08-31",
            period=None,
            birthday=None,
        )
        assert isinstance(result, DateRange)
        assert result.start == datetime(2025, 6, 1)
        assert result.end.month == 8
        assert result.end.day == 31

    def test_no_memory_type_birthday_year(self):
        result = resolve_date_range(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday="07/21/2000",
        )
        assert isinstance(result, DateRange)
        assert result.start.month == 7
        assert result.start.day == 21

    def test_no_options_raises_usage_error(self):
        import click

        with pytest.raises(click.UsageError, match="You must specify"):
            resolve_date_range(
                year=None,
                start=None,
                end=None,
                period=None,
                birthday=None,
            )

    def test_on_this_day_with_start_end_override(self):
        """--start/--end overrides on_this_day's multi-range with single range."""
        result = resolve_date_range(
            year=None,
            start="2024-03-23",
            end="2024-03-23",
            period=None,
            birthday=None,
            memory_type="on_this_day",
        )
        # Should be a single DateRange, not a list
        assert isinstance(result, DateRange)
        assert result.start.month == 3
        assert result.start.day == 23


def _make_trip(start: date, end: date, name: str = "Test") -> object:
    """Create a DetectedTrip for testing."""
    from immich_memories.analysis.trip_detection import DetectedTrip

    return DetectedTrip(
        start_date=start,
        end_date=end,
        location_name=name,
        asset_count=10,
        centroid_lat=48.8,
        centroid_lon=2.3,
    )


class TestTripSelection:
    """Tests for --near-date and --month trip selection."""

    def test_closest_trip_to_date_picks_nearest(self):
        trips = [
            _make_trip(date(2024, 3, 10), date(2024, 3, 15), "March"),
            _make_trip(date(2024, 7, 1), date(2024, 7, 10), "July"),
            _make_trip(date(2024, 12, 20), date(2024, 12, 28), "December"),
        ]
        result = _closest_trip_to_date(trips, date(2024, 7, 5))
        assert result.location_name == "July"

    def test_closest_trip_to_date_prefers_earlier_when_equidistant(self):
        trips = [
            _make_trip(date(2024, 3, 1), date(2024, 3, 5), "March"),
            _make_trip(date(2024, 9, 1), date(2024, 9, 5), "September"),
        ]
        # June 1 is equidistant from both midpoints (~3 months each way)
        result = _closest_trip_to_date(trips, date(2024, 6, 3))
        assert result.location_name == "March"

    def test_select_trips_near_date(self):
        trips = [
            _make_trip(date(2024, 3, 10), date(2024, 3, 15), "March"),
            _make_trip(date(2024, 7, 1), date(2024, 7, 10), "July"),
        ]
        result = select_trips(trips, near_date="2024-07-05")
        assert len(result) == 1
        assert result[0].location_name == "July"

    def test_select_trips_month(self):
        trips = [
            _make_trip(date(2024, 3, 10), date(2024, 3, 15), "March"),
            _make_trip(date(2024, 7, 1), date(2024, 7, 10), "July"),
        ]
        result = select_trips(trips, month=7)
        assert len(result) == 1
        assert result[0].location_name == "July"

    def test_select_trips_all(self):
        trips = [
            _make_trip(date(2024, 3, 10), date(2024, 3, 15), "March"),
            _make_trip(date(2024, 7, 1), date(2024, 7, 10), "July"),
        ]
        result = select_trips(trips, all_trips=True)
        assert len(result) == 2

    def test_select_trips_index(self):
        trips = [
            _make_trip(date(2024, 3, 10), date(2024, 3, 15), "March"),
            _make_trip(date(2024, 7, 1), date(2024, 7, 10), "July"),
        ]
        result = select_trips(trips, trip_index=2)
        assert len(result) == 1
        assert result[0].location_name == "July"

    def test_select_trips_index_out_of_range(self):
        trips = [_make_trip(date(2024, 3, 10), date(2024, 3, 15), "March")]
        with pytest.raises(ValueError, match="out of range"):
            select_trips(trips, trip_index=5)

    def test_select_trips_discovery_mode(self):
        """No selection flags → empty list (discovery mode)."""
        trips = [_make_trip(date(2024, 3, 10), date(2024, 3, 15), "March")]
        result = select_trips(trips)
        assert result == []

    def test_select_trips_near_date_empty_list(self):
        """--near-date with no trips returns empty."""
        result = select_trips([], near_date="2024-07-05")
        assert result == []
