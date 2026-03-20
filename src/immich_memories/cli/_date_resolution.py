"""Date range resolution and duration scaling for CLI commands."""

from __future__ import annotations

import click

from immich_memories.timeperiod import (
    DateRange,
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)


def resolve_date_range(
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
    memory_type: str | None = None,
    season: str | None = None,
    month: int | None = None,
    hemisphere: str = "north",
) -> DateRange | list[DateRange]:
    """Resolve date range from command line options.

    When --memory-type is set, delegates to preset date builders.
    Otherwise falls through to manual date range options.

    Returns:
        DateRange or list[DateRange] for the selected period

    Raises:
        click.UsageError: If invalid combination of options
    """
    if memory_type:
        return _resolve_memory_type_dates(memory_type, year, season, month, hemisphere)

    if start and end:
        try:
            start_date = parse_date(start)
            end_date = parse_date(end)
            return custom_range(start_date, end_date)
        except ValueError as e:
            raise click.UsageError(str(e))

    if start and period:
        try:
            start_date = parse_date(start)
            return from_period(start_date, period)
        except ValueError as e:
            raise click.UsageError(str(e))

    if year:
        if birthday:
            try:
                bday = parse_date(birthday)
                return birthday_year(bday, year)
            except ValueError as e:
                raise click.UsageError(str(e))
        else:
            return calendar_year(year)

    raise click.UsageError(
        "You must specify a time period. Use one of:\n"
        "  --year YEAR                    Calendar year (Jan 1 - Dec 31)\n"
        "  --year YEAR --birthday DATE    Year from birthday (e.g., Feb 7 - Feb 6)\n"
        "  --start DATE --end DATE        Custom date range\n"
        "  --start DATE --period PERIOD   Period from start (e.g., 6m, 1y)\n"
        "  --memory-type TYPE             Memory type preset (season, monthly_highlights, etc.)"
    )


def _resolve_memory_type_dates(
    memory_type: str,
    year: int | None,
    season: str | None,
    month: int | None,
    hemisphere: str,
) -> DateRange | list[DateRange]:
    """Resolve date ranges from memory type preset."""
    from immich_memories.memory_types.date_builders import (
        build_month,
        build_on_this_day,
        build_season,
    )

    if memory_type == "season":
        if not season:
            raise click.UsageError("--season is required with --memory-type season")
        if not year:
            raise click.UsageError("--year is required with --memory-type season")
        return build_season(season, year, hemisphere)

    if memory_type == "monthly_highlights":
        if month is None:
            raise click.UsageError("--month is required with --memory-type monthly_highlights")
        if not year:
            raise click.UsageError("--year is required with --memory-type monthly_highlights")
        return build_month(month, year)

    if memory_type == "on_this_day":
        from datetime import date

        return build_on_this_day(date.today())

    # Types that use calendar year: year_in_review, person_spotlight, multi_person
    if not year:
        raise click.UsageError(f"--year is required with --memory-type {memory_type}")
    return calendar_year(year)


def duration_from_date_range(date_range: DateRange) -> float:
    """Scale duration by date range: 1 month = 60s, 1 year = 600s.

    Quadratic curve fitted through (1mo, 60s), (6mo, 360s), (12mo, 600s).
    Linear ~60s/month for the first half, then decelerates toward 600s.
    """
    months = max(1, (date_range.end - date_range.start).days + 1) / 30.0
    duration = (-20 * months**2 + 800 * months - 120) / 11
    return float(max(30, min(600, duration)))


def default_duration_for_type(
    memory_type: str | None, date_range: DateRange | None
) -> float | None:
    """Get default duration in seconds for a memory type.

    Date-range based types scale with span (1 month = 60s, 1 year = 600s).
    Fixed types: on_this_day (45s), trip (35s/day), person without range (120s).
    """
    if not memory_type:
        return None

    if memory_type == "on_this_day":
        return 45.0
    if memory_type == "trip" and date_range is not None:
        days = max(1, (date_range.end - date_range.start).days + 1)
        return float(max(60, min(600, days * 35)))
    if memory_type in ("person_spotlight", "multi_person"):
        if date_range is None:
            return 120.0
        return duration_from_date_range(date_range)

    # Everything else: scale by date range
    if date_range is not None:
        return duration_from_date_range(date_range)
    return None
