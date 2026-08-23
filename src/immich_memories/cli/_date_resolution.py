"""Date range resolution and duration scaling for CLI commands."""

from __future__ import annotations

from datetime import date, datetime

import click

from immich_memories.timeperiod import (
    DateRange,
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)


def _parse_birthday(value: str) -> date:
    """Parse the birthday option's documented MM/DD forms before generic dates."""
    for candidate, date_format in (
        (value, "%m/%d/%Y"),
        (f"{value}/2000", "%m/%d/%Y"),
    ):
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue
    return parse_date(value)


def _resolve_manual_dates(
    start: str | None,
    end: str | None,
    period: str | None,
) -> DateRange | None:
    """Try to resolve --start/--end or --start/--period to a DateRange.

    Returns None if neither combination is provided.
    """
    if start and end:
        try:
            return custom_range(parse_date(start), parse_date(end))
        except ValueError as e:
            raise click.UsageError(str(e))
    if start and period:
        try:
            return from_period(parse_date(start), period)
        except ValueError as e:
            raise click.UsageError(str(e))
    return None


def infer_memory_type(
    memory_type: str | None,
    *,
    year: int | None,
    month: int | None,
    has_person: bool = False,
    season: str | None = None,
    birthday: str | None = None,
    from_album: str | None = None,
) -> str | None:
    """Work out which memory the date flags describe.

    `--month` used to do nothing unless `--memory-type` was also given, so
    `--year 2025 --month 7` quietly rendered the whole year. Inferring the type
    from the dates means the combination a user naturally types does what it
    looks like it does, without `--month` depending on a second flag.

    An explicit `--memory-type` always wins, so existing invocations are
    unchanged. A year on its own is only read as a yearly review when nothing
    else narrows the selection -- with a person, a season or a birthday the user
    has asked for something specific, and guessing would override it. An album
    brings its own assets, so the date flags say nothing about it.
    """
    if memory_type or from_album:
        return memory_type
    if year is None:
        return None
    if month is not None:
        return "monthly_highlights"
    if has_person or season or birthday:
        return None
    return "year_in_review"


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
    years_back: int | None = None,
    on_this_day_target: date | None = None,
    holiday: str | None = None,
) -> DateRange | list[DateRange]:
    """Resolve date range from command line options.

    When --memory-type is set, delegates to preset date builders.
    --start/--end can override the preset's default date range.
    Otherwise falls through to manual date range options.
    """
    if memory_type:
        default_range = _resolve_memory_type_dates(
            memory_type,
            year,
            season,
            month,
            hemisphere,
            years_back,
            on_this_day_target,
            holiday,
        )
        manual_range = _resolve_manual_dates(start, end, period)
        if manual_range:
            return manual_range
        if memory_type == "person_spotlight" and birthday:
            try:
                return birthday_year(_parse_birthday(birthday), year)
            except ValueError as e:
                raise click.UsageError(str(e))
        return default_range

    manual = _resolve_manual_dates(start, end, period)
    if manual:
        return manual

    if year:
        if birthday:
            try:
                return birthday_year(_parse_birthday(birthday), year)
            except ValueError as e:
                raise click.UsageError(str(e))
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
    years_back: int | None = None,
    on_this_day_target: date | None = None,
    holiday: str | None = None,
) -> DateRange | list[DateRange]:
    """Resolve date ranges from memory type preset."""
    from immich_memories.memory_types.date_builders import build_month, build_season

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

    spanning = _multi_year_ranges(memory_type, year, years_back, on_this_day_target, holiday)
    if spanning is not None:
        return spanning

    # Types that use calendar year: year_in_review, person_spotlight, multi_person
    if not year:
        raise click.UsageError(f"--year is required with --memory-type {memory_type}")

    # --month narrows any yearly type to a single month
    if month is not None:
        return build_month(month, year)

    return calendar_year(year)


def duration_from_date_range(date_range: DateRange) -> float:
    """Scale duration by date range: 1 month = 60s, 1 year = 600s.

    Quadratic curve fitted through (1mo, 60s), (6mo, 360s), (12mo, 600s).
    Linear ~60s/month for the first half, then decelerates toward 600s.
    """
    months = max(1, (date_range.end - date_range.start).days + 1) / 30.0
    duration = (-20 * months**2 + 800 * months - 120) / 11
    return float(max(30, min(600, duration)))


# WHY these two by name: they span whole years, and duration_from_date_range's
# curve was fitted on 1-12 months -- it turns negative past ~40 months, so five
# Christmases clamped to the same 30s floor as an empty weekend (#511). Their
# presets already state the length they want, so the CLI reads that instead.
_PRESET_DURATION_TYPES = ("holiday", "then_and_now")


def _preset_duration(memory_type: str) -> float | None:
    """The registered preset's own intended length for a memory type."""
    from immich_memories.memory_types.factory import create_preset
    from immich_memories.memory_types.registry import MemoryType

    return create_preset(MemoryType(memory_type)).default_duration_seconds


def default_duration_for_type(
    memory_type: str | None, date_range: DateRange | None
) -> float | None:
    """Get default duration in seconds for a memory type.

    Date-range based types scale with span (1 month = 60s, 1 year = 600s).
    Trip dates provide an editorial estimate; discovered media later applies
    the capacity cap. Types that span several years take the length their
    preset asks for, since the span curve does not reach that far. Other fixed
    types: on_this_day (45s), person without range (120s).
    """
    if not memory_type:
        return None

    if memory_type == "on_this_day":
        return 45.0
    if memory_type in _PRESET_DURATION_TYPES:
        return _preset_duration(memory_type)
    if memory_type == "trip" and date_range is not None:
        from immich_memories.planning.auto_duration import trip_editorial_duration_seconds

        days = max(1, (date_range.end - date_range.start).days + 1)
        return trip_editorial_duration_seconds(days)
    if memory_type in ("person_spotlight", "multi_person"):
        if date_range is None:
            return 120.0
        return duration_from_date_range(date_range)

    # Everything else: scale by date range
    if date_range is not None:
        return duration_from_date_range(date_range)
    return None


def _multi_year_ranges(
    memory_type: str,
    year: int | None,
    years_back: int | None,
    on_this_day_target: date | None,
    holiday: str | None,
) -> list[DateRange] | None:
    """Ranges for the types that span several years, or None for the rest.

    Kept apart from the single-range types so the dispatcher stays flat: these
    three each need their own defaults, and inlining them pushed the caller's
    cognitive complexity past the gate.
    """
    from immich_memories.memory_types.date_builders import build_holiday, build_on_this_day

    if memory_type == "on_this_day":
        return build_on_this_day(on_this_day_target or date.today(), years_back=years_back)

    if memory_type == "holiday":
        if not holiday:
            raise click.UsageError("--holiday is required with --memory-type holiday")
        # WHY today= only when the year was defaulted: asking for Christmas in
        # August would otherwise spend one of the requested years on a window
        # that has not happened. An explicit --year is a choice.
        return build_holiday(
            holiday,
            year or date.today().year,
            years_back=years_back or 5,
            today=None if year else date.today(),
        )

    if memory_type == "then_and_now":
        # WHY two whole years rather than a window: the contrast is the point, and
        # a narrow window in a year long past is often empty.
        now_year = year or date.today().year
        then_year = now_year - (years_back or 10)
        # Most recent first, like the other multi-range types: the caller derives
        # its display span as start=ranges[-1].start, end=ranges[0].end, which
        # inverts on an ascending list.
        return [calendar_year(now_year), calendar_year(then_year)]

    return None
