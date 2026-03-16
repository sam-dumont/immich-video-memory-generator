"""Memory type preset factory and registry.

Registers built-in preset factories for each memory type.
Adding a new memory type = adding a new factory function with @register_preset.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from immich_memories.memory_types.date_builders import (
    build_month,
    build_on_this_day,
    build_season,
    build_trip,
)
from immich_memories.memory_types.presets import (
    MemoryPreset,
    PersonFilter,
    ScoringProfile,
)
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import birthday_year, calendar_year

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
    person_filter = PersonFilter()
    if person_names:
        person_filter = PersonFilter(mode="single", person_names=person_names[:1])
    return MemoryPreset(
        memory_type=MemoryType.YEAR_IN_REVIEW,
        name=f"{year} Memories",
        description=f"A look back at your best moments of {year}",
        date_ranges=[calendar_year(year)],
        person_filter=person_filter,
        scoring=ScoringProfile(),
        title_template="{year}",
        default_duration_minutes=8,
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
    person_filter = PersonFilter()
    if person_names:
        person_filter = PersonFilter(mode="single", person_names=person_names[:1])
    return MemoryPreset(
        memory_type=MemoryType.SEASON,
        name=f"{season_cap} {year}",
        description=f"{season_cap} highlights of {year}",
        date_ranges=[date_range],
        person_filter=person_filter,
        scoring=ScoringProfile(motion_weight=0.35),
        title_template="{season} {year}",
        default_duration_minutes=3,
    )


@register_preset(
    MemoryType.PERSON_SPOTLIGHT,
    name="Person Spotlight",
    description="A year focused on one person",
)
def _person_spotlight(
    year: int,
    person_names: list[str] | None = None,
    use_birthday: bool = False,
    birthday: date | None = None,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if not person_names:
        raise ValueError("person_names is required for PERSON_SPOTLIGHT memory type")
    name = person_names[0]
    date_range = birthday_year(birthday, year) if use_birthday and birthday else calendar_year(year)
    return MemoryPreset(
        memory_type=MemoryType.PERSON_SPOTLIGHT,
        name=f"Your Year with {name}",
        description=f"Best moments with {name} in {year}",
        date_ranges=[date_range],
        person_filter=PersonFilter(mode="single", person_names=[name]),
        scoring=ScoringProfile(face_weight=0.6, motion_weight=0.15),
        title_template="{year}",
        subtitle_template="Your Year with {person}",
        default_duration_minutes=2,
    )


@register_preset(
    MemoryType.MULTI_PERSON,
    name="Multi-Person",
    description="Moments featuring multiple people together",
)
def _multi_person(
    year: int,
    person_names: list[str] | None = None,
    require_co_occurrence: bool = True,
    **kwargs,  # noqa: ARG001
) -> MemoryPreset:
    if not person_names:
        raise ValueError("person_names is required for MULTI_PERSON memory type")
    joined = " & ".join(person_names)
    mode = "all_of" if require_co_occurrence else "any"
    return MemoryPreset(
        memory_type=MemoryType.MULTI_PERSON,
        name=joined,
        description=f"Moments with {joined} in {year}",
        date_ranges=[calendar_year(year)],
        person_filter=PersonFilter(
            mode=mode,
            person_names=list(person_names),
            require_co_occurrence=require_co_occurrence,
        ),
        scoring=ScoringProfile(face_weight=0.5, motion_weight=0.2),
        title_template="{year}",
        subtitle_template="{persons}",
        default_duration_minutes=5,
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
    person_filter = PersonFilter()
    if person_names:
        person_filter = PersonFilter(mode="single", person_names=person_names[:1])
    return MemoryPreset(
        memory_type=MemoryType.MONTHLY_HIGHLIGHTS,
        name=f"{month_name} {year}",
        description=f"Highlights from {month_name} {year}",
        date_ranges=[date_range],
        person_filter=person_filter,
        scoring=ScoringProfile(),
        title_template="{month} {year}",
        default_duration_minutes=1,
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
    person_filter = PersonFilter()
    if person_names:
        person_filter = PersonFilter(mode="single", person_names=person_names[:1])
    return MemoryPreset(
        memory_type=MemoryType.ON_THIS_DAY,
        name=f"{month_name} {target_date.day} Through the Years",
        description=f"Memories from {month_name} {target_date.day} across previous years",
        date_ranges=date_ranges,
        person_filter=person_filter,
        scoring=ScoringProfile(),
        title_template="{month} {day}",
        subtitle_template="Through the Years",
        default_duration_minutes=2,
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
    # Trips: 1 minute per 3 days of travel, capped at 10
    trip_days = (trip_end - trip_start).days + 1
    duration = max(1, min(10, round(trip_days / 3)))
    person_filter = PersonFilter()
    if person_names:
        person_filter = PersonFilter(mode="single", person_names=person_names[:1])

    return MemoryPreset(
        memory_type=MemoryType.TRIP,
        name=location,
        description=f"Trip to {location}",
        date_ranges=[date_range],
        person_filter=person_filter,
        scoring=ScoringProfile(
            motion_weight=0.3,
            content_weight=0.3,
            face_weight=0.2,
            stability_weight=0.1,
            audio_weight=0.0,
            duration_weight=0.1,
        ),
        title_template="{location}",
        subtitle_template="{start_date} - {end_date}",
        default_duration_minutes=duration,
    )
