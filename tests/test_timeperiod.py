"""Tests for time period utilities."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from immich_memories.timeperiod import (
    DateRange,
    Period,
    PeriodUnit,
    available_years,
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)


class TestDateRange:
    """Tests for DateRange class."""

    def test_days_property(self):
        """Test days calculation."""
        dr = DateRange(
            start=datetime(2024, 1, 1, 0, 0, 0),
            end=datetime(2024, 1, 31, 23, 59, 59),
        )
        assert dr.days == 31

    def test_description_calendar_year(self):
        """Test description for calendar year."""
        dr = calendar_year(2024)
        assert dr.description == "Year 2024"

    def test_description_custom_range(self):
        """Test description for custom range."""
        dr = DateRange(
            start=datetime(2024, 6, 1, 0, 0, 0),
            end=datetime(2024, 8, 31, 23, 59, 59),
        )
        assert dr.description == "Jun 01, 2024 - Aug 31, 2024"

    def test_is_calendar_year_true(self):
        """Test is_calendar_year for full year."""
        dr = calendar_year(2024)
        assert dr.is_calendar_year is True

    def test_is_calendar_year_false(self):
        """Test is_calendar_year for partial year."""
        dr = DateRange(
            start=datetime(2024, 1, 1, 0, 0, 0),
            end=datetime(2024, 6, 30, 23, 59, 59),
        )
        assert dr.is_calendar_year is False

    def test_contains_datetime(self):
        """Test contains with datetime."""
        dr = calendar_year(2024)
        assert dr.contains(datetime(2024, 6, 15, 12, 0, 0)) is True
        assert dr.contains(datetime(2023, 12, 31, 23, 59, 59)) is False
        assert dr.contains(datetime(2025, 1, 1, 0, 0, 0)) is False

    def test_contains_date(self):
        """Test contains with date."""
        dr = calendar_year(2024)
        assert dr.contains(date(2024, 6, 15)) is True
        assert dr.contains(date(2023, 12, 31)) is False


class TestPeriod:
    """Tests for Period class."""

    def test_parse_days(self):
        """Test parsing days period."""
        period = Period.parse("30d")
        assert period.value == 30
        assert period.unit == PeriodUnit.DAYS

    def test_parse_weeks(self):
        """Test parsing weeks period."""
        period = Period.parse("2w")
        assert period.value == 2
        assert period.unit == PeriodUnit.WEEKS

    def test_parse_months(self):
        """Test parsing months period."""
        period = Period.parse("6m")
        assert period.value == 6
        assert period.unit == PeriodUnit.MONTHS

    def test_parse_years(self):
        """Test parsing years period."""
        period = Period.parse("1y")
        assert period.value == 1
        assert period.unit == PeriodUnit.YEARS

    def test_parse_with_whitespace(self):
        """Test parsing with whitespace."""
        period = Period.parse("  6m  ")
        assert period.value == 6
        assert period.unit == PeriodUnit.MONTHS

    def test_parse_uppercase(self):
        """Test parsing uppercase is converted."""
        period = Period.parse("6M")
        assert period.value == 6
        assert period.unit == PeriodUnit.MONTHS

    @pytest.mark.parametrize(
        "input_str",
        [
            pytest.param("invalid", id="no-digits-no-unit"),
            pytest.param("30", id="digits-only"),
            pytest.param("m", id="unit-only"),
            pytest.param("3x", id="unknown-unit"),
        ],
    )
    def test_parse_invalid_inputs(self, input_str):
        """Invalid period strings are rejected."""
        with pytest.raises(ValueError, match="Invalid period format"):
            Period.parse(input_str)

    def test_add_to_date_days(self):
        """Test adding days to date."""
        period = Period(value=30, unit=PeriodUnit.DAYS)
        result = period.add_to_date(date(2024, 1, 1))
        assert result == datetime(2024, 1, 31, 0, 0, 0)

    def test_add_to_date_weeks(self):
        """Test adding weeks to date."""
        period = Period(value=2, unit=PeriodUnit.WEEKS)
        result = period.add_to_date(date(2024, 1, 1))
        assert result == datetime(2024, 1, 15, 0, 0, 0)

    def test_add_to_date_months(self):
        """Test adding months to date."""
        period = Period(value=6, unit=PeriodUnit.MONTHS)
        result = period.add_to_date(date(2024, 1, 1))
        assert result == datetime(2024, 7, 1, 0, 0, 0)

    def test_add_to_date_months_day_overflow(self):
        """Test adding months handles day overflow."""
        period = Period(value=1, unit=PeriodUnit.MONTHS)
        result = period.add_to_date(date(2024, 1, 31))
        # Jan 31 + 1 month = Feb 29 (2024 is leap year)
        assert result == datetime(2024, 2, 29, 0, 0, 0)

    def test_add_to_date_years(self):
        """Test adding years to date."""
        period = Period(value=1, unit=PeriodUnit.YEARS)
        result = period.add_to_date(date(2024, 1, 1))
        assert result == datetime(2025, 1, 1, 0, 0, 0)

    def test_add_to_date_years_leap_year(self):
        """Test adding years handles leap year edge case."""
        period = Period(value=1, unit=PeriodUnit.YEARS)
        result = period.add_to_date(date(2024, 2, 29))
        # Feb 29, 2024 + 1 year = Feb 28, 2025 (2025 is not leap year)
        assert result == datetime(2025, 2, 28, 0, 0, 0)

    def test_str_representation(self):
        """Test string representation."""
        period = Period(value=6, unit=PeriodUnit.MONTHS)
        assert str(period) == "6m"


class TestCalendarYear:
    """Tests for calendar_year function."""

    def test_calendar_year_basic(self):
        """Test basic calendar year generation."""
        dr = calendar_year(2024)
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 12, 31, 23, 59, 59)

    def test_calendar_year_is_calendar_year(self):
        """Test that result is identified as calendar year."""
        dr = calendar_year(2024)
        assert dr.is_calendar_year is True

    def test_calendar_year_days(self):
        """Test days in regular year."""
        dr = calendar_year(2023)
        assert dr.days == 365

    def test_calendar_year_leap_year(self):
        """Test days in leap year."""
        dr = calendar_year(2024)
        assert dr.days == 366


class TestBirthdayYear:
    """Tests for birthday_year function."""

    def test_birthday_year_basic(self):
        """Test basic birthday year generation."""
        dr = birthday_year(date(1990, 2, 7), 2024)
        assert dr.start == datetime(2024, 2, 7, 0, 0, 0)
        assert dr.end == datetime(2025, 2, 6, 23, 59, 59)

    def test_birthday_year_not_calendar_year(self):
        """Test that birthday year is not identified as calendar year."""
        dr = birthday_year(date(1990, 2, 7), 2024)
        assert dr.is_calendar_year is False

    def test_birthday_year_leap_year_birthday(self):
        """Test Feb 29 birthday on non-leap year."""
        dr = birthday_year(date(2000, 2, 29), 2023)
        # 2023 is not leap year, so Feb 28 is used
        assert dr.start == datetime(2023, 2, 28, 0, 0, 0)

    def test_birthday_year_datetime_input(self):
        """Test with datetime input."""
        dr = birthday_year(datetime(1990, 2, 7, 12, 0, 0), 2024)
        assert dr.start == datetime(2024, 2, 7, 0, 0, 0)


class TestFromPeriod:
    """Tests for from_period function."""

    def test_from_period_six_months(self):
        """Test 6 month period."""
        dr = from_period(date(2024, 1, 1), "6m")
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 6, 30, 23, 59, 59)

    def test_from_period_one_year(self):
        """Test 1 year period."""
        dr = from_period(date(2024, 1, 1), "1y")
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 12, 31, 23, 59, 59)

    def test_from_period_thirty_days(self):
        """Test 30 day period."""
        dr = from_period(date(2024, 1, 1), "30d")
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 1, 30, 23, 59, 59)

    def test_from_period_two_weeks(self):
        """Test 2 week period."""
        dr = from_period(date(2024, 1, 1), "2w")
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 1, 14, 23, 59, 59)

    def test_from_period_period_object(self):
        """Test with Period object."""
        period = Period(value=6, unit=PeriodUnit.MONTHS)
        dr = from_period(date(2024, 1, 1), period)
        assert dr.end == datetime(2024, 6, 30, 23, 59, 59)


class TestCustomRange:
    """Tests for custom_range function."""

    def test_custom_range_basic(self):
        """Test basic custom range."""
        dr = custom_range(date(2024, 6, 1), date(2024, 8, 31))
        assert dr.start == datetime(2024, 6, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 8, 31, 23, 59, 59)

    def test_custom_range_same_day(self):
        """Test single day range."""
        dr = custom_range(date(2024, 6, 15), date(2024, 6, 15))
        assert dr.start == datetime(2024, 6, 15, 0, 0, 0)
        assert dr.end == datetime(2024, 6, 15, 23, 59, 59)
        assert dr.days == 1

    def test_custom_range_end_before_start(self):
        """Test error when end is before start."""
        with pytest.raises(ValueError, match="End date.*cannot be before start date"):
            custom_range(date(2024, 8, 31), date(2024, 6, 1))

    def test_custom_range_datetime_input(self):
        """Test with datetime input."""
        dr = custom_range(
            datetime(2024, 6, 1, 10, 0, 0),
            datetime(2024, 8, 31, 15, 0, 0),
        )
        # Start should use provided time
        assert dr.start == datetime(2024, 6, 1, 10, 0, 0)
        # End should be normalized to end of day
        assert dr.end == datetime(2024, 8, 31, 23, 59, 59)


class TestParseDate:
    """Tests for parse_date function."""

    def test_parse_iso_format(self):
        """Test ISO format (YYYY-MM-DD)."""
        result = parse_date("2024-02-07")
        assert result == date(2024, 2, 7)

    def test_parse_slash_ymd(self):
        """Test YYYY/MM/DD format."""
        result = parse_date("2024/02/07")
        assert result == date(2024, 2, 7)

    def test_parse_european_format(self):
        """Test DD/MM/YYYY format."""
        result = parse_date("07/02/2024")
        assert result == date(2024, 2, 7)

    def test_parse_us_format(self):
        """Test MM/DD/YYYY format when unambiguous (month > 12)."""
        # Note: When day and month are both <= 12, DD/MM/YYYY is tried first
        # Use a date where month > 12 is invalid to force MM/DD interpretation
        result = parse_date("12/25/2024")  # December 25th
        assert result == date(2024, 12, 25)

    def test_parse_with_whitespace(self):
        """Test parsing with whitespace."""
        result = parse_date("  2024-02-07  ")
        assert result == date(2024, 2, 7)

    def test_parse_invalid_date(self):
        """Test invalid date raises error."""
        with pytest.raises(ValueError, match="Cannot parse date"):
            parse_date("not-a-date")


class TestAvailableYears:
    """Tests for available_years function."""

    def test_available_years_default(self):
        """Test default years list."""
        years = available_years(current_year=2024, years_back=5)
        assert years == [2024, 2023, 2022, 2021, 2020]

    def test_available_years_descending(self):
        """Test years are in descending order."""
        years = available_years(current_year=2024, years_back=3)
        assert years[0] > years[-1]

    def test_available_years_count(self):
        """Test correct number of years."""
        years = available_years(current_year=2024, years_back=20)
        assert len(years) == 20

    def test_available_years_single(self):
        """Single year back returns just that year."""
        years = available_years(current_year=2024, years_back=1)
        assert years == [2024]


class TestDateRangeEdgeCases:
    """Edge cases for date range operations."""

    def test_contains_boundary_start(self):
        """Start instant is included."""
        dr = calendar_year(2024)
        assert dr.contains(datetime(2024, 1, 1, 0, 0, 0)) is True

    def test_contains_boundary_end(self):
        """End instant is included."""
        dr = calendar_year(2024)
        assert dr.contains(datetime(2024, 12, 31, 23, 59, 59)) is True

    def test_contains_one_second_after_end(self):
        """One second after end is excluded."""
        dr = calendar_year(2024)
        assert dr.contains(datetime(2025, 1, 1, 0, 0, 0)) is False

    def test_birthday_year_jan_1_birthday(self):
        """Jan 1 birthday year spans exactly one calendar year."""
        dr = birthday_year(date(2000, 1, 1), 2024)
        assert dr.start == datetime(2024, 1, 1, 0, 0, 0)
        assert dr.end == datetime(2024, 12, 31, 23, 59, 59)

    def test_birthday_year_dec_31_birthday(self):
        """Dec 31 birthday wraps to next year."""
        dr = birthday_year(date(2000, 12, 31), 2024)
        assert dr.start == datetime(2024, 12, 31, 0, 0, 0)
        assert dr.end == datetime(2025, 12, 30, 23, 59, 59)

    def test_custom_range_single_day_has_one_day(self):
        """Same start and end date produces exactly 1 day."""
        dr = custom_range(date(2024, 6, 15), date(2024, 6, 15))
        assert dr.days == 1
