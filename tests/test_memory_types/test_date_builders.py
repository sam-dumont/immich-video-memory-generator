"""Tests for date_builders module."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from immich_memories.memory_types.date_builders import (
    build_month,
    build_on_this_day,
    build_season,
)
from immich_memories.timeperiod import DateRange


class TestBuildSeasonSummerNorth:
    """Test build_season for summer in northern hemisphere."""

    def test_summer_north_start(self) -> None:
        result = build_season("summer", 2023)
        assert result.start == datetime(2023, 6, 1, 0, 0, 0)

    def test_summer_north_end(self) -> None:
        result = build_season("summer", 2023)
        assert result.end == datetime(2023, 8, 31, 23, 59, 59)

    def test_returns_date_range(self) -> None:
        result = build_season("summer", 2023)
        assert isinstance(result, DateRange)

    def test_spring_north(self) -> None:
        result = build_season("spring", 2023)
        assert result.start == datetime(2023, 3, 1, 0, 0, 0)
        assert result.end == datetime(2023, 5, 31, 23, 59, 59)

    def test_fall_north(self) -> None:
        result = build_season("fall", 2023)
        assert result.start == datetime(2023, 9, 1, 0, 0, 0)
        assert result.end == datetime(2023, 11, 30, 23, 59, 59)

    def test_autumn_alias(self) -> None:
        result = build_season("autumn", 2023)
        assert result == build_season("fall", 2023)


class TestBuildSeasonWinter:
    """Test build_season for winter spanning two years."""

    def test_winter_north_start(self) -> None:
        result = build_season("winter", 2023)
        assert result.start == datetime(2023, 12, 1, 0, 0, 0)

    def test_winter_north_end(self) -> None:
        result = build_season("winter", 2023)
        assert result.end == datetime(2024, 2, 29, 23, 59, 59)

    def test_winter_non_leap_year(self) -> None:
        result = build_season("winter", 2022)
        assert result.end == datetime(2023, 2, 28, 23, 59, 59)


class TestBuildSeasonSouthernHemisphere:
    """Test build_season with southern hemisphere reversal."""

    def test_summer_south_is_dec_feb(self) -> None:
        result = build_season("summer", 2023, hemisphere="south")
        assert result.start == datetime(2023, 12, 1, 0, 0, 0)
        assert result.end == datetime(2024, 2, 29, 23, 59, 59)

    def test_winter_south_is_jun_aug(self) -> None:
        result = build_season("winter", 2023, hemisphere="south")
        assert result.start == datetime(2023, 6, 1, 0, 0, 0)
        assert result.end == datetime(2023, 8, 31, 23, 59, 59)

    def test_spring_south_is_sep_nov(self) -> None:
        result = build_season("spring", 2023, hemisphere="south")
        assert result.start == datetime(2023, 9, 1, 0, 0, 0)
        assert result.end == datetime(2023, 11, 30, 23, 59, 59)

    def test_fall_south_is_mar_may(self) -> None:
        result = build_season("fall", 2023, hemisphere="south")
        assert result.start == datetime(2023, 3, 1, 0, 0, 0)
        assert result.end == datetime(2023, 5, 31, 23, 59, 59)


class TestBuildSeasonInvalid:
    """Test build_season with invalid inputs."""

    def test_invalid_season_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid season"):
            build_season("monsoon", 2023)

    def test_invalid_hemisphere_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid hemisphere"):
            build_season("summer", 2023, hemisphere="east")


class TestBuildMonthBasic:
    """Test build_month for basic cases."""

    def test_january(self) -> None:
        result = build_month(1, 2023)
        assert result.start == datetime(2023, 1, 1, 0, 0, 0)
        assert result.end == datetime(2023, 1, 31, 23, 59, 59)

    def test_april_has_30_days(self) -> None:
        result = build_month(4, 2023)
        assert result.end == datetime(2023, 4, 30, 23, 59, 59)

    def test_returns_date_range(self) -> None:
        result = build_month(6, 2023)
        assert isinstance(result, DateRange)


class TestBuildMonthFebruary:
    """Test build_month for February with leap year handling."""

    def test_february_leap_year(self) -> None:
        result = build_month(2, 2024)
        assert result.end == datetime(2024, 2, 29, 23, 59, 59)

    def test_february_non_leap_year(self) -> None:
        result = build_month(2, 2023)
        assert result.end == datetime(2023, 2, 28, 23, 59, 59)


class TestBuildMonthInvalid:
    """Test build_month with invalid inputs."""

    def test_month_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid month"):
            build_month(0, 2023)

    def test_month_13_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid month"):
            build_month(13, 2023)

    def test_negative_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid month"):
            build_month(-1, 2023)


class TestBuildOnThisDay:
    """Test build_on_this_day for basic cases."""

    def test_returns_correct_number_of_ranges(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=3)
        assert len(result) == 3

    def test_first_range_is_most_recent_year(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=3)
        assert result[0].start == datetime(2023, 3, 11, 0, 0, 0)
        assert result[0].end == datetime(2023, 3, 13, 23, 59, 59)

    def test_ranges_go_back_in_order(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=3)
        assert result[1].start == datetime(2022, 3, 11, 0, 0, 0)
        assert result[1].end == datetime(2022, 3, 13, 23, 59, 59)
        assert result[2].start == datetime(2021, 3, 11, 0, 0, 0)
        assert result[2].end == datetime(2021, 3, 13, 23, 59, 59)

    def test_returns_date_range_instances(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=1)
        assert all(isinstance(r, DateRange) for r in result)

    def test_years_back_zero_returns_empty(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=0)
        assert result == []

    def test_negative_years_back_returns_empty(self) -> None:
        result = build_on_this_day(date(2024, 3, 12), years_back=-1)
        assert result == []

    def test_default_years_back_is_five(self) -> None:
        result = build_on_this_day(date(2024, 3, 12))
        assert len(result) == 5


class TestBuildOnThisDayFeb29:
    """Test build_on_this_day with Feb 29 edge case."""

    def test_feb29_skips_non_leap_years(self) -> None:
        result = build_on_this_day(date(2024, 2, 29), years_back=5)
        # 2023, 2022, 2021, 2019 are not leap years -> use Feb 28 - Mar 1
        # 2020 is a leap year -> use Feb 28 - Mar 1
        assert len(result) == 5

    def test_feb29_non_leap_year_uses_feb28_to_mar1(self) -> None:
        result = build_on_this_day(date(2024, 2, 29), years_back=1)
        # 2023 is not a leap year, so use Feb 28 - Mar 1
        assert result[0].start == datetime(2023, 2, 27, 0, 0, 0)
        assert result[0].end == datetime(2023, 3, 1, 23, 59, 59)

    def test_feb29_leap_year_uses_normal_range(self) -> None:
        result = build_on_this_day(date(2024, 2, 29), years_back=4)
        # 4 years back from 2024: 2023, 2022, 2021, 2020
        # 2020 is a leap year
        leap_range = result[3]  # 2020
        assert leap_range.start == datetime(2020, 2, 28, 0, 0, 0)
        assert leap_range.end == datetime(2020, 3, 1, 23, 59, 59)
