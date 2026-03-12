"""Additional tests for text_builder edge cases not covered in test_titles.py."""

from __future__ import annotations

from datetime import date

import pytest

from immich_memories.titles.text_builder import (
    SelectionType,
    _generate_date_range_title,
    calculate_birthday_age,
    generate_title,
    get_month_name,
    get_ordinal,
    infer_selection_type,
)


class TestCalculateBirthdayAge:
    """Tests for birthday age calculation."""

    def test_birthday_already_passed(self):
        """Age increments when birthday has passed in the video year."""
        assert calculate_birthday_age(date(2000, 3, 1), date(2025, 6, 15)) == 25

    def test_birthday_not_yet_passed(self):
        """Age does not increment before birthday in the video year."""
        assert calculate_birthday_age(date(2000, 9, 15), date(2025, 6, 15)) == 24

    def test_birthday_same_day(self):
        """Age increments on the exact birthday."""
        assert calculate_birthday_age(date(2000, 6, 15), date(2025, 6, 15)) == 25

    def test_birthday_day_before(self):
        """Day before birthday gives previous year's age."""
        assert calculate_birthday_age(date(2000, 6, 15), date(2025, 6, 14)) == 24

    def test_newborn(self):
        """Same year, before birthday, gives 0."""
        assert calculate_birthday_age(date(2025, 12, 1), date(2025, 6, 1)) == 0

    def test_same_date(self):
        """Birth date equals video date gives 0."""
        assert calculate_birthday_age(date(2025, 6, 15), date(2025, 6, 15)) == 0

    def test_negative_clamped_to_zero(self):
        """Video date before birth date returns 0 (clamped)."""
        assert calculate_birthday_age(date(2025, 6, 15), date(2020, 1, 1)) == 0

    def test_leap_year_birthday(self):
        """Feb 29 birthday evaluated on non-leap year."""
        # Born Feb 29 2000, video date March 1 2025 (not a leap year)
        assert calculate_birthday_age(date(2000, 2, 29), date(2025, 3, 1)) == 25

    def test_leap_year_birthday_before(self):
        """Feb 29 birthday, video date Feb 28 non-leap year."""
        assert calculate_birthday_age(date(2000, 2, 29), date(2025, 2, 28)) == 24


class TestGenerateDateRangeTitle:
    """Tests for _generate_date_range_title edge cases."""

    def test_full_calendar_year(self):
        """Jan 1 to Dec 31 same year renders as just the year."""
        result = _generate_date_range_title(date(2024, 1, 1), date(2024, 12, 31), None, "en")
        assert result.main_title == "2024"
        assert result.selection_type == SelectionType.CALENDAR_YEAR

    def test_single_month(self):
        """Dates within the same month render as 'Month Year'."""
        result = _generate_date_range_title(date(2024, 6, 5), date(2024, 6, 28), None, "en")
        assert result.main_title == "June 2024"
        assert result.selection_type == SelectionType.SINGLE_MONTH

    def test_same_year_multi_month(self):
        """Multi-month range in same year renders as 'Month to Month Year'."""
        result = _generate_date_range_title(date(2024, 3, 1), date(2024, 8, 15), None, "en")
        assert result.main_title == "March to August 2024"
        assert result.selection_type == SelectionType.MONTH_RANGE

    def test_cross_year_range(self):
        """Range spanning years renders as 'Month Year to Month Year'."""
        result = _generate_date_range_title(date(2023, 11, 1), date(2024, 2, 28), None, "en")
        assert result.main_title == "November 2023 to February 2024"
        assert result.selection_type == SelectionType.MONTH_RANGE

    def test_with_person_name(self):
        """Person name is passed through as subtitle."""
        result = _generate_date_range_title(date(2024, 1, 1), date(2024, 12, 31), "Alice", "en")
        assert result.subtitle == "Alice"

    def test_french_locale(self):
        """French locale uses correct month names and connectors."""
        result = _generate_date_range_title(date(2024, 3, 1), date(2024, 8, 15), None, "fr")
        assert result.main_title == "Mars à Août 2024"

    def test_french_cross_year(self):
        """French cross-year range uses correct format."""
        result = _generate_date_range_title(date(2023, 11, 1), date(2024, 2, 28), None, "fr")
        assert result.main_title == "Novembre 2023 à Février 2024"


