"""Output filename and title helpers based on memory type context."""

from __future__ import annotations

import calendar
from datetime import date


def build_output_filename(
    memory_type: str | None,
    preset_params: dict,
    person_name: str | None,
    date_start: date | None,
    date_end: date | None,
) -> str:
    """Build a human-readable output filename from memory context.

    Uses memory type, person names, and date range to produce filenames like:
    - sam_emile_march_2026_memories.mp4 (multi-person, single month)
    - emile_summer_2025_memories.mp4 (season preset)
    - sam_2025_memories.mp4 (year in review)

    Args:
        memory_type: Memory type key (e.g. "year_in_review", "multi_person", "custom", None)
        preset_params: Memory preset parameters dict (person_names, year, month, season, etc.)
        person_name: Single selected person name (from state.selected_person), or None
        date_start: Start date of the date range
        date_end: End date of the date range

    Returns:
        Filename string ending in .mp4
    """
    who = _build_who_part(memory_type, preset_params, person_name)
    when = _build_when_part(memory_type, preset_params, date_start, date_end)

    parts = [p for p in (who, when) if p]
    slug = "_".join(parts) if parts else "memories"

    return f"{slug}_memories.mp4"


def build_title_person_name(
    memory_type: str | None,
    preset_params: dict,
    person_name: str | None,
    use_first_name_only: bool = True,
) -> str | None:
    """Build person name for title screens, handling multi-person presets.

    Args:
        memory_type: Memory type key or None.
        preset_params: Memory preset parameters dict.
        person_name: Single selected person name, or None.
        use_first_name_only: Whether to truncate to first name.

    Returns:
        Formatted person name string, or None.
    """
    # Multi-person: join names from preset params
    preset_names = preset_params.get("person_names", [])
    if memory_type == "multi_person" and len(preset_names) >= 2:
        names = preset_names
        if use_first_name_only:
            names = [n.split()[0] for n in names]
        if len(names) == 2:
            return f"{names[0]} & {names[1]}"
        return f"{', '.join(names[:-1])} & {names[-1]}"

    # Single person from preset or state
    if preset_names:
        name = preset_names[0]
    elif person_name:
        name = person_name
    else:
        return None

    if use_first_name_only:
        return name.split()[0]
    return name


def should_show_month_dividers(
    memory_type: str | None,
    date_start: date | None,
    date_end: date | None,
) -> bool:
    """Decide whether to show month dividers based on memory type context.

    Rules:
    - Single month range: no dividers (nothing to divide)
    - Short ranges (<=3 months): no dividers (too choppy)
    - Monthly highlights / On This Day: no dividers
    - Everything else: respect config setting

    Args:
        memory_type: Memory type key or None.
        date_start: Start date of the range.
        date_end: End date of the range.

    Returns:
        True if month dividers should be shown.
    """
    # Memory type overrides
    if memory_type == "monthly_highlights":
        return False
    if memory_type == "on_this_day":
        return False

    if not date_start or not date_end:
        return True

    # Count distinct months in range
    month_span = (date_end.year - date_start.year) * 12 + (date_end.month - date_start.month) + 1

    # Single month or very short range: skip dividers
    return month_span > 3


def get_divider_mode(
    memory_type: str | None,
    date_start: date | None,
    date_end: date | None,
) -> str:
    """Decide which divider style to use: "none", "month", or "year".

    Rules:
    - Monthly highlights / trip: no dividers
    - On This Day: always year dividers (same date across years)
    - Short ranges (<=3 months): no dividers
    - Multi-year ranges: year dividers
    - Everything else (4+ months, single year): month dividers

    Args:
        memory_type: Memory type key or None.
        date_start: Start date of the range.
        date_end: End date of the range.

    Returns:
        "none", "month", or "year".
    """
    # Types that never get dividers
    if memory_type in ("monthly_highlights", "trip"):
        return "none"

    # On This Day always uses year dividers
    if memory_type == "on_this_day":
        return "year"

    if not date_start or not date_end:
        return "month"

    month_span = (date_end.year - date_start.year) * 12 + (date_end.month - date_start.month) + 1

    # Short range: no dividers
    if month_span <= 3:
        return "none"

    # Multi-year range: year dividers
    if date_start.year != date_end.year:
        return "year"

    # Single year, 4+ months: month dividers
    return "month"


def _build_who_part(
    memory_type: str | None,
    preset_params: dict,
    person_name: str | None,
) -> str:
    """Build the 'who' portion of the filename."""
    # Multi-person: join names from preset params
    if memory_type == "multi_person":
        names = preset_params.get("person_names", [])
        if names:
            if len(names) <= 3:
                return "_".join(n.lower() for n in names)
            return "_".join(n.lower() for n in names[:3]) + "_and_others"

    # Trip: use "trip" as the who part
    if memory_type == "trip":
        return "trip"

    # Single person from preset params or state
    preset_names = preset_params.get("person_names", [])
    if preset_names:
        return preset_names[0].lower()
    if person_name:
        return person_name.lower()

    return "everyone"


def _when_trip(preset_params: dict, date_start: date | None, date_end: date | None) -> str:
    """Build 'when' part for trip memory type."""
    import re

    location = preset_params.get("location_name")
    if location:
        return re.sub(r"[^a-z0-9]+", "_", location.lower()).strip("_")
    if date_start and date_end:
        return _date_range_slug(date_start, date_end)
    return str(preset_params.get("year", ""))


def _when_season(preset_params: dict) -> str:
    """Build 'when' part for season memory type."""
    season = preset_params.get("season", "")
    year = preset_params.get("year", "")
    return f"{season}_{year}" if season and year else str(year or "")


def _when_monthly(preset_params: dict) -> str:
    """Build 'when' part for monthly highlights memory type."""
    month = preset_params.get("month")
    year = preset_params.get("year", "")
    if month:
        month_name = calendar.month_name[month].lower()
        return f"{month_name}_{year}" if year else month_name
    return str(year or "")


def _build_when_part(
    memory_type: str | None,
    preset_params: dict,
    date_start: date | None,
    date_end: date | None,
) -> str:
    """Build the 'when' portion of the filename."""
    if memory_type == "trip":
        return _when_trip(preset_params, date_start, date_end)

    if memory_type == "season":
        return _when_season(preset_params)

    if memory_type == "monthly_highlights":
        return _when_monthly(preset_params)

    # On This Day: month + day (no year, it spans years)
    if memory_type == "on_this_day" and date_start:
        month_name = calendar.month_name[date_start.month].lower()
        return f"{month_name}_{date_start.day}"

    # Prefer date range when available (gives month-level detail)
    if date_start and date_end:
        return _date_range_slug(date_start, date_end)

    # Fallback to year param from preset
    year = preset_params.get("year")
    if year:
        return str(year)

    return ""


def _date_range_slug(start: date, end: date) -> str:
    """Build a readable slug from a date range."""
    # Full calendar year
    if (
        start.month == start.day == 1
        and end.month == 12
        and end.day == 31
        and start.year == end.year
    ):
        return str(start.year)

    # Same month
    if start.year == end.year and start.month == end.month:
        month_name = calendar.month_name[start.month].lower()
        return f"{month_name}_{start.year}"

    # Same year, different months: jan-apr_2026
    if start.year == end.year:
        start_abbr = calendar.month_abbr[start.month].lower()
        end_abbr = calendar.month_abbr[end.month].lower()
        return f"{start_abbr}-{end_abbr}_{start.year}"

    # Different years
    return f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
