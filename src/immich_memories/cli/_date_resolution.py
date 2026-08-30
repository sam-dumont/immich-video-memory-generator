"""Date range resolution and duration scaling for CLI commands."""

from __future__ import annotations

import contextlib
from calendar import monthrange
from datetime import date, datetime

import click

from immich_memories.timeperiod import (
    DateRange,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)

# A leap year, so a 29 February birthday survives being given a filler one.
_BIRTHDAY_FILLER_YEAR = 2000

# How a birthday read out of Immich is written back into the --birthday value.
# Month first and dash-separated is the one short form _parse_birthday reads
# with no ambiguity, so auto-detection cannot disagree with a typed birthday.
BIRTHDAY_FLAG_FORMAT = "%m-%d"


def _parse_birthday(value: str) -> date:
    """Read --birthday as RFC 3339 order: MM-DD, or a full YYYY-MM-DD.

    This flag once read slashes month-first while every other flag read them
    day-first, so 07/02 meant July for a 7 February birthday. Slash dialects
    are gone everywhere (#748): big-endian or rejected, never guessed.
    """
    with contextlib.suppress(ValueError):
        return parse_date(value)

    with contextlib.suppress(ValueError):
        return datetime.strptime(f"{_BIRTHDAY_FILLER_YEAR}-{value}", "%Y-%m-%d").date()

    raise ValueError(
        f"Cannot parse birthday: '{value}'. Dates are RFC 3339 order: "
        "MM-DD (for example 02-07), or a full date YYYY-MM-DD."
    )


def _birthday_windows(
    birthday: str,
    year: int | None,
    years_back: int | None,
) -> list[DateRange]:
    """The CLI's --birthday, answered by the builder the wizard's card uses.

    One builder, so a birthday memory is the same memory whichever way it was
    asked for (#659, #724). The parse stays here because only the CLI has a
    string to read: the wizard picks a date.
    """
    from immich_memories.memory_types.date_builders import build_birthday_windows

    try:
        anchor = _parse_birthday(birthday)
    except ValueError as e:
        raise click.UsageError(str(e))
    return build_birthday_windows(anchor, year, years_back)


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
    preset_params: dict | None = None,
) -> DateRange | list[DateRange]:
    """Resolve date range from command line options.

    When --memory-type is set, delegates to preset date builders.
    --start/--end can override the preset's default date range.
    Otherwise falls through to manual date range options.

    ``preset_params`` carries what a memory type cannot spell as date flags: a
    special day's window comes out of the catalogue, not off the command line.
    """
    if memory_type:
        # Ahead of the other types because a birthday memory is the one that
        # does not need --year: with none given the builder celebrates the most
        # recent birthday, which is what "make Emma's birthday video" means.
        if memory_type == "person_spotlight" and birthday:
            return _resolve_manual_dates(start, end, period) or _birthday_windows(
                birthday, year, years_back
            )
        default_range = _resolve_memory_type_dates(
            memory_type,
            year,
            season,
            month,
            hemisphere,
            years_back,
            on_this_day_target,
            holiday,
            preset_params,
        )
        manual_range = _resolve_manual_dates(start, end, period)
        if manual_range:
            return manual_range
        return default_range

    manual = _resolve_manual_dates(start, end, period)
    if manual:
        return manual

    if year:
        if birthday:
            return _birthday_windows(birthday, year, years_back)
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
    preset_params: dict | None = None,
) -> DateRange | list[DateRange]:
    """Resolve date ranges from memory type preset."""
    from immich_memories.memory_types.date_builders import build_month, build_season

    discovered = _discovered_scope(memory_type, preset_params or {})
    if discovered is not None:
        return discovered

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


def _holiday_ranges(holiday: str, year: int | None, years_back: int | None) -> list[DateRange]:
    """One window per year around a holiday, as the registered preset builds them."""
    from immich_memories.memory_types.factory import create_preset
    from immich_memories.memory_types.registry import MemoryType

    preset = create_preset(
        MemoryType.HOLIDAY, holiday=holiday, year=year, years_back=years_back or 5
    )
    return preset.date_ranges


def _discovered_scope(memory_type: str, preset_params: dict) -> DateRange | None:
    """The window a memory type discovers rather than reads off the date flags.

    Two types find their own scope: the catalogue records a special day's, and
    GPS detection finds a trip's. None means the date flags still decide.
    """
    if memory_type == "special_day":
        return _special_day_scope(preset_params)
    if memory_type == "trip":
        return _trip_scope(preset_params)
    return None


def _trip_scope(preset_params: dict) -> DateRange | None:
    """The detected trip's own span, or None while no trip has been picked yet.

    ``--year`` scopes trip *detection*, not the memory: the window a trip memory
    actually covers is the trip, and it only exists once detection has run and
    one has been selected. Until then the year is the honest answer, which is
    what the caller falls through to.
    """
    from immich_memories.memory_types.date_builders import build_trip

    start, end = preset_params.get("trip_start"), preset_params.get("trip_end")
    if start is None or end is None:
        return None
    return build_trip(start, end)


