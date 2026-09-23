"""Realistic, media-aware duration planning for automatic memories."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from immich_memories.api.models import Asset, VideoClipInfo

# What set a film's length, in the words a run record and a log line use.
DURATION_FROM_MATERIAL = "the material"
DURATION_FROM_DURATION_FLAG = "--duration"
DURATION_FROM_SHORT_FORM = "--short-form"
DURATION_FROM_PRESET = "the preset floor"

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

# A trip and an album have no calendar period to measure coverage against:
# their span is whatever their media turns out to cover.
_TRIP_CURVE_TYPES = ("trip", "album")


@dataclass(frozen=True, slots=True)
class DurationDecision:
    """How long a film runs, and what decided it.

    ``source`` is one of the four things that can set a length: an explicit
    ``--duration``, a ``--short-form`` preset, the material the period holds,
    or the type's preset floor when the period holds nothing to measure.
    """

    seconds: float
    source: str
    photographed_days: int = 0
    editorial_seconds: float = 0.0
    capacity_seconds: float = 0.0

    def as_record(self) -> dict[str, float | int | str]:
        """The fields a run record carries so a reader can see why a film is this long."""
        return {
            "seconds": round(self.seconds, 2),
            "source": self.source,
            "photographed_days": self.photographed_days,
            "editorial_seconds": round(self.editorial_seconds, 2),
            "capacity_seconds": round(self.capacity_seconds, 2),
        }

    def sentence(self) -> str:
        """One line for the log and the console."""
        if self.source != DURATION_FROM_MATERIAL:
            return f"Duration {self.seconds:.0f}s, set by {self.source}"
        return (
            f"Duration {self.seconds:.0f}s, set by {self.source}: "
            f"{self.photographed_days} photographed days "
            f"(editorial {self.editorial_seconds:.0f}s, "
            f"capacity {self.capacity_seconds:.0f}s)"
        )


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


@dataclass(frozen=True, slots=True)
class _Material:
    """The usable seconds and stills a period holds, gathered by the day they fell on."""

    video_seconds_by_day: dict[date, float]
    photo_count_by_day: dict[date, int]

    @classmethod
    def of(
        cls,
        clips: Sequence[VideoClipInfo],
        photos: Sequence[Asset],
        *,
        clip_limit: float,
    ) -> _Material:
        video_seconds_by_day: dict[date, float] = defaultdict(float)
        photo_count_by_day: dict[date, int] = defaultdict(int)
        for clip in clips:
            source_duration = max(0.0, clip.duration_seconds)
            video_seconds_by_day[_asset_day(clip.asset)] += min(source_duration, clip_limit)
        for photo in photos:
            photo_count_by_day[_asset_day(photo)] += 1
        return cls(video_seconds_by_day, photo_count_by_day)

    @property
    def photographed_days(self) -> int:
        return len(set(self.video_seconds_by_day) | set(self.photo_count_by_day))

    def diverse_capacity_seconds(self, *, still_duration: float, title_seconds: float) -> float:
        """What the editor can fill: excerpt lengths, not raw sources.

        One dense day cannot inflate the recommendation beyond thirty seconds,
        and a burst of forty frames of the same scene counts as four stills.
        """
        content = 0.0
        for day in set(self.video_seconds_by_day) | set(self.photo_count_by_day):
            photo_seconds = (
                min(self.photo_count_by_day[day], _MAX_CAPACITY_PHOTOS_PER_DAY) * still_duration
            )
            content += min(
                _MAX_DIVERSE_SECONDS_PER_DAY,
                self.video_seconds_by_day[day] + photo_seconds,
            )
        return content + title_seconds


def decide_memory_duration(
    clips: Sequence[VideoClipInfo],
    photos: Sequence[Asset],
    *,
    requested_seconds: float | None,
    requested_source: str,
    preset_seconds: float | None,
    memory_type: str | None,
    avg_clip_duration: float,
    photo_duration: float,
    title_duration: float,
    ending_duration: float,
) -> DurationDecision:
    """Fit a memory's runtime to the material discovery actually found.

    A length the run was told to use is returned untouched; otherwise the type's
    editorial curve proposes a length and the diverse excerpts the period holds
    cap it, so no film is longer than the editor can fill. A period with nothing
    in it keeps its preset rather than collapsing to zero.
    """
    if requested_seconds is not None:
        return DurationDecision(float(requested_seconds), requested_source)

    floor_seconds = max(0.0, float(preset_seconds or 0.0))
    material = _Material.of(clips, photos, clip_limit=max(0.0, avg_clip_duration))
    if material.photographed_days == 0:
        return DurationDecision(floor_seconds, DURATION_FROM_PRESET)

    editorial_seconds = _editorial_target(
        memory_type,
        floor_seconds,
        photographed_days=material.photographed_days,
    )
    capacity_seconds = material.diverse_capacity_seconds(
        still_duration=max(0.0, photo_duration),
        title_seconds=max(0.0, title_duration) + max(0.0, ending_duration),
    )
    fitted_seconds = _rounded_down(min(editorial_seconds, capacity_seconds))
    return DurationDecision(
        seconds=fitted_seconds if fitted_seconds > 0.0 else floor_seconds,
        source=DURATION_FROM_MATERIAL if fitted_seconds > 0.0 else DURATION_FROM_PRESET,
        photographed_days=material.photographed_days,
        editorial_seconds=editorial_seconds,
        capacity_seconds=capacity_seconds,
    )


def _editorial_target(
    memory_type: str | None,
    preset_seconds: float,
    *,
    photographed_days: int,
) -> float:
    """The length this type's own curve asks for, before capacity caps it."""
    if memory_type in _TRIP_CURVE_TYPES:
        return trip_editorial_duration_seconds(photographed_days)
    return preset_seconds


def _rounded_down(seconds: float) -> float:
    return math.floor(seconds / _DURATION_ROUNDING_SECONDS) * _DURATION_ROUNDING_SECONDS
