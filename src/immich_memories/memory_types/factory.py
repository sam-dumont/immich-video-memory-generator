"""Memory type preset factory and registry.

Registers built-in preset factories for each memory type.
Adding a new memory type = adding a new factory function with @register_preset.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from immich_memories.memory_types.date_builders import (
    KNOWN_HOLIDAYS,
    build_birthday_windows,
    build_holiday,
    build_month,
    build_on_this_day,
    build_season,
    build_special_day,
    build_then_and_now,
    build_trip,
    resolve_holiday,
)
from immich_memories.memory_types.presets import (
    MemoryPreset,
    PersonFilter,
    person_filter_for,
)
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import calendar_year

# A birthday memory is a year plus a stack of flashbacks, and #703's use case
# asks for five to ten minutes of it. Ten is what the CLI's span curve already
# returns for a year with one person, so both surfaces land on the same number.
BIRTHDAY_MEMORY_SECONDS = 600.0

# Registry: maps MemoryType -> factory callable
_REGISTRY: dict[MemoryType, Callable[..., MemoryPreset]] = {}
_DESCRIPTIONS: dict[MemoryType, tuple[str, str]] = {}


def register_preset(
    memory_type: MemoryType,
    name: str,
    description: str,
) -> Callable:
    """Decorator to register a preset factory for a memory type."""

    def decorator(func: Callable[..., MemoryPreset]) -> Callable[..., MemoryPreset]:
        _REGISTRY[memory_type] = func
        _DESCRIPTIONS[memory_type] = (name, description)
        return func

    return decorator


def create_preset(memory_type: MemoryType, **kwargs) -> MemoryPreset:
    """Create a memory preset from a registered factory.

    Args:
        memory_type: The memory type to create.
        **kwargs: Arguments forwarded to the factory function.

    Returns:
        Configured MemoryPreset.

    Raises:
        ValueError: If no factory is registered for the memory type.
    """
    factory = _REGISTRY.get(memory_type)
    if factory is None:
        raise ValueError(
            f"No preset factory registered for '{memory_type}'. "
            f"Available: {', '.join(str(t) for t in _REGISTRY)}"
        )
    return factory(**kwargs)


def list_memory_types() -> list[dict[str, str]]:
    """List all registered memory types with metadata.

    Returns:
        List of dicts with 'type', 'name', 'description' keys.
    """
    return [
        {"type": str(mt), "name": name, "description": desc}
        for mt, (name, desc) in _DESCRIPTIONS.items()
    ]


# ─── Built-in preset factories ────────────────────────────────────────────────


@register_preset(
    MemoryType.YEAR_IN_REVIEW,
    name="Year in Review",
    description="Best moments from a full calendar year",
)
def _year_in_review(
    year: int,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    return MemoryPreset(
        memory_type=MemoryType.YEAR_IN_REVIEW,
        name=f"{year} Memories",
        description=f"A look back at your best moments of {year}",
        date_ranges=[calendar_year(year)],
        person_filter=person_filter_for(person_names),
        default_duration_seconds=600,  # ~50s per month × 12
    )


@register_preset(
    MemoryType.SEASON,
    name="Season",
    description="Highlights from a specific season",
)
def _season(
    year: int,
    season: str | None = None,
    hemisphere: str = "north",
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if season is None:
        raise ValueError("season is required for SEASON memory type")
    date_range = build_season(season, year, hemisphere)
    season_cap = season.capitalize()
    return MemoryPreset(
        memory_type=MemoryType.SEASON,
        name=f"{season_cap} {year}",
        description=f"{season_cap} highlights of {year}",
        date_ranges=[date_range],
        person_filter=person_filter_for(person_names),
        default_duration_seconds=135,  # ~45s per month × 3
    )


@register_preset(
    MemoryType.PERSON_SPOTLIGHT,
    name="Person Spotlight",
    description="A year focused on one person",
)
def _person_spotlight(
    year: int | None = None,
    person_names: list[str] | None = None,
    use_birthday: bool = False,
    birthday: date | None = None,
    years_back: int | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    """A year with one person — a calendar one, or the one between two birthdays.

    Anchored on a birthday it is a different memory: the rolling year ending on
    the party, plus that birthday in every earlier year the library reaches.
    Long enough to be a story rather than a montage, which is why it asks for
    ten minutes where the calendar year asks for two.
    """
    if not person_names:
        raise ValueError("person_names is required for PERSON_SPOTLIGHT memory type")
    name = person_names[0]
    if use_birthday and birthday:
        return MemoryPreset(
            memory_type=MemoryType.PERSON_SPOTLIGHT,
            name=f"{name}'s Year",
            description=f"A year with {name}, birthday to birthday",
            date_ranges=build_birthday_windows(birthday, year, years_back),
            person_filter=person_filter_for([name]),
            default_duration_seconds=BIRTHDAY_MEMORY_SECONDS,
        )
    if year is None:
        raise ValueError("year is required for a PERSON_SPOTLIGHT that is not birthday-anchored")
    return MemoryPreset(
        memory_type=MemoryType.PERSON_SPOTLIGHT,
        name=f"Your Year with {name}",
        description=f"Best moments with {name} in {year}",
        date_ranges=[calendar_year(year)],
        # A spotlight is one person by definition, so extra names name a
        # different memory -- Multi-Person -- rather than narrowing this one.
        person_filter=person_filter_for([name]),
        default_duration_seconds=600,
    )


@register_preset(
    MemoryType.MULTI_PERSON,
    name="Multi-Person",
    description="Moments featuring multiple people together",
)
def _multi_person(
    year: int,
    person_names: list[str] | None = None,
    person_match: str = "and",
    require_co_occurrence: bool | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if not person_names:
        raise ValueError("person_names is required for MULTI_PERSON memory type")
    if require_co_occurrence is not None:
        person_match = "and" if require_co_occurrence else "or"
    person_filter = person_filter_for(person_names, person_match=person_match)
    joined = (" & " if person_match == "and" else " or ").join(person_names)
    return MemoryPreset(
        memory_type=MemoryType.MULTI_PERSON,
        name=joined,
        description=f"Moments with {joined} in {year}",
        date_ranges=[calendar_year(year)],
        person_filter=person_filter,
        default_duration_seconds=600,
    )


@register_preset(
    MemoryType.MONTHLY_HIGHLIGHTS,
    name="Monthly Highlights",
    description="Best moments from a single month",
)
def _monthly_highlights(
    year: int,
    month: int | None = None,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if month is None:
        raise ValueError("month is required for MONTHLY_HIGHLIGHTS memory type")
    import calendar as cal

    month_name = cal.month_name[month]
    date_range = build_month(month, year)
    return MemoryPreset(
        memory_type=MemoryType.MONTHLY_HIGHLIGHTS,
        name=f"{month_name} {year}",
        description=f"Highlights from {month_name} {year}",
        date_ranges=[date_range],
        person_filter=person_filter_for(person_names),
        default_duration_seconds=60,
    )


@register_preset(
    MemoryType.ON_THIS_DAY,
    name="On This Day",
    description="Memories from this date across previous years",
)
def _on_this_day(
    target_date: date | None = None,
    years_back: int = 5,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if target_date is None:
        target_date = date.today()
    date_ranges = build_on_this_day(target_date, years_back)
    import calendar

    month_name = calendar.month_name[target_date.month]
    return MemoryPreset(
        memory_type=MemoryType.ON_THIS_DAY,
        name=f"{month_name} {target_date.day} Through the Years",
        description=f"Memories from {month_name} {target_date.day} across previous years",
        date_ranges=date_ranges,
        person_filter=person_filter_for(person_names),
        default_duration_seconds=45,  # ~30-45s — it's a single date across years
    )


@register_preset(
    MemoryType.TRIP,
    name="Trip",
    description="Automatic trip detection from GPS data",
)
def _trip(
    year: int,  # noqa: ARG001
    trip_start: date | None = None,
    trip_end: date | None = None,
    location_name: str | None = None,
    asset_count: int = 10,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if trip_start is None or trip_end is None:
        raise ValueError("trip_start and trip_end are required for TRIP memory type")

    location = location_name or "Unknown Location"
    date_range = build_trip(trip_start, trip_end)
    # Start with a modest editorial curve. Auto mode adjusts this downward
    # after discovery when the selected media cannot support the full story.
    trip_days = (trip_end - trip_start).days + 1
    from immich_memories.planning.auto_duration import trip_editorial_duration_seconds

    duration = trip_editorial_duration_seconds(trip_days)

    return MemoryPreset(
        memory_type=MemoryType.TRIP,
        name=location,
        description=f"Trip to {location}",
        date_ranges=[date_range],
        person_filter=person_filter_for(person_names),
        default_duration_seconds=duration,
    )


_HOLIDAY_LABELS = {
    "new_year": "New Year",
    "valentines": "Valentine's Day",
    "easter": "Easter",
    "mothers_day": "Mother's Day",
    "fathers_day": "Father's Day",
    "halloween": "Halloween",
    "thanksgiving": "Thanksgiving",
    "christmas_eve": "Christmas Eve",
    "christmas": "Christmas",
    "new_years_eve": "New Year's Eve",
}


def holiday_label(holiday: str, year: int) -> str:
    """A printable name, falling back to the date for a household's own occasion."""
    key = holiday.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _HOLIDAY_LABELS:
        return _HOLIDAY_LABELS[key]
    resolved = resolve_holiday(holiday, year)
    return resolved.strftime("%-d %B")