class TestGenerateTitleValidation:
    """Tests for generate_title input validation."""

    def test_calendar_year_requires_year(self):
        """Calendar year selection without year raises ValueError."""
        with pytest.raises(ValueError, match="Year required"):
            generate_title(SelectionType.CALENDAR_YEAR)

    def test_birthday_requires_age(self):
        """Birthday selection without age raises ValueError."""
        with pytest.raises(ValueError, match="Birthday age required"):
            generate_title(SelectionType.BIRTHDAY_YEAR)

    def test_single_month_requires_month_and_year(self):
        """Single month selection without month or year raises ValueError."""
        with pytest.raises(ValueError, match="Month and year required"):
            generate_title(SelectionType.SINGLE_MONTH, year=2024)

    def test_month_range_requires_months(self):
        """Month range without start/end month raises ValueError."""
        with pytest.raises(ValueError, match="Start and end month required"):
            generate_title(SelectionType.MONTH_RANGE)

    def test_month_range_requires_year(self):
        """Month range without any year raises ValueError."""
        with pytest.raises(ValueError, match="Year.*required"):
            generate_title(SelectionType.MONTH_RANGE, start_month=1, end_month=3)

    def test_date_range_requires_dates(self):
        """Date range without dates raises ValueError."""
        with pytest.raises(ValueError, match="Start and end date required"):
            generate_title(SelectionType.DATE_RANGE)

    def test_month_range_same_year_via_year(self):
        """Month range uses 'year' param when start_year/end_year not given."""
        result = generate_title(
            SelectionType.MONTH_RANGE,
            start_month=1,
            end_month=6,
            year=2024,
        )
        assert result.main_title == "January to June 2024"

    def test_month_range_different_years(self):
        """Month range with different start/end years."""
        result = generate_title(
            SelectionType.MONTH_RANGE,
            start_month=11,
            end_month=2,
            start_year=2023,
            end_year=2024,
        )
        assert result.main_title == "November 2023 to February 2024"


class TestGetMonthNameEdgeCases:
    """Edge cases for get_month_name."""

    def test_month_zero_raises(self):
        """Month 0 raises ValueError."""
        with pytest.raises(ValueError, match="1-12"):
            get_month_name(0)

    def test_month_13_raises(self):
        """Month 13 raises ValueError."""
        with pytest.raises(ValueError, match="1-12"):
            get_month_name(13)

    def test_unknown_locale_falls_back_to_english(self):
        """Unknown locale falls back to English."""
        assert get_month_name(1, locale="zz") == "January"


class TestGetOrdinalEdgeCases:
    """Edge cases for get_ordinal."""

    @pytest.mark.parametrize(
        "n,expected",
        [
            pytest.param(11, "11th", id="11th-special"),
            pytest.param(12, "12th", id="12th-special"),
            pytest.param(13, "13th", id="13th-special"),
            pytest.param(21, "21st", id="21st"),
            pytest.param(22, "22nd", id="22nd"),
            pytest.param(23, "23rd", id="23rd"),
            pytest.param(111, "111th", id="111th-special"),
            pytest.param(112, "112th", id="112th-special"),
        ],
    )
    def test_english_ordinals(self, n: int, expected: str):
        """English ordinals handle teens and irregular suffixes."""
        assert get_ordinal(n, "en") == expected

    def test_unknown_locale_returns_plain_number(self):
        """Unknown locale returns just the number as string."""
        assert get_ordinal(5, "zz") == "5"


class TestInferSelectionTypeEdgeCases:
    """Edge cases for infer_selection_type."""

    def test_no_params_defaults_to_calendar_year(self):
        """No params provided defaults to CALENDAR_YEAR."""
        assert infer_selection_type() == SelectionType.CALENDAR_YEAR

    def test_birthday_takes_priority(self):
        """Birthday age takes priority over other params."""
        result = infer_selection_type(
            year=2024,
            month=6,
            birthday_age=5,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        assert result == SelectionType.BIRTHDAY_YEAR
