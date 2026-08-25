"""How a recap's material is spread across the timeline.

Selection ranks clips; this module decides which *periods* deserve to be in
the cut at all. The two jobs are separable, and keeping them apart matters:
a per-day cap applied before selection silently destroys the density signal
selection needs, which is how a November recap once dropped its busiest day
entirely (#488).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)


_MAX_PHOTOS_PER_DAY = 2
_MAX_SHARE_FROM_ONE_DAY = 0.25
_EVENT_DENSITY_MULTIPLE = 3.0
_EVENT_CAP_MULTIPLE = 3
_EVENT_CLIPS_PER_PERIOD = 3
_MAX_EVENT_PERIODS = 2
_MIN_EVENT_PEOPLE_SHARE = 0.10


class PhotoDayCaps(NamedTuple):
    """What a single day may contribute to a cut, in photos.

    The two numbers are one rule and travel together: a dense day may hold
    more than an ordinary one, but no day passes the ceiling. Handing the
    preference downstream on its own is how the event multiple came to triple
    the very ceiling it sits next to — forty clips over four days meant a
    ten-photo preference and a thirty-photo event day, three quarters of the
    cut from one day.
    """

    preferred: int
    ceiling: int


_DEFAULT_PHOTO_DAY_CAPS = PhotoDayCaps(
    _MAX_PHOTOS_PER_DAY, _MAX_PHOTOS_PER_DAY * _EVENT_CAP_MULTIPLE
)


# How far apart two shots have to be to be different moments, by how much
# timeline the memory covers. Five minutes is a moment inside a sixty-second
# month, where the cut has a slot for most of the days in it. Across a year it
# is a rounding error: a real year recap spent two of its thirty-nine slots on
# one evening at a venue, 71 minutes apart, and two more on one arcade, 62
# minutes apart. The breakpoints are the ones _period_key already uses, so the
# module has one vocabulary for "how long is this memory".
_MOMENT_WINDOW_BY_SPAN = ((31, 0.0), (92, 30.0), (366, 90.0))
_MULTI_YEAR_MOMENT_WINDOW = 180.0


def moment_window_for(span_days: int, configured_minutes: float) -> float:
    """The same-moment window this memory should use, in minutes.

    The configured value is a floor, never a ceiling: a month uses exactly
    what it was given, and a longer memory widens from there. Zero still
    means no deduplication at all.
    """
    if configured_minutes <= 0:
        return configured_minutes
    for limit, minutes in _MOMENT_WINDOW_BY_SPAN:
        if span_days <= limit:
            return max(configured_minutes, minutes)
    return max(configured_minutes, _MULTI_YEAR_MOMENT_WINDOW)


def _people_share(clips: list[ClipWithSegment]) -> float:
    """Share of a period's material that Immich recognised a person in.

    Free: Immich has already run face recognition over the library, so this
    reads metadata rather than pixels.
    """
    if not clips:
        return 0.0
    with_people = sum(1 for c in clips if getattr(c.clip.asset, "people", None))
    return with_people / len(clips)


def _period_key(dt: datetime, span_days: int) -> str:
    """Bucket key adaptive to date range: weeks for short, months for medium, quarters for long."""
    if span_days <= 30:
        return dt.strftime("%Y-%m-%d")  # daily for ≤1 month
    if span_days <= 90:
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"  # weekly for ≤3 months
    if span_days <= 365:
        return dt.strftime("%Y-%m")  # monthly for ≤1 year
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"  # quarterly for >1 year


def _event_periods(by_period: dict[str, list[ClipWithSegment]]) -> set[str]:
    """Periods holding far more material than the month's norm.

    Contrast, not an absolute share: in a flat month every period would clear
    a fixed threshold and two arbitrary days would win on tie-break.
    """
    sizes = sorted(len(v) for v in by_period.values())
    median = sizes[len(sizes) // 2] if sizes else 0
    if not median:
        return set()
    threshold = _EVENT_DENSITY_MULTIPLE * median
    ranked = sorted(by_period, key=lambda k: (-len(by_period[k]), k))
    dense = [k for k in ranked if len(by_period[k]) >= threshold]
    # Volume is not significance. The densest day of a real November was 130
    # photos of an empty apartment — a property viewing, no people in any of
    # them — and density alone promoted that catalogue over the month's real
    # days. Measured across five event days: the viewing recognised people in
    # 0% of its assets, every genuine event in 14-51% (#488).
    peopled = [k for k in dense if _people_share(by_period[k]) >= _MIN_EVENT_PEOPLE_SHARE]
    return set(peopled[:_MAX_EVENT_PERIODS])


# How much of the pool the span is measured across. First-to-last is what one
# wrong timestamp decides on its own, and this library documents Shared Album
# assets arriving stamped 1970 — a single one turned a December into a
# fifty-year memory, which means quarterly period buckets and a three-hour
# moment window over one afternoon.
_SPAN_MARGIN_SHARE = 0.02
# Below this there is nothing to spare: dropping an end off a handful of
# clips throws away a real edge of the memory rather than a wrong one.
_SPAN_MIN_CLIPS_TO_TRIM = 10


def span_days_of(clips: list[ClipWithSegment]) -> int:
    """How much timeline this memory covers, ignoring its outermost dates."""
    dates = sorted(c.clip.asset.file_created_at for c in clips if c.clip.asset.file_created_at)
    if len(dates) < _SPAN_MIN_CLIPS_TO_TRIM:
        return (dates[-1] - dates[0]).days if dates else 0
    margin = max(1, int(len(dates) * _SPAN_MARGIN_SHARE))
    inner = dates[margin : len(dates) - margin] or dates
    return (inner[-1] - inner[0]).days


def _event_periods_of(clips: list[ClipWithSegment]) -> set[str]:
    """Event periods measured on the raw pool.

    Must run before any per-day cap: a cap compresses every day into the same
    narrow range, so the contrast test would end up reading its own output —
    real events stop standing out and flat days cross the line by accident.
    """
    dates = [c.clip.asset.file_created_at for c in clips]
    if not dates:
        return set()
    span_days = span_days_of(clips)
    by_period: dict[str, list[ClipWithSegment]] = defaultdict(list)
    for c in clips:
        by_period[_period_key(c.clip.asset.file_created_at, span_days)].append(c)
    return _event_periods(by_period)


def _fill_gap_periods(
    unselected_by_period: dict[str, list[ClipWithSegment]],
    covered: set[str],
    events: set[str],
) -> list[ClipWithSegment]:
    """One clip per uncovered period, densest period first.

    Order decides who is served once the duration budget can no longer serve
    everyone: filling chronologically treats a day holding a fifth of the
    month exactly like a quiet Tuesday. On a real April recap that cost the
    month its second event — 99 assets, a whole coastal day — while three
    clips from ordinary days took 14 of the 58.7 seconds. Event periods earn
    more than one clip so the day reads as a day rather than a glimpse.
    """
    by_material = sorted(unselected_by_period, key=lambda p: (-len(unselected_by_period[p]), p))
    fillers: list[ClipWithSegment] = []
    for period in by_material:
        if period in covered:
            continue
        ranked = sorted(unselected_by_period[period], key=lambda c: c.score, reverse=True)
        fillers.extend(ranked[: _EVENT_CLIPS_PER_PERIOD if period in events else 1])
        covered.add(period)
    return fillers


def _photo_caps_per_day(
    by_day: dict[str, list[ClipWithSegment]],
    caps: PhotoDayCaps,
) -> dict[str, int]:
    """Raise the per-day photo cap for days holding far more than the norm.

    A flat cap erases the signal selection depends on: on a real November the
    busiest day — 129 Live Photos inside one hour — reached the selector
    holding two clips, indistinguishable from a day with two idle snapshots,
    and the recap skipped the event entirely (#488). The ceiling is the outer
    bound on that bonus, not its input.
    """
    events = _event_periods(by_day)
    event_cap = min(caps.preferred * _EVENT_CAP_MULTIPLE, caps.ceiling)
    return {day: event_cap if day in events else caps.preferred for day in by_day}


def photos_per_day_for(target_clips: int, active_days: int) -> PhotoDayCaps:
    """How many photos a day may contribute before it counts as flooding.

    "Flooding" is relative to how many slots the memory has. Two a day suits a
    60-second month; for a four-day trip needing forty clips it left selection
    with eight photos to work with, so it filled a fifth of the runtime and
    duration backfill supplied the other four fifths — by relaxed constraints,
    where selection would have chosen. Measured on a 967-asset trip: 55
    candidates reached selection, 49 of the 55 final clips came from backfill.
    """
    if target_clips <= 0 or active_days <= 0:
        return _DEFAULT_PHOTO_DAY_CAPS
    # Twice the per-day share, so selection chooses rather than merely accepts,
    # but never more than a quarter of the whole cut from one day — six photos
    # of one race day in a seven-clip recap was the failure that put this cap
    # here in the first place.
    headroom = math.ceil(target_clips / active_days) * 2
    ceiling = max(_MAX_PHOTOS_PER_DAY, int(target_clips * _MAX_SHARE_FROM_ONE_DAY))
    return PhotoDayCaps(preferred=min(headroom, ceiling), ceiling=ceiling)


def _partition_photos_per_day(
    clips: list[ClipWithSegment],
    caps: PhotoDayCaps = _DEFAULT_PHOTO_DAY_CAPS,
) -> tuple[list[ClipWithSegment], list[ClipWithSegment]]:
    """Partition same-day photos into preferred and duration-fallback pools.

    Videos are always preferred. Within each day, the highest-scored photos
    enter initial selection and the remainder stay available for backfill.
    """
    from immich_memories.api.models import AssetType

    videos = [c for c in clips if c.clip.asset.type != AssetType.IMAGE]
    photos = [c for c in clips if c.clip.asset.type == AssetType.IMAGE]

    if not photos:
        return clips, []

    # Group photos by day, keep best N per day
    by_day: dict[str, list[ClipWithSegment]] = defaultdict(list)
    for p in photos:
        day_key = p.clip.asset.file_created_at.strftime("%Y-%m-%d")
        by_day[day_key].append(p)

    day_caps = _photo_caps_per_day(by_day, caps)
    kept_photos: list[ClipWithSegment] = []
    overflow_photos: list[ClipWithSegment] = []
    for day_key in sorted(by_day):
        day_photos = sorted(by_day[day_key], key=lambda c: c.score, reverse=True)
        cap = day_caps[day_key]
        kept_photos.extend(day_photos[:cap])
        overflow_photos.extend(day_photos[cap:])

    if overflow_photos:
        logger.info(
            f"Same-day photo preference: reserved {len(overflow_photos)} overflow photos "
            f"for duration backfill (preferred max {caps.preferred}/day, "
            f"ceiling {caps.ceiling})"
        )

    return videos + kept_photos, overflow_photos


def enforce_photo_cap(
    clips: list[ClipWithSegment],
    max_ratio: float,
    protected_ids: set[str] | None = None,
) -> list[ClipWithSegment]:
    """Drop lowest-scored photos until photo ratio <= max_ratio.

    Videos are never dropped. If only photos exist (no videos),
    all are kept since the ratio can't be improved by dropping.

    The cap always applies here, even when videos are too scarce to fill the
    budget. That is deliberate and two-stage: normalising a photo-biased
    selection first leaves backfill room to use every valid video candidate,
    and backfill then re-admits photos through photo_cap_bypassed. Bypassing
    here as well would admit them twice.

    protected_ids survive the cap. They are the clips selection kept to
    represent a period, and some months hold their best day entirely in
    photos: a November recap dropped all three clips of its busiest day —
    129 Live Photos shot inside one hour — because the cap ranked them as
    ordinary photos (#488). Protection the scaler honours has to hold here
    too, or coverage is not coverage.
    """
    from immich_memories.api.models import AssetType

    videos = [c for c in clips if c.clip.asset.type != AssetType.IMAGE]
    photos = [c for c in clips if c.clip.asset.type == AssetType.IMAGE]

    if not photos or not videos:
        return clips

    if max_ratio >= 1.0:
        return clips

    # Solve P / (V + P) <= ratio for P. Using the pre-filter total here
    # leaves the final, smaller result above the requested ratio.
    max_photos = max(0, int(len(videos) * max_ratio / (1.0 - max_ratio)))

    if len(photos) <= max_photos:
        return clips

    # Keep protected photos, then the highest-scored of the rest. A favorite
    # is protected wherever it appears: a February of newborn photos lost
    # three starred clips here to a ratio meant to stop a film becoming a
    # slideshow, when a slideshow of exactly those photos is what was asked
    # for.
    protected = (protected_ids or set()) | {
        c.clip.asset.id for c in photos if c.clip.asset.is_favorite
    }
    kept_photos = [c for c in photos if c.clip.asset.id in protected]
    others = sorted(
        (c for c in photos if c.clip.asset.id not in protected),
        key=lambda c: c.score,
        reverse=True,
    )
    kept_photos += others[: max(0, max_photos - len(kept_photos))]

    # WHY not "final": duration backfill runs after this and adds more clips, so
    # the count here is the pool mid-refinement. A real run logged "2 photos
    # (50% of 4 final clips)" and delivered a 13-clip video with 7 photos in it,
    # which reads as the cap having been ignored rather than simply not final.
    logger.info(
        f"Photo cap: {len(photos)} → {len(kept_photos)} photos "
        f"(at most {max_ratio:.0%} alongside {len(videos)} videos; "
        f"backfill may add more)"
    )

    return videos + kept_photos