def _special_day_scope(preset_params: dict) -> DateRange:
    """The window the catalogue recorded for one day, or the calendar day."""
    from immich_memories.memory_types.date_builders import build_special_day

    day = preset_params.get("day")
    if day is None:
        raise click.UsageError(
            "--day is required with --memory-type special_day. Run "
            "`immich-memories days-due` to see which days the catalogue holds."
        )
    return build_special_day(day, preset_params.get("window"))


def duration_from_date_range(date_range: DateRange) -> float:
    """Scale duration by date range: 1 month = 60s, 1 year = 600s.

    Quadratic curve fitted through (1mo, 60s), (6mo, 360s), (12mo, 600s).
    Linear ~60s/month for the first half, then decelerates toward 600s. Kept
    for products such as Season whose CLI contract explicitly uses this curve.
    """
    months = max(1, (date_range.end - date_range.start).days + 1) / 30.0
    duration = (-20 * months**2 + 800 * months - 120) / 11
    return float(max(30, min(600, duration)))


def _recap_duration_from_date_range(date_range: DateRange) -> float:
    """Give recap/person scopes one minute per month, capped at ten minutes."""
    start = date_range.start.date()
    end = date_range.end.date()
    complete_calendar_months = start.day == 1 and end.day == monthrange(end.year, end.month)[1]
    months = (
        (end.year - start.year) * 12 + end.month - start.month + 1
        if complete_calendar_months
        else ((date_range.end - date_range.start).days + 1) / 30.0
    )
    return float(max(30, min(600, months * 60)))


# WHY these by name: duration_from_date_range's curve was fitted on 1-12
# months and is wrong at both ends. Past ~40 months it turns negative, so five
# Christmases clamped to the same 30s floor as an empty weekend (#511); at a
# one-day span it evaluates negative too, so a special day would render as 30
# seconds however much happened on it. Their presets already state the length
# they want, so the CLI reads that instead.
_PRESET_DURATION_TYPES = ("holiday", "then_and_now", "special_day")


def _preset_duration(memory_type: str, preset_params: dict | None = None) -> float | None:
    """The registered preset's own intended length for a memory type."""
    from immich_memories.memory_types.factory import create_preset
    from immich_memories.memory_types.registry import MemoryType

    preset = create_preset(MemoryType(memory_type), **(preset_params or {}))
    return preset.default_duration_seconds


def default_duration_for_type(
    memory_type: str | None,
    date_range: DateRange | None,
    preset_params: dict | None = None,
    primary_window: DateRange | None = None,
) -> float | None:
    """Get default duration in seconds for a memory type.

    Recap and person types scale at 1 minute per month up to 10 minutes.
    Season retains its documented date-range curve. Trip dates provide an
    editorial estimate; discovered media later applies the capacity cap.
    Types the span curve cannot reach -- several years at one end, a single day
    at the other -- take the length their preset asks for.
    Other fixed types: on_this_day (45s), person without range (120s).

    ``preset_params`` is forwarded to the preset factory for the types whose
    length depends on more than the dates: a special day needs the day it
    happened on and how long it stayed awake.

    ``primary_window`` is the window a memory is actually made of, when that is
    narrower than the span it displays. A birthday memory shows decades but is
    made from a rolling year (#511, #719).
    """
    if not memory_type:
        return None

    if memory_type == "on_this_day":
        return 45.0
    if memory_type == "monthly_highlights":
        return 60.0
    if memory_type in _PRESET_DURATION_TYPES:
        return _preset_duration(memory_type, preset_params)
    if memory_type == "trip" and date_range is not None:
        from immich_memories.planning.auto_duration import trip_editorial_duration_seconds

        days = max(1, (date_range.end - date_range.start).days + 1)
        return trip_editorial_duration_seconds(days)
    if memory_type in ("person_spotlight", "multi_person"):
        if date_range is None:
            return 120.0
        return _recap_duration_from_date_range(primary_window or date_range)
    if memory_type == "year_in_review" and date_range is not None:
        return _recap_duration_from_date_range(date_range)

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
    from immich_memories.memory_types.date_builders import (
        build_on_this_day,
        build_then_and_now,
    )

    if memory_type == "on_this_day":
        return build_on_this_day(on_this_day_target or date.today(), years_back=years_back)

    if memory_type == "holiday":
        if not holiday:
            raise click.UsageError("--holiday is required with --memory-type holiday")
        # Through the preset, not build_holiday: the rule that a defaulted year
        # must skip a holiday that has not happened yet belongs to whoever
        # defaults the year, and the wizard defaults it there too.
        return _holiday_ranges(holiday, year, years_back)

    if memory_type == "then_and_now":
        # WHY the `or 10`: --years-back defaults to None here, and 0 is read as
        # unset rather than as an error. The builder owns everything else.
        return build_then_and_now(year or date.today().year, years_back or 10)

    return None
