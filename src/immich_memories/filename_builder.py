"""Output filename and title helpers based on memory type context."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange

# 8 hex characters: short enough to read in a filename, and with a few hundred
# renders per library the odds of two recipes colliding are negligible.
_RECIPE_HASH_CHARS = 8
_RECIPE_HASH_SUFFIX = re.compile(rf"_[0-9a-f]{{{_RECIPE_HASH_CHARS}}}$")

# Segment boundaries are floats derived from analysis. A rerun landing a few
# milliseconds apart is the same edit, so boundaries are compared at 10 ms.
_BOUNDARY_PRECISION = 2


def build_music_output_path(video_path: Path) -> Path:
    """Return a sibling music-mix path while preserving the video container."""
    return video_path.with_name(f"{video_path.stem}.with_music{video_path.suffix}")


def normalize_output_path(path: Path, container: Literal["mp4", "mov"]) -> Path:
    """Return an output path whose suffix matches the resolved container."""
    expected_suffix = f".{container}"
    if path.suffix.lower() == expected_suffix:
        return path
    return path.with_suffix(expected_suffix)


def build_memory_output_path(
    *,
    output_dir: Path,
    person_names: list[str] | tuple[str, ...],
    memory_type: str | None,
    date_range: DateRange,
    container: str,
) -> Path:
    """The CLI's default file name for a memory: who is in it, what it covers."""
    person_slug = (
        "_".join(n.lower().replace(" ", "_") for n in person_names) if person_names else "all"
    )
    type_slug = memory_type or "memories"
    if date_range.is_calendar_year:
        date_slug = str(date_range.start.year)
    else:
        date_slug = f"{date_range.start.strftime('%Y%m%d')}-{date_range.end.strftime('%Y%m%d')}"
    return output_dir / f"{person_slug}_{type_slug}_{date_slug}.{container}"


def build_output_filename(
    memory_type: str | None,
    preset_params: dict,
    person_name: str | None,
    date_start: date | None,
    date_end: date | None,
    container: Literal["mp4", "mov"] = "mp4",
) -> str:
    """Build a human-readable output filename from memory context.

    Uses memory type, person names, and date range to produce filenames like:
    - sam_noah_march_2026_memories.mp4 (multi-person, single month)
    - noah_summer_2025_memories.mp4 (season preset)
    - sam_2025_memories.mp4 (year in review)

    Args:
        memory_type: Memory type key (e.g. "year_in_review", "multi_person", "custom", None)
        preset_params: Memory preset parameters dict (person_names, year, month, season, etc.)
        person_name: Single selected person name (from state.selected_person), or None
        date_start: Start date of the date range
        date_end: End date of the date range

    Returns:
        Filename string ending in the resolved container suffix.
    """
    who = _build_who_part(memory_type, preset_params, person_name)
    when = _build_when_part(memory_type, preset_params, date_start, date_end)

    parts = [p for p in (who, when) if p]
    slug = "_".join(parts) if parts else "memories"

    return f"{slug}_memories.{container}"


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


def recipe_hash(
    *,
    memory_type: str | None,
    date_start: date | None,
    date_end: date | None,
    target_duration: float,
    clips: Sequence[tuple[str, float, float]],
    extras: Mapping[str, object] | None = None,
) -> str:
    """Fingerprint the inputs that decide what a render contains.

    Two runs that would cut the same clips, in the same order, over the same
    range hash the same and so write the same file -- the newer render replaces
    the older instead of piling up beside it. Change the selection, the order,
    the duration or the memory type and the hash changes with it.

    This is a fingerprint of the *recipe*, not of the output. Music generation is
    unseeded, so two renders sharing a hash are the same edit but not the same
    bytes; the newer one wins deliberately.
    """
    payload = {
        "memory_type": memory_type or "",
        "date_start": date_start.isoformat() if date_start else "",
        "date_end": date_end.isoformat() if date_end else "",
        "target_duration": round(target_duration, _BOUNDARY_PRECISION),
        "clips": [
            [
                asset_id,
                round(start, _BOUNDARY_PRECISION),
                round(end, _BOUNDARY_PRECISION),
            ]
            for asset_id, start, end in clips
        ],
        "extras": {k: str(v) for k, v in sorted((extras or {}).items())},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:_RECIPE_HASH_CHARS]


def apply_recipe_hash(path: Path, digest: str) -> Path:
    """Return the path carrying this recipe hash, replacing any it already has."""
    stem = _RECIPE_HASH_SUFFIX.sub("", path.stem)
    return path.with_name(f"{stem}_{digest}{path.suffix}")
