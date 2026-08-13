"""Pure planning for the final content and title-screen timeline."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

_TITLE_SHARE = 0.20
_MIN_ENDING_SECONDS = 2.0
_LOCATION_CHANGE_KM = 30.0


@dataclass(frozen=True, slots=True)
class TimelinePlan:
    """Durations and divider limit for one final playable timeline."""

    target_duration: float
    content_budget: float
    title_budget: float
    title_duration: float
    ending_duration: float
    divider_duration: float
    max_dividers: int


def _asset_for(item: Any) -> Any:
    nested = getattr(item, "clip", None)
    if nested is not None:
        item = nested
    asset = getattr(item, "asset", None)
    if asset is not None:
        return asset
    return item if hasattr(item, "file_created_at") else None


def _item_date(item: Any) -> date | None:
    value = getattr(item, "date", None)
    asset = _asset_for(item)
    if value is None and asset is not None:
        value = getattr(asset, "file_created_at", None)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _item_location(item: Any) -> tuple[float, float, str | None] | None:
    latitude = getattr(item, "latitude", None)
    longitude = getattr(item, "longitude", None)
    name = getattr(item, "location_name", None)
    asset = _asset_for(item)
    exif = getattr(asset, "exif_info", None) if asset is not None else None
    if exif is not None:
        latitude = latitude if latitude is not None else getattr(exif, "latitude", None)
        longitude = longitude if longitude is not None else getattr(exif, "longitude", None)
        name = name or getattr(exif, "city", None)
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude), name


def _month_divider_count(clips: list[Any], title_settings: Any) -> int:
    month_keys = [
        (clip_date.year, clip_date.month)
        for clip in clips
        if (clip_date := _item_date(clip)) is not None
    ]
    threshold = max(1, int(getattr(title_settings, "month_divider_threshold", 1)))
    counts = Counter(month_keys)
    eligible = list(dict.fromkeys(key for key in month_keys if counts[key] >= threshold))
    return max(0, len(eligible) - 1)


def _year_divider_count(clips: list[Any]) -> int:
    years = list(
        dict.fromkeys(
            clip_date.year for clip in clips if (clip_date := _item_date(clip)) is not None
        )
    )
    return max(0, len(years) - 1)


def _location_divider_count(clips: list[Any]) -> int:
    from immich_memories.analysis.trip_detection import haversine_km

    count = 0
    previous: tuple[float, float] | None = None
    for clip in clips:
        location = _item_location(clip)
        if location is None:
            continue
        latitude, longitude, name = location
        if previous is not None:
            distance = haversine_km(previous[0], previous[1], latitude, longitude)
            if distance > _LOCATION_CHANGE_KM and name:
                count += 1
        previous = latitude, longitude
    return count


def _eligible_dividers(clips: list[Any], title_settings: Any, memory_type: str | None) -> int:
    if memory_type == "trip" and getattr(title_settings, "show_location_cards", True):
        return _location_divider_count(clips)
    divider_mode = getattr(title_settings, "divider_mode", "month")
    if divider_mode == "year":
        return _year_divider_count(clips)
    if divider_mode == "month" and getattr(title_settings, "show_month_dividers", True):
        return _month_divider_count(clips, title_settings)
    return 0


def plan_timeline(
    clips: list[Any],
    title_settings: Any | None,
    target_duration: float,
    memory_type: str | None,
) -> TimelinePlan:
    """Fit title screens inside 20% of the requested final duration."""
    target = max(0.0, target_duration)
    if title_settings is None or not getattr(title_settings, "enabled", True):
        return TimelinePlan(target, target, 0.0, 0.0, 0.0, 0.0, 0)

    remaining = target * _TITLE_SHARE
    title_duration = min(max(0.0, float(title_settings.title_duration)), remaining)
    remaining -= title_duration

    configured_ending = max(0.0, float(title_settings.ending_duration))
    ending_duration = min(configured_ending, remaining) if remaining >= _MIN_ENDING_SECONDS else 0.0
    remaining -= ending_duration

    divider_duration = max(0.0, float(title_settings.month_divider_duration))
    eligible_dividers = _eligible_dividers(clips, title_settings, memory_type)
    max_dividers = (
        min(eligible_dividers, int(remaining // divider_duration)) if divider_duration > 0.0 else 0
    )
    title_budget = title_duration + ending_duration + max_dividers * divider_duration
    return TimelinePlan(
        target_duration=target,
        content_budget=target - title_budget,
        title_budget=title_budget,
        title_duration=title_duration,
        ending_duration=ending_duration,
        divider_duration=divider_duration,
        max_dividers=max_dividers,
    )