def holiday_choices() -> dict[str, str]:
    """Every holiday the pipeline resolves, with a printable name.

    Keyed off KNOWN_HOLIDAYS so adding one there reaches the picker without a
    second list to keep in step.
    """
    return {key: holiday_label(key, date.today().year) for key in KNOWN_HOLIDAYS}


@register_preset(
    MemoryType.HOLIDAY,
    name="Holiday",
    description="The same holiday, across the years",
)
def _holiday(
    holiday: str = "christmas",
    year: int | None = None,
    years_back: int = 5,
    window_days: int = 2,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    """A holiday is the date a library reliably has every year, so it spans them."""
    # WHY today= only when the year was defaulted: asking for Christmas in
    # August would otherwise spend one of the requested years on a window that
    # has not happened. A year the caller named is a choice and is left alone.
    # Both surfaces default the year here, so both get the guard from here.
    today = None if year else date.today()
    year = year or date.today().year
    label = holiday_label(holiday, year)

    return MemoryPreset(
        memory_type=MemoryType.HOLIDAY,
        name=f"{label} Through the Years",
        description=f"{label} across {years_back} years",
        date_ranges=build_holiday(holiday, year, years_back, window_days, today=today),
        person_filter=person_filter_for(person_names),
        default_duration_seconds=60,
    )


@register_preset(
    MemoryType.THEN_AND_NOW,
    name="Then and Now",
    description="An early year beside the present one",
)
def _then_and_now(
    year: int | None = None,
    years_back: int = 10,
    person_names: list[str] | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    """Two windows, far apart on purpose.

    The contrast is the whole point, so the gap is required: a then-and-now with
    no distance between the two is just a now.
    """
    year = year or date.today().year
    then_year = year - years_back

    return MemoryPreset(
        memory_type=MemoryType.THEN_AND_NOW,
        name=f"{then_year} and {year}",
        description=f"{then_year} beside {year}",
        date_ranges=build_then_and_now(year, years_back),
        person_filter=person_filter_for(person_names),
        default_duration_seconds=45,
    )


@register_preset(
    MemoryType.SPECIAL_DAY,
    name="Special Day",
    description="One day the library says something happened on",
)
def _special_day(
    day: date | None = None,
    window: tuple[datetime, datetime] | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    what: str | None = None,
    active_hours: float = 0.0,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    """One occasion, named by the catalogue that found it.

    Refuse over fake: a day the model could not name is a day that should not
    be rendered, so there is no "Memories from 12 June 2016" fallback here.
    """
    if day is None:
        raise ValueError("day is required for SPECIAL_DAY memory type")

    name = (title or "").strip() or (what or "").strip()
    if not name:
        raise ValueError(
            f"The catalogue entry for {day.isoformat()} has neither a title nor a "
            "'what', so there is nothing truthful to call the memory."
        )

    from immich_memories.planning.auto_duration import special_day_editorial_duration_seconds

    hours = (window[1] - window[0]).total_seconds() / 3600.0 if window else active_hours

    return MemoryPreset(
        memory_type=MemoryType.SPECIAL_DAY,
        name=name,
        description=(subtitle or "").strip() or name,
        date_ranges=[build_special_day(day, window)],
        # Not person-filtered on purpose: the memory is the occasion, and real
        # names would reach durable run history through it.
        person_filter=PersonFilter(),
        default_duration_seconds=special_day_editorial_duration_seconds(hours),
    )
