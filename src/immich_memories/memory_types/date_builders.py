"""Date range builders for memory types.

Provides functions to build DateRange objects for seasons, months,
and "on this day" lookbacks.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from immich_memories.timeperiod import DateRange, calendar_year

# Northern hemisphere season definitions: season -> (start_month, end_month)
# Winter spans two calendar years (Dec of year to Feb of year+1).
_NORTH_SEASONS: dict[str, tuple[int, int]] = {
    "spring": (3, 5),
    "summer": (6, 8),
    "fall": (9, 11),
    "winter": (12, 2),
}

# Southern hemisphere swaps: north summer <-> south winter, etc.
_SOUTH_SWAP: dict[str, str] = {
    "spring": "fall",
    "summer": "winter",
    "fall": "spring",
    "winter": "summer",
}

_VALID_SEASONS = {"spring", "summer", "fall", "autumn", "winter"}
_VALID_HEMISPHERES = {"north", "south"}


def build_season(
    season: str,
    year: int,
    hemisphere: str = "north",
) -> DateRange:
    """Build a DateRange for a meteorological season.

    Args:
        season: One of "spring", "summer", "fall"/"autumn", "winter".
        year: The year. For winter, this is the start year (Dec).
        hemisphere: "north" or "south". Southern reverses seasons.

    Returns:
        DateRange covering the full season.

    Raises:
        ValueError: If season or hemisphere is invalid.
    """
    season = season.lower()
    hemisphere = hemisphere.lower()

    if season not in _VALID_SEASONS:
        raise ValueError(
            f"Invalid season: '{season}'. Expected one of: {', '.join(sorted(_VALID_SEASONS))}"
        )
    if hemisphere not in _VALID_HEMISPHERES:
        raise ValueError(f"Invalid hemisphere: '{hemisphere}'. Expected 'north' or 'south'")

    # Normalize "autumn" to "fall"
    if season == "autumn":
        season = "fall"

    # Southern hemisphere: swap to the corresponding northern season
    if hemisphere == "south":
        season = _SOUTH_SWAP[season]

    start_month, end_month = _NORTH_SEASONS[season]

    # Handle winter spanning two years
    end_year = year + 1 if start_month > end_month else year

    last_day = calendar.monthrange(end_year, end_month)[1]

    return DateRange(
        start=datetime(year, start_month, 1, 0, 0, 0),
        end=datetime(end_year, end_month, last_day, 23, 59, 59),
    )


def build_month(month: int, year: int) -> DateRange:
    """Build a DateRange for a full calendar month.

    Args:
        month: Month number (1-12).
        year: The year.

    Returns:
        DateRange covering the full month.

    Raises:
        ValueError: If month is not 1-12.
    """
    if month < 1 or month > 12:
        raise ValueError(f"Invalid month: {month}. Expected a value between 1 and 12.")

    last_day = calendar.monthrange(year, month)[1]

    return DateRange(
        start=datetime(year, month, 1, 0, 0, 0),
        end=datetime(year, month, last_day, 23, 59, 59),
    )


def build_on_this_day(
    target_date: date,
    years_back: int | None = None,
) -> list[DateRange]:
    """Build a list of +/-1 day ranges for each previous year.

    For each year going back from target_date, creates a range from
    (target_date - 1 day) to (target_date + 1 day) in that year.

    Args:
        target_date: The reference date (e.g., today).
        years_back: How many years to look back. None = all years (30-year max).
            Explicit 0 or negative returns empty.

    Returns:
        List of DateRange objects, most recent year first.
    """
    # WHY: 30-year max covers most personal photo libraries without excessive API calls
    effective_years_back = years_back if years_back is not None else 30
    if effective_years_back <= 0:
        return []

    ranges: list[DateRange] = []

    for i in range(1, effective_years_back + 1):
        past_year = target_date.year - i

        # Resolve the target day in the past year
        center = _resolve_date_in_year(target_date, past_year)

        day_before = _subtract_one_day(center)
        day_after = _add_one_day(center)

        ranges.append(
            DateRange(
                start=datetime(day_before.year, day_before.month, day_before.day, 0, 0, 0),
                end=datetime(day_after.year, day_after.month, day_after.day, 23, 59, 59),
            )
        )

    return ranges


def build_then_and_now(year: int, years_back: int) -> list[DateRange]:
    """Build the two eras of a then-and-now, most recent first.

    Two whole calendar years rather than two narrow windows: a short window in a
    year long past is usually empty, and the contrast needs enough material on
    both sides to read.

    Callers must treat the result as an ordered list of eras and key off the
    ranges themselves, never off ``range.start.year`` — the two eras are equal
    calendar years today, but nothing in the memory type requires them to be.

    Args:
        year: The recent end of the pair.
        years_back: The gap to the other end, in years. Must be at least 1.

    Returns:
        ``[now, then]`` — most recent first, matching the other multi-range
        types, whose callers derive a display span as ``ranges[-1].start`` to
        ``ranges[0].end``.

    Raises:
        ValueError: If years_back is zero or negative.
    """
    if years_back <= 0:
        raise ValueError("years_back must be at least 1 for a then-and-now memory")
    return [calendar_year(year), calendar_year(year - years_back)]


def _resolve_date_in_year(target: date, year: int) -> date:
    """Resolve a date into a specific year, handling Feb 29."""
    try:
        return date(year, target.month, target.day)
    except ValueError:
        # Feb 29 in a non-leap year -> use Feb 28
        return date(year, 2, 28)


def _subtract_one_day(d: date) -> date:
    """Subtract one day from a date."""
    return date.fromordinal(d.toordinal() - 1)


def _add_one_day(d: date) -> date:
    """Add one day to a date."""
    return date.fromordinal(d.toordinal() + 1)


def build_trip(start: date, end: date) -> DateRange:
    """Build a DateRange for a trip from start to end date (inclusive).

    Args:
        start: Trip start date.
        end: Trip end date.

    Returns:
        DateRange covering the full trip.
    """
    return DateRange(
        start=datetime(start.year, start.month, start.day, 0, 0, 0),
        end=datetime(end.year, end.month, end.day, 23, 59, 59),
    )


def build_special_day(
    day: date,
    window: tuple[datetime, datetime] | None,
) -> DateRange:
    """The scope of one day the library says something happened on.

    A recorded window *is* the scope, not a hint at it. The catalogue only
    records one when trimming to it removes a meaningful slice of the day, which
    is what says "the memory starts at the circuit, not at the cat on the
    balcony that morning" — so anything softer than using it as the bounds
    throws that judgement away. Its instants are passed through untouched,
    offset included, because they reach Immich's takenAfter/takenBefore as
    written and a window can legitimately end after midnight.

    **Shrink, don't pad.** If the window yields too few usable clips the memory
    gets shorter. It must never widen back to the whole day: that silently
    undoes the trim, and it is padding by another name.

    One range, never two. A day is one occasion, and the fetch list has no
    priority ordering to express "this window first, then the rest".
    """
    if window is not None:
        return DateRange(start=window[0], end=window[1])
    return DateRange(
        start=datetime(day.year, day.month, day.day, 0, 0, 0),
        end=datetime(day.year, day.month, day.day, 23, 59, 59),
    )


# Fixed-date holidays. Deliberately short: this is a starting set, and any date
# the list misses can be given as MM-DD.
_FIXED_HOLIDAYS = {
    "new_year": (1, 1),
    "valentines": (2, 14),
    "halloween": (10, 31),
    "christmas_eve": (12, 24),
    "christmas": (12, 25),
    "new_years_eve": (12, 31),
}


# Everything resolve_holiday answers to. Add a holiday to _FIXED_HOLIDAYS or a
# branch below and name it here, and every caller that asks "which dates are
# already a holiday memory" picks it up without being edited.
KNOWN_HOLIDAYS = (*_FIXED_HOLIDAYS, "easter", "thanksgiving", "mothers_day", "fathers_day")


def _easter(year: int) -> date:
    """Western Easter Sunday, by the anonymous Gregorian algorithm.

    Computed rather than tabulated: a table of Easter dates is wrong the year
    after it is written, and this is the holiday most likely to be photographed
    by families with young children.
    """
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = ((h + length - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The nth given weekday of a month, e.g. the fourth Thursday of November."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (nth - 1))


def resolve_holiday(holiday: str, year: int) -> date:
    """The date a holiday falls on in a given year.

    Accepts a known name or an explicit ``MM-DD``. Moving holidays are computed,
    so they stay correct in years nobody has thought about yet.
    """
    key = holiday.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _FIXED_HOLIDAYS:
        month, day = _FIXED_HOLIDAYS[key]
        return date(year, month, day)
    if key == "easter":
        return _easter(year)
    if key == "thanksgiving":
        return _nth_weekday(year, 11, weekday=3, nth=4)
    if key == "mothers_day":
        return _nth_weekday(year, 5, weekday=6, nth=2)
    if key == "fathers_day":
        return _nth_weekday(year, 6, weekday=6, nth=3)

    try:
        month_str, day_str = holiday.strip().split("-")
        return date(year, int(month_str), int(day_str))
    except (ValueError, TypeError) as exc:
        known = ", ".join(sorted(KNOWN_HOLIDAYS))
        raise ValueError(
            f"Unknown holiday {holiday!r}. Use one of: {known}, or a MM-DD date."
        ) from exc


def build_holiday(
    holiday: str,
    year: int,
    years_back: int = 5,
    window_days: int = 2,
    today: date | None = None,
) -> list[DateRange]:
    """One window per year around a holiday, most recent first.

    A holiday is the date a library is most likely to have every single year, so
    it is worth spanning years the way On This Day does rather than covering one
    occasion.

    ``today`` only affects the starting year, and only when it makes the first
    window impossible: asking for Christmas in August would otherwise include a
    Christmas that has not happened, spending one of the requested years on a
    window no photo can fall in. An explicit ``year`` from the caller is a
    choice and is left alone.
    """
    if years_back <= 0:
        return []

    if today is not None and resolve_holiday(holiday, year) > today:
        year -= 1

    ranges: list[DateRange] = []
    for offset in range(years_back):
        centre = resolve_holiday(holiday, year - offset)
        start = centre - timedelta(days=window_days)
        end = centre + timedelta(days=window_days)
        ranges.append(
            DateRange(
                start=datetime(start.year, start.month, start.day, 0, 0, 0),
                end=datetime(end.year, end.month, end.day, 23, 59, 59),
            )
        )
    return ranges
