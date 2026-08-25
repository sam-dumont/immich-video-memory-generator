"""Time period utilities for flexible date range selection.

Supports:
- Full calendar years (Jan 1 - Dec 31)
- Birthday-based years (e.g., Feb 7, 2024 - Feb 7, 2025)
- Custom date ranges with start/end
- Period-based ranges (e.g., "6m" = 6 months from start)
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import NamedTuple


class PeriodUnit(StrEnum):
    """Time period units."""

    DAYS = "d"
    WEEKS = "w"
    MONTHS = "m"
    YEARS = "y"


class DateRange(NamedTuple):
    """A date range with start and end dates."""

    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        """Number of days in this range."""
        return (self.end.date() - self.start.date()).days + 1

    @property
    def description(self) -> str:
        """Human-readable description of the range."""
        if self.is_calendar_year:
            return f"Year {self.start.year}"

        start_str = self.start.strftime("%b %d, %Y")
        end_str = self.end.strftime("%b %d, %Y")
        return f"{start_str} - {end_str}"

    @property
    def is_calendar_year(self) -> bool:
        """Check if this is a full calendar year."""
        return (
            self.start.month == self.start.day == 1
            and self.end.month == 12
            and self.end.day == 31
            and self.start.year == self.end.year
        )

    def contains(self, dt: datetime | date) -> bool:
        """Check if a datetime falls within this range."""
        if isinstance(dt, date) and not isinstance(dt, datetime):
            dt = datetime.combine(dt, datetime.min.time())
        return self.start <= dt <= self.end


@dataclass
class Period:
    """A time period with a value and unit."""

    value: int
    unit: PeriodUnit

    @classmethod
    def parse(cls, period_str: str) -> Period:
        """Parse a period string like '6m', '1y', '2w', '30d'.

        Args:
            period_str: Period string (e.g., "6m", "1y", "2w", "30d")

        Returns:
            Period object

        Raises:
            ValueError: If period string is invalid
        """
        period_str = period_str.strip().lower()
        match = re.match(r"^(\d+)\s*([dwmy])$", period_str)

        if not match:
            raise ValueError(
                f"Invalid period format: '{period_str}'. "
                "Expected format: number + unit (d/w/m/y). "
                "Examples: '6m', '1y', '2w', '30d'"
            )

        value = int(match.group(1))
        unit_char = match.group(2)

        unit_map = {
            "d": PeriodUnit.DAYS,
            "w": PeriodUnit.WEEKS,
            "m": PeriodUnit.MONTHS,
            "y": PeriodUnit.YEARS,
        }

        return cls(value=value, unit=unit_map[unit_char])

    def add_to_date(self, start: date | datetime) -> datetime:
        """Add this period to a start date.

        Args:
            start: Starting date/datetime

        Returns:
            End datetime after adding the period
        """
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time())

        if self.unit == PeriodUnit.DAYS:
            return start + timedelta(days=self.value)

        if self.unit == PeriodUnit.WEEKS:
            return start + timedelta(weeks=self.value)

        if self.unit == PeriodUnit.MONTHS:
            # Add months while handling edge cases
            year = start.year + (start.month + self.value - 1) // 12
            month = (start.month + self.value - 1) % 12 + 1

            # Handle day overflow (e.g., Jan 31 + 1 month)
            import calendar

            max_day = calendar.monthrange(year, month)[1]
            day = min(start.day, max_day)

            return start.replace(year=year, month=month, day=day)

        if self.unit == PeriodUnit.YEARS:
            try:
                return start.replace(year=start.year + self.value)
            except ValueError:
                # Handle Feb 29 on non-leap year
                return start.replace(year=start.year + self.value, month=2, day=28)

        raise ValueError(f"Unknown period unit: {self.unit}")

    def __str__(self) -> str:
        """Return period as string (e.g., '6m')."""
        return f"{self.value}{self.unit.value}"


def calendar_year(year: int) -> DateRange:
    """Get date range for a full calendar year.

    Args:
        year: The calendar year (e.g., 2024)

    Returns:
        DateRange from Jan 1 to Dec 31 of that year
    """
    return DateRange(
        start=datetime(year, 1, 1, 0, 0, 0),
        end=datetime(year, 12, 31, 23, 59, 59),
    )


def same_day_in_year(day: date, year: int) -> date:
    """The day an annual date falls on in a given year.

    29 February lands on the 28th in a year that has no 29th, which is the
    reading a birthday takes everywhere else in the world.
    """
    try:
        return date(year, day.month, day.day)
    except ValueError:
        return date(year, 2, 28)


def birthday_year(
    birthday: date | datetime,
    year: int | None = None,
) -> DateRange:
    """The year of a person's life that ends on one of their birthdays.

    ``year`` names the birthday being celebrated, not a calendar year to run
    forward from: the window starts the day *after* the previous birthday and
    ends on this one. A birthday memory is about the year leading up to the
    party, and the party itself belongs in it.

    Running forward instead put the celebrated birthday outside its own memory
    and last year's inside it, and made the twelve months you got depend on
    which year number the request happened to name rather than on the event
    (#719).

    Example:
        birthday_year(date(1990, 2, 7), 2026)
        -> Feb 8, 2025 to Feb 7, 2026
    """
    if year is None:
        year = datetime.now().year

    celebrated = same_day_in_year(birthday, year)
    previous = same_day_in_year(birthday, year - 1)

    start = datetime.combine(previous + timedelta(days=1), time.min)
    end = datetime.combine(celebrated, time(23, 59, 59))
    return DateRange(start=start, end=end)


def from_period(
    start: date | datetime,
    period: str | Period,
) -> DateRange:
    """Create date range from start date and period.

    Args:
        start: Starting date/datetime
        period: Period string (e.g., "6m") or Period object

    Returns:
        DateRange from start to start + period - 1 day

    Example:
        from_period(date(2024, 1, 1), "6m")
        -> Jan 1, 2024 to Jun 30, 2024
    """
    if isinstance(start, date) and not isinstance(start, datetime):
        start = datetime.combine(start, datetime.min.time())

    if isinstance(period, str):
        period = Period.parse(period)

    # End is period from start, minus 1 day, at end of day
    end = (period.add_to_date(start) - timedelta(days=1)).replace(hour=23, minute=59, second=59)

    return DateRange(
        start=start,
        end=end,
    )


def custom_range(
    start: date | datetime,
    end: date | datetime,
) -> DateRange:
    """Create custom date range.

    Args:
        start: Start date/datetime
        end: End date/datetime

    Returns:
        DateRange with proper time bounds

    Raises:
        ValueError: If end is before start
    """
    if isinstance(start, date) and not isinstance(start, datetime):
        start = datetime.combine(start, datetime.min.time())

    if isinstance(end, date) and not isinstance(end, datetime):
        end = datetime.combine(end, datetime.max.time().replace(microsecond=0))
    else:
        # Ensure end is at end of day
        end = end.replace(hour=23, minute=59, second=59)

    if end < start:
        raise ValueError(f"End date ({end.date()}) cannot be before start date ({start.date()})")

    return DateRange(start=start, end=end)


def parse_date(date_str: str) -> date:
    """Parse a date string in various formats.

    Supported formats:
    - YYYY-MM-DD (ISO format)
    - DD/MM/YYYY
    - MM/DD/YYYY (if unambiguous or month > 12)
    - YYYY/MM/DD

    Args:
        date_str: Date string to parse

    Returns:
        Parsed date object

    Raises:
        ValueError: If date string cannot be parsed
    """
    date_str = date_str.strip()

    # Try ISO format first (YYYY-MM-DD)
    with contextlib.suppress(ValueError):
        return datetime.strptime(date_str, "%Y-%m-%d").date()

    # Try YYYY/MM/DD
    with contextlib.suppress(ValueError):
        return datetime.strptime(date_str, "%Y/%m/%d").date()

    # Try DD/MM/YYYY (European format)
    with contextlib.suppress(ValueError):
        return datetime.strptime(date_str, "%d/%m/%Y").date()

    # Try MM/DD/YYYY (US format)
    with contextlib.suppress(ValueError):
        return datetime.strptime(date_str, "%m/%d/%Y").date()

    raise ValueError(
        f"Cannot parse date: '{date_str}'. Expected format: YYYY-MM-DD, DD/MM/YYYY, or MM/DD/YYYY"
    )


def available_years(
    current_year: int | None = None,
    years_back: int = 20,
) -> list[int]:
    """Get list of available years for selection.

    Args:
        current_year: Current year (defaults to now)
        years_back: How many years back to include

    Returns:
        List of years in descending order
    """
    if current_year is None:
        current_year = datetime.now().year

    return list(range(current_year, current_year - years_back, -1))
