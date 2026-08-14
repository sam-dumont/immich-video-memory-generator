"""Pure planning for the final content and title-screen timeline."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

_TITLE_SHARE = 0.20
_MIN_ENDING_SECONDS = 2.0
_LOCATION_CHANGE_KM = 30.0

DividerPolicy = Literal["pending", "all", "none", "capped"]


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
    divider_policy: DividerPolicy = "capped"
    eligible_dividers: int = 0
    soft_max_duration: float | None = None
    transition_budget: float = 0.0


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


def _is_chronological_month_mode(title_settings: Any, memory_type: str | None) -> bool:
    return (
        memory_type != "trip"
        and getattr(title_settings, "divider_mode", "month") == "month"
        and getattr(title_settings, "show_month_dividers", True)
    )


def _transition_ratio(transition_mode: Any) -> float:
    mode = str(getattr(transition_mode, "value", transition_mode)).lower()
    if mode == "crossfade":
        return 1.0
    if mode == "smart":
        return 0.70
    return 0.0


def _transition_overlap_budget(
    base_content_budget: float,
    *,
    title_card_count: int,
    expected_clip_duration: float,
    expected_content_clips: int | None,
    transition_mode: Any,
    transition_duration: float,
) -> float:
    """Estimate time removed by overlapping transitions.

    SMART uses the same 70% fade probability as ``_pick_transition``.  When the
    selected clip count is not known yet, iterate because adding overlap time
    can itself require another content clip (and therefore another boundary).
    """
    ratio = _transition_ratio(transition_mode)
    duration = max(0.0, transition_duration)
    if 0.0 in (ratio, duration):
        return 0.0

    if expected_content_clips is not None:
        boundaries = max(0, int(expected_content_clips) + title_card_count - 1)
        return boundaries * duration * ratio

    average = max(0.1, expected_clip_duration)
    overlap = 0.0
    for _ in range(8):
        content_clips = max(1, math.ceil((base_content_budget + overlap) / average))
        boundaries = max(0, content_clips + title_card_count - 1)
        updated = boundaries * duration * ratio
        if math.isclose(updated, overlap, abs_tol=1e-9):
            return updated
        overlap = updated
    return overlap


def _with_transition_budget(
    plan: TimelinePlan,
    *,
    title_card_count: int,
    expected_clip_duration: float,
    expected_content_clips: int | None,
    transition_mode: Any,
    transition_duration: float,
) -> TimelinePlan:
    base_content_budget = max(0.0, plan.target_duration - plan.title_budget)
    transition_budget = _transition_overlap_budget(
        base_content_budget,
        title_card_count=title_card_count,
        expected_clip_duration=expected_clip_duration,
        expected_content_clips=expected_content_clips,
        transition_mode=transition_mode,
        transition_duration=transition_duration,
    )
    return replace(
        plan,
        content_budget=base_content_budget + transition_budget,
        transition_budget=transition_budget,
    )


def _selected_month_divider_count(clips: list[Any]) -> int:
    months = list(
        dict.fromkeys(
            (clip_date.year, clip_date.month)
            for clip in clips
            if (clip_date := _item_date(clip)) is not None
        )
    )
    return max(0, len(months) - 1)


def plan_timeline(
    clips: list[Any],
    title_settings: Any | None,
    target_duration: float,
    memory_type: str | None,
    *,
    expected_clip_duration: float = 5.0,
    expected_content_clips: int | None = None,
    transition_mode: Any = "none",
    transition_duration: float = 0.5,
) -> TimelinePlan:
    """Fit title screens inside 20% of the requested final duration."""
    target = max(0.0, target_duration)
    if title_settings is None or not getattr(title_settings, "enabled", True):
        return _with_transition_budget(
            TimelinePlan(target, target, 0.0, 0.0, 0.0, 0.0, 0),
            title_card_count=0,
            expected_clip_duration=expected_clip_duration,
            expected_content_clips=expected_content_clips,
            transition_mode=transition_mode,
            transition_duration=transition_duration,
        )

    remaining = target * _TITLE_SHARE
    title_duration = min(max(0.0, float(title_settings.title_duration)), remaining)
    remaining -= title_duration

    configured_ending = max(0.0, float(title_settings.ending_duration))
    ending_duration = min(configured_ending, remaining) if remaining >= _MIN_ENDING_SECONDS else 0.0
    remaining -= ending_duration

    divider_duration = max(0.0, float(title_settings.month_divider_duration))
    if _is_chronological_month_mode(title_settings, memory_type):
        eligible_dividers = _eligible_dividers(clips, title_settings, memory_type)
        reserved_dividers = (
            min(eligible_dividers, int(remaining // divider_duration))
            if divider_duration > 0.0
            else 0
        )
        title_budget = title_duration + ending_duration + reserved_dividers * divider_duration
        plan = TimelinePlan(
            target_duration=target,
            content_budget=target - title_budget,
            title_budget=title_budget,
            title_duration=title_duration,
            ending_duration=ending_duration,
            divider_duration=divider_duration,
            max_dividers=0,
            divider_policy="pending",
            eligible_dividers=eligible_dividers,
            soft_max_duration=target + min(10.0, target * _TITLE_SHARE),
        )
        return _with_transition_budget(
            plan,
            title_card_count=int(title_duration > 0.0)
            + reserved_dividers
            + int(ending_duration > 0.0),
            expected_clip_duration=expected_clip_duration,
            expected_content_clips=expected_content_clips,
            transition_mode=transition_mode,
            transition_duration=transition_duration,
        )

    if memory_type == "trip":
        # Location changes in the full candidate pool are a poor predictor of
        # the final cut. Resolve them after selection so phantom cards cannot
        # consume content time (Somme had five reserved but only one rendered).
        title_budget = title_duration + ending_duration
        plan = TimelinePlan(
            target_duration=target,
            content_budget=target - title_budget,
            title_budget=title_budget,
            title_duration=title_duration,
            ending_duration=ending_duration,
            divider_duration=divider_duration,
            max_dividers=0,
            divider_policy="pending",
            soft_max_duration=target + min(10.0, target * _TITLE_SHARE),
        )
        return _with_transition_budget(
            plan,
            title_card_count=int(title_duration > 0.0) + int(ending_duration > 0.0),
            expected_clip_duration=expected_clip_duration,
            expected_content_clips=expected_content_clips,
            transition_mode=transition_mode,
            transition_duration=transition_duration,
        )

    eligible_dividers = _eligible_dividers(clips, title_settings, memory_type)
    max_dividers = (
        min(eligible_dividers, int(remaining // divider_duration)) if divider_duration > 0.0 else 0
    )
    title_budget = title_duration + ending_duration + max_dividers * divider_duration
    plan = TimelinePlan(
        target_duration=target,
        content_budget=target - title_budget,
        title_budget=title_budget,
        title_duration=title_duration,
        ending_duration=ending_duration,
        divider_duration=divider_duration,
        max_dividers=max_dividers,
        eligible_dividers=eligible_dividers,
    )
    return _with_transition_budget(
        plan,
        title_card_count=int(title_duration > 0.0) + max_dividers + int(ending_duration > 0.0),
        expected_clip_duration=expected_clip_duration,
        expected_content_clips=expected_content_clips,
        transition_mode=transition_mode,
        transition_duration=transition_duration,
    )


def finalize_selected_timeline(
    preliminary: TimelinePlan,
    selected_clips: list[Any],
    *,
    selected_duration: float,
    title_settings: Any | None,
    memory_type: str | None,
    transition_mode: Any = "none",
    transition_duration: float = 0.5,
) -> TimelinePlan:
    """Choose all chronological month dividers or none after selection."""
    if preliminary.divider_policy != "pending":
        return preliminary
    if title_settings is None:
        return preliminary

    if memory_type == "trip":
        eligible = _eligible_dividers(selected_clips, title_settings, memory_type)
        remaining = max(
            0.0,
            preliminary.target_duration * _TITLE_SHARE
            - preliminary.title_duration
            - preliminary.ending_duration,
        )
        chosen = (
            min(eligible, int(remaining // preliminary.divider_duration))
            if preliminary.divider_duration > 0.0
            else 0
        )
        title_budget = (
            preliminary.title_duration
            + preliminary.ending_duration
            + chosen * preliminary.divider_duration
        )
        plan = replace(
            preliminary,
            title_budget=title_budget,
            max_dividers=chosen,
            divider_policy="capped",
            eligible_dividers=eligible,
            soft_max_duration=preliminary.target_duration + chosen * preliminary.divider_duration,
        )
        return _with_transition_budget(
            plan,
            title_card_count=int(plan.title_duration > 0.0)
            + chosen
            + int(plan.ending_duration > 0.0),
            expected_clip_duration=5.0,
            expected_content_clips=len(selected_clips),
            transition_mode=transition_mode,
            transition_duration=transition_duration,
        )

    if not _is_chronological_month_mode(title_settings, memory_type):
        return preliminary

    eligible = _selected_month_divider_count(selected_clips)
    complete_title_budget = (
        preliminary.title_duration
        + preliminary.ending_duration
        + eligible * preliminary.divider_duration
    )
    # WHY: Opening/ending time is already removed from the content budget. The
    # overflow allowance must cover the *complete* selected divider set too;
    # otherwise the generic 10-second cap makes six or more 2-second dividers
    # impossible for a well-filled yearly memory.
    soft_max = max(
        preliminary.soft_max_duration or preliminary.target_duration,
        preliminary.target_duration + eligible * preliminary.divider_duration,
    )
    complete_plan = _with_transition_budget(
        replace(
            preliminary,
            title_budget=complete_title_budget,
            max_dividers=eligible,
            eligible_dividers=eligible,
        ),
        title_card_count=int(preliminary.title_duration > 0.0)
        + eligible
        + int(preliminary.ending_duration > 0.0),
        expected_clip_duration=5.0,
        expected_content_clips=len(selected_clips),
        transition_mode=transition_mode,
        transition_duration=transition_duration,
    )
    include_all = (
        selected_duration + complete_title_budget - complete_plan.transition_budget <= soft_max
    )
    chosen = eligible if include_all else 0
    plan = replace(
        preliminary,
        title_budget=(
            preliminary.title_duration
            + preliminary.ending_duration
            + chosen * preliminary.divider_duration
        ),
        max_dividers=chosen,
        divider_policy="all" if include_all else "none",
        eligible_dividers=eligible,
        soft_max_duration=soft_max,
    )
    return _with_transition_budget(
        plan,
        title_card_count=int(plan.title_duration > 0.0) + chosen + int(plan.ending_duration > 0.0),
        expected_clip_duration=5.0,
        expected_content_clips=len(selected_clips),
        transition_mode=transition_mode,
        transition_duration=transition_duration,
    )
