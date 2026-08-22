"""How a recap's material is spread across the timeline.

Selection ranks clips; this module decides which *periods* deserve to be in
the cut at all. The two jobs are separable, and keeping them apart matters:
a per-day cap applied before selection silently destroys the density signal
selection needs, which is how a November recap once dropped its busiest day
entirely (#488).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)


_MAX_PHOTOS_PER_DAY = 2
_EVENT_DENSITY_MULTIPLE = 3.0
_EVENT_CAP_MULTIPLE = 3
_EVENT_CLIPS_PER_PERIOD = 3
_MAX_EVENT_PERIODS = 2
_MIN_EVENT_PEOPLE_SHARE = 0.10


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


def _event_periods_of(clips: list[ClipWithSegment]) -> set[str]:
    """Event periods measured on the raw pool.

    Must run before any per-day cap: a cap compresses every day into the same
    narrow range, so the contrast test would end up reading its own output —
    real events stop standing out and flat days cross the line by accident.
    """
    dates = [c.clip.asset.file_created_at for c in clips]
    if not dates:
        return set()
    span_days = (max(dates) - min(dates)).days
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
    base_cap: int,
) -> dict[str, int]:
    """Raise the per-day photo cap for days holding far more than the norm.

    A flat cap erases the signal selection depends on: on a real November the
    busiest day — 129 Live Photos inside one hour — reached the selector
    holding two clips, indistinguishable from a day with two idle snapshots,
    and the recap skipped the event entirely (#488).
    """
    events = _event_periods(by_day)
    event_cap = base_cap * _EVENT_CAP_MULTIPLE
    return {day: event_cap if day in events else base_cap for day in by_day}


def _partition_photos_per_day(
    clips: list[ClipWithSegment],
    max_per_day: int = _MAX_PHOTOS_PER_DAY,
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

    caps = _photo_caps_per_day(by_day, max_per_day)
    kept_photos: list[ClipWithSegment] = []
    overflow_photos: list[ClipWithSegment] = []
    for day_key in sorted(by_day):
        day_photos = sorted(by_day[day_key], key=lambda c: c.score, reverse=True)
        cap = caps[day_key]
        kept_photos.extend(day_photos[:cap])
        overflow_photos.extend(day_photos[cap:])

    if overflow_photos:
        logger.info(
            f"Same-day photo preference: reserved {len(overflow_photos)} overflow photos "
            f"for duration backfill (preferred max {max_per_day}/day)"
        )

    return videos + kept_photos, overflow_photos


def enforce_photo_cap(
    clips: list[ClipWithSegment],
    max_ratio: float,
    videos_scarce: bool = False,
    protected_ids: set[str] | None = None,
) -> list[ClipWithSegment]:
    """Drop lowest-scored photos until photo ratio <= max_ratio.

    Videos are never dropped. If only photos exist (no videos),
    all are kept since the ratio can't be improved by dropping.
    When videos_scarce is True, the cap is bypassed entirely —
    photos fill the budget freely (matches unified_budget PR #224).

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

    if videos_scarce:
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
