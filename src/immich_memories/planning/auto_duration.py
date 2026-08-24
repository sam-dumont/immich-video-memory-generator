"""Realistic, media-aware duration planning for automatic memories."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from immich_memories.api.models import Asset, VideoClipInfo

_TRIP_BASE_SECONDS = 30.0
_TRIP_SECONDS_PER_ACTIVE_DAY = 10.0
_TRIP_MIN_EDITORIAL_SECONDS = 60.0
_TRIP_MAX_EDITORIAL_SECONDS = 300.0
_SPECIAL_DAY_BASE_SECONDS = 30.0
_SPECIAL_DAY_SECONDS_PER_ACTIVE_HOUR = 6.0
_SPECIAL_DAY_MIN_EDITORIAL_SECONDS = 60.0
_SPECIAL_DAY_MAX_EDITORIAL_SECONDS = 180.0
_MAX_DIVERSE_SECONDS_PER_DAY = 30.0
_MAX_CAPACITY_PHOTOS_PER_DAY = 4
_DURATION_ROUNDING_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class AutoDurationResult:
    """Resolved automatic runtime and the evidence used to choose it."""

    total_seconds: float
    active_days: int
    editorial_seconds: float
    diverse_capacity_seconds: float


def trip_editorial_duration_seconds(active_days: int) -> float:
    """Return the bounded editorial target before media-capacity adjustment."""
    if active_days <= 0:
        return 0.0
    return min(
        _TRIP_MAX_EDITORIAL_SECONDS,
        max(
            _TRIP_MIN_EDITORIAL_SECONDS,
            _TRIP_BASE_SECONDS + active_days * _TRIP_SECONDS_PER_ACTIVE_DAY,
        ),
    )


def special_day_editorial_duration_seconds(hours: float) -> float:
    """How long one occasion runs, from how long it stayed awake.

    ``hours`` is the recorded window's span when the catalogue trimmed one, and
    the activity run's active hours when it did not. Active hours is the signal
    already measured to separate an occasion from a busy afternoon, so keying
    runtime off it is the same evidence twice rather than a new invention.

    These constants are a **starting curve to be measured, not trusted**: check
    them on a contact sheet across a real catalogue before treating any of the
    three numbers as settled, and record which side of the content-first scoring
    change the sheet came from.
    """
    return min(
        _SPECIAL_DAY_MAX_EDITORIAL_SECONDS,
        max(
            _SPECIAL_DAY_MIN_EDITORIAL_SECONDS,
            _SPECIAL_DAY_BASE_SECONDS + hours * _SPECIAL_DAY_SECONDS_PER_ACTIVE_HOUR,
        ),
    )


def _asset_day(asset: Asset) -> date:
    return asset.file_created_at.date()


def resolve_trip_auto_duration(
    clips: Sequence[VideoClipInfo],
    photos: Sequence[Asset],
    *,
    avg_clip_duration: float,
    photo_duration: float,
    title_duration: float,
    ending_duration: float,
) -> AutoDurationResult:
    """Resolve a trip runtime from active days and diverse usable excerpts.

    The editorial curve stays intentionally modest. Capacity is computed from
    final excerpt lengths, not raw source lengths, and one dense day cannot
    inflate the recommendation beyond thirty seconds.
    """
    video_seconds_by_day: dict[date, float] = defaultdict(float)
    photo_count_by_day: dict[date, int] = defaultdict(int)
    clip_limit = max(0.0, avg_clip_duration)
    still_duration = max(0.0, photo_duration)

    for clip in clips:
        source_duration = max(0.0, clip.duration_seconds)
        video_seconds_by_day[_asset_day(clip.asset)] += min(source_duration, clip_limit)

    for photo in photos:
        photo_count_by_day[_asset_day(photo)] += 1

    active_dates = set(video_seconds_by_day) | set(photo_count_by_day)
    active_days = len(active_dates)
    if active_days == 0:
        return AutoDurationResult(0.0, 0, 0.0, 0.0)

    editorial_seconds = trip_editorial_duration_seconds(active_days)

    content_capacity = 0.0
    for active_date in active_dates:
        photo_seconds = (
            min(photo_count_by_day[active_date], _MAX_CAPACITY_PHOTOS_PER_DAY) * still_duration
        )
        content_capacity += min(
            _MAX_DIVERSE_SECONDS_PER_DAY,
            video_seconds_by_day[active_date] + photo_seconds,
        )

    title_seconds = max(0.0, title_duration) + max(0.0, ending_duration)
    diverse_capacity_seconds = content_capacity + title_seconds
    bounded_seconds = min(editorial_seconds, diverse_capacity_seconds)
    total_seconds = (
        math.floor(bounded_seconds / _DURATION_ROUNDING_SECONDS) * _DURATION_ROUNDING_SECONDS
    )

    return AutoDurationResult(
        total_seconds=total_seconds,
        active_days=active_days,
        editorial_seconds=editorial_seconds,
        diverse_capacity_seconds=diverse_capacity_seconds,
    )
