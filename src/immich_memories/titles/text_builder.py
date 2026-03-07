"""Dynamic title text generation based on video selection criteria.

This module handles generating appropriate title text based on:
- Calendar year selections
- Birthday year selections (with ordinals)
- Month selections
- Date range selections
- Person names as subtitles

Full localization support for English and French (extensible).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class SelectionType(Enum):
    """Type of video selection that determines title format."""

    CALENDAR_YEAR = "calendar_year"
    BIRTHDAY_YEAR = "birthday_year"
    SINGLE_MONTH = "single_month"
    MONTH_RANGE = "month_range"
    DATE_RANGE = "date_range"


@dataclass
class TitleInfo:
    """Generated title information."""

    main_title: str
    subtitle: str | None = None
    selection_type: SelectionType = SelectionType.CALENDAR_YEAR


# Month names by locale
MONTH_NAMES: dict[str, list[str]] = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "fr": [
        "Janvier",
        "Février",
        "Mars",
        "Avril",
        "Mai",
        "Juin",
        "Juillet",
        "Août",
        "Septembre",
        "Octobre",
        "Novembre",
        "Décembre",
    ],
}

# Title patterns by locale
TITLE_PATTERNS: dict[str, dict[str, str]] = {
    "en": {
        "year_ordinal": "{ordinal} Year",
        "month_year": "{month} {year}",
        "month_range_same_year": "{start_month} to {end_month} {year}",
        "month_range_different_year": "{start_month} {start_year} to {end_month} {end_year}",
    },
    "fr": {
        "year_ordinal": "{ordinal} Année",
        "month_year": "{month} {year}",
        "month_range_same_year": "{start_month} à {end_month} {year}",
        "month_range_different_year": "{start_month} {start_year} à {end_month} {end_year}",
    },
}


def get_month_name(month: int, locale: str = "en") -> str:
    """Get the localized month name.

    Args:
        month: Month number (1-12).
        locale: Language code ("en", "fr").

    Returns:
        Localized month name.
    """
    if locale not in MONTH_NAMES:
        locale = "en"

    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1-12, got {month}")

    return MONTH_NAMES[locale][month - 1]


def get_ordinal(n: int, locale: str = "en") -> str:
    """Get the localized ordinal string for a number.

    Args:
        n: The number to convert to ordinal.
        locale: Language code ("en", "fr").

    Returns:
        Ordinal string (e.g., "1st", "2nd", "1ère", "2ème").
    """
    if locale == "en":
        # English ordinals
        suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"
    elif locale == "fr":
        # French ordinals
        if n == 1:
            return "1ère"
        return f"{n}ème"
    else:
        # Fallback to just the number
        return str(n)


def generate_title(
    selection_type: SelectionType,
    *,
    year: int | None = None,
    month: int | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    person_name: str | None = None,
    birthday_age: int | None = None,
    locale: str = "en",
) -> TitleInfo:
    """Generate dynamic title based on video selection criteria.

    Args:
        selection_type: Type of selection (calendar year, birthday, month, etc.).
        year: Year for calendar year or single month selections.
        month: Month number for single month selections.
        start_month: Starting month for month range.
        end_month: Ending month for month range.
        start_year: Starting year for range spanning years.
        end_year: Ending year for range spanning years.
        start_date: Start date for date range selections.
        end_date: End date for date range selections.
        person_name: Name of person (used as subtitle).
        birthday_age: Age for birthday year selections.
        locale: Language code ("en", "fr").

    Returns:
        TitleInfo with main title and optional subtitle.
    """
    patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])

    if selection_type == SelectionType.CALENDAR_YEAR:
        # Simple year display
        if year is None:
            raise ValueError("Year required for calendar year selection")
        return TitleInfo(
            main_title=str(year),
            subtitle=person_name,
            selection_type=selection_type,
        )

    elif selection_type == SelectionType.BIRTHDAY_YEAR:
        # Birthday year with ordinal
        if birthday_age is None:
            raise ValueError("Birthday age required for birthday year selection")

        ordinal = get_ordinal(birthday_age, locale)
        main_title = patterns["year_ordinal"].format(ordinal=ordinal)

        return TitleInfo(
            main_title=main_title,
            subtitle=person_name,
            selection_type=selection_type,
        )

    elif selection_type == SelectionType.SINGLE_MONTH:
        # Single month display
        if month is None or year is None:
            raise ValueError("Month and year required for single month selection")

        month_name = get_month_name(month, locale)
        main_title = patterns["month_year"].format(month=month_name, year=year)

        return TitleInfo(
            main_title=main_title,
            subtitle=person_name,
            selection_type=selection_type,
        )

    elif selection_type == SelectionType.MONTH_RANGE:
        # Month range display
        if start_month is None or end_month is None:
            raise ValueError("Start and end month required for month range")

        start_month_name = get_month_name(start_month, locale)
        end_month_name = get_month_name(end_month, locale)

        # Determine if same year or different years
        s_year = start_year or year
        e_year = end_year or year

        if s_year is None or e_year is None:
            raise ValueError("Year(s) required for month range selection")

        if s_year == e_year:
            # Same year: "January to March 2024"
            main_title = patterns["month_range_same_year"].format(
                start_month=start_month_name,
                end_month=end_month_name,
                year=s_year,
            )
        else:
            # Different years: "November 2023 to February 2024"
            main_title = patterns["month_range_different_year"].format(
                start_month=start_month_name,
                start_year=s_year,
                end_month=end_month_name,
                end_year=e_year,
            )

        return TitleInfo(
            main_title=main_title,
            subtitle=person_name,
            selection_type=selection_type,
        )

    elif selection_type == SelectionType.DATE_RANGE:
        # Date range - analyze to determine best display format
        if start_date is None or end_date is None:
            raise ValueError("Start and end date required for date range selection")

        return _generate_date_range_title(start_date, end_date, person_name, locale)

    else:
        # Fallback
        return TitleInfo(
            main_title=str(year) if year else "Memories",
            subtitle=person_name,
            selection_type=selection_type,
        )


def _generate_date_range_title(
    start_date: date,
    end_date: date,
    person_name: str | None,
    locale: str,
) -> TitleInfo:
    """Generate title for a date range, choosing the best format.

    This function intelligently determines the best title format based on
    the span of the date range:
    - Full year → Just the year
    - Entire single month → "Month Year"
    - Multiple months in same year → "Month to Month Year"
    - Spanning years → "Month Year to Month Year"
    """
    patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])

    # Check if it's a full calendar year
    if (
        start_date.month == 1
        and start_date.day == 1
        and end_date.month == 12
        and end_date.day == 31
        and start_date.year == end_date.year
    ):
        return TitleInfo(
            main_title=str(start_date.year),
            subtitle=person_name,
            selection_type=SelectionType.CALENDAR_YEAR,
        )

    # Check if it's a single month (entirely within one month)
    if start_date.year == end_date.year and start_date.month == end_date.month:
        month_name = get_month_name(start_date.month, locale)
        main_title = patterns["month_year"].format(
            month=month_name,
            year=start_date.year,
        )
        return TitleInfo(
            main_title=main_title,
            subtitle=person_name,
            selection_type=SelectionType.SINGLE_MONTH,
        )

    # Multiple months
    start_month_name = get_month_name(start_date.month, locale)
    end_month_name = get_month_name(end_date.month, locale)

    if start_date.year == end_date.year:
        # Same year
        main_title = patterns["month_range_same_year"].format(
            start_month=start_month_name,
            end_month=end_month_name,
            year=start_date.year,
        )
    else:
        # Different years
        main_title = patterns["month_range_different_year"].format(
            start_month=start_month_name,
            start_year=start_date.year,
            end_month=end_month_name,
            end_year=end_date.year,
        )

    return TitleInfo(
        main_title=main_title,
        subtitle=person_name,
        selection_type=SelectionType.MONTH_RANGE,
    )


def generate_month_divider_text(month: int, year: int | None = None, locale: str = "en") -> str:
    """Generate text for a month divider screen.

    Args:
        month: Month number (1-12).
        year: Optional year to include.
        locale: Language code.

    Returns:
        Month divider text (e.g., "January" or "January 2024").
    """
    month_name = get_month_name(month, locale)

    if year is not None:
        patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])
        return patterns["month_year"].format(month=month_name, year=year)

    return month_name


def infer_selection_type(
    *,
    year: int | None = None,
    month: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    birthday_age: int | None = None,
) -> SelectionType:
    """Infer the selection type from provided parameters.

    Args:
        year: Year value.
        month: Month value.
        start_date: Start date.
        end_date: End date.
        birthday_age: Birthday age if applicable.

    Returns:
        Inferred SelectionType.
    """
    if birthday_age is not None:
        return SelectionType.BIRTHDAY_YEAR

    if start_date is not None and end_date is not None:
        return SelectionType.DATE_RANGE

    if month is not None and year is not None:
        return SelectionType.SINGLE_MONTH

    if year is not None:
        return SelectionType.CALENDAR_YEAR

    return SelectionType.CALENDAR_YEAR


def calculate_birthday_age(birth_date: date, video_date: date) -> int:
    """Calculate age in years from birth date to video date.

    Args:
        birth_date: Person's birth date.
        video_date: Date of the video.

    Returns:
        Age in years.
    """
    age = video_date.year - birth_date.year

    # Adjust if birthday hasn't occurred yet in the video year
    if (video_date.month, video_date.day) < (birth_date.month, birth_date.day):
        age -= 1

    return max(0, age)
