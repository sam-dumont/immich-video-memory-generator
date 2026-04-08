"""Unified photo+video budget selection.

Merges video and photo candidates into a single ranked pool
and fits them to a duration budget. Adaptive: temporal coverage
matters as much as raw score.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.processing.assembly_config import TitleScreenSettings

logger = logging.getLogger(__name__)

# Target 1-2 photos per day to avoid one event dominating.
_MAX_PHOTOS_PER_DAY = 2


@dataclass
class BudgetCandidate:
    """A video or photo competing for duration budget."""

    asset_id: str
    duration: float
    score: float
    candidate_type: str  # "video" | "photo"
    date: datetime
    is_favorite: bool = False


@dataclass
class UnifiedSelection:
    """Result of unified budget selection."""

    kept_video_ids: set[str] = field(default_factory=set)
    selected_photo_ids: list[str] = field(default_factory=list)
    content_duration: float = 0.0
    overhead_estimate: float = 0.0


def estimate_title_overhead(
    clip_dates: list[str],
    title_settings: TitleScreenSettings | None,
    target_duration: float,
    memory_type: str | None = None,
    num_clips: int = 0,
    transition_duration: float = 0.5,
) -> float:
    """Estimate title/divider/ending overhead in seconds.

    Accounts for crossfade transitions that compress the timeline:
    each transition overlaps two clips, saving ~half the transition duration.
    """
    if title_settings is None or not title_settings.enabled:
        return 0.0

    overhead = title_settings.title_duration + title_settings.ending_duration

    # Count month/year dividers
    if title_settings.show_month_dividers and clip_dates:
        from collections import Counter

        month_counts = Counter(d[:7] for d in clip_dates if d)
        divider_count = sum(
            1 for count in month_counts.values() if count >= title_settings.month_divider_threshold
        )
        overhead += divider_count * title_settings.month_divider_duration

    # WHY: Crossfade transitions overlap adjacent clips, compressing the timeline.
    # Each transition saves ~half its duration from the total.
    # title(1) + clips(N) + ending(1) → N+1 transitions
    if num_clips > 0:
        num_transitions = num_clips + 1  # title-to-first + between-clips + last-to-ending
        crossfade_savings = num_transitions * transition_duration * 0.5
        overhead = max(0.0, overhead - crossfade_savings)

    # Memory-type-aware cap
    if memory_type == "trip":
        # WHY: Trip location cards are narrative-essential — higher cap.
        # Floor guarantees trip structure even when clips are dense.
        overhead_cap = target_duration * 0.30
        overhead_floor = target_duration * 0.10
        return max(min(overhead, overhead_cap), overhead_floor)

    return min(overhead, target_duration * 0.20)


def select_within_budget(
    videos: list[BudgetCandidate],
    photos: list[BudgetCandidate],
    content_budget: float,
    max_photo_ratio: float = 0.50,
    min_photo_ratio: float = 0.10,
) -> UnifiedSelection:
    """Select videos and photos that fit within the content budget.

    Reserves min_photo_ratio of the budget for photos so they always
    get a seat at the table. Videos fill the rest. Temporal sole
    representatives are protected from removal.
    """
    if not videos and not photos:
        return UnifiedSelection()

    # Step 1: Reserve budget for photos (min_photo_ratio)
    # WHY: Without reservation, dense video months starve photos entirely.
    photo_reserved = content_budget * min_photo_ratio if photos else 0.0
    video_budget = content_budget - photo_reserved

    # Step 2: Fit videos into video budget (trim if over)
    total_video_duration = sum(v.duration for v in videos)

    if total_video_duration > video_budget:
        kept_videos = _trim_videos_to_budget(videos, video_budget)
    else:
        kept_videos = videos.copy()

    kept_ids = {v.asset_id for v in kept_videos}
    video_duration = sum(v.duration for v in kept_videos)

    # Step 3: Fill remaining budget with photos (reserved + any video underspend)
    remaining_for_photos = content_budget - video_duration
    if photos and remaining_for_photos > 0:
        selected_photos = _fill_with_photos(
            photos,
            remaining_for_photos,
            video_count=len(kept_videos),
            max_photo_ratio=max_photo_ratio,
            content_budget=content_budget,
            video_duration=video_duration,
        )
        photo_ids = [p.asset_id for p in selected_photos]
        running_duration = video_duration + sum(p.duration for p in selected_photos)
    else:
        photo_ids = []
        running_duration = video_duration

    return UnifiedSelection(
        kept_video_ids=kept_ids,
        selected_photo_ids=photo_ids,
        content_duration=running_duration,
    )


def _trim_videos_to_budget(videos: list[BudgetCandidate], budget: float) -> list[BudgetCandidate]:
    """Remove lowest-scored videos to fit budget, protecting temporal sole reps."""
    # Count clips per month to find sole representatives
    month_counts: dict[str, int] = defaultdict(int)
    for v in videos:
        month_key = v.date.strftime("%Y-%m")
        month_counts[month_key] += 1

    def removability(c: BudgetCandidate) -> tuple:
        month_key = c.date.strftime("%Y-%m")
        is_sole = month_counts[month_key] <= 1
        return (is_sole, c.is_favorite, c.score)

    # Sort by removability: most removable first
    sorted_candidates = sorted(videos, key=removability)

    # Greedily keep from the top (least removable)
    kept: list[BudgetCandidate] = []
    running = 0.0
    for c in reversed(sorted_candidates):
        if running + c.duration <= budget:
            kept.append(c)
            running += c.duration

    # If we removed a sole representative, that month lost all clips.
    # Track which months still have clips and back-fill if possible.
    kept_ids = {c.asset_id for c in kept}
    kept_months = {c.date.strftime("%Y-%m") for c in kept}
    for c in sorted_candidates:
        if c.asset_id in kept_ids:
            continue
        month_key = c.date.strftime("%Y-%m")
        if month_key not in kept_months and running + c.duration <= budget:
            kept.append(c)
            kept_ids.add(c.asset_id)
            kept_months.add(month_key)
            running += c.duration

    return kept


def _passes_ratio_check(
    video_count: int,
    selected_count: int,
    max_photo_ratio: float,
    videos_are_scarce: bool,
) -> bool:
    """Check if adding one more photo stays within the ratio cap."""
    if video_count == 0 or videos_are_scarce:
        return True
    total_after = video_count + selected_count + 1
    photo_ratio_after = (selected_count + 1) / total_after
    return photo_ratio_after <= max_photo_ratio


def _fill_with_photos(
    photos: list[BudgetCandidate],
    remaining_budget: float,
    video_count: int,
    max_photo_ratio: float,
    content_budget: float = 0.0,
    video_duration: float = 0.0,
) -> list[BudgetCandidate]:
    """Fill remaining budget with temporally-spread photos.

    Uses multi-pass selection to cover unique days first, then densify:
    1. Best photo per day (temporal spread)
    2. Second-best per day
    3. Remaining by score (still capped at 2/day)
    """
    if not photos:
        return []

    # WHY: if videos use less than half the content budget, videos are scarce
    # and photos should fill freely without ratio cap
    videos_are_scarce = content_budget > 0 and video_duration < content_budget * 0.5

    # Group photos by day, each day sorted by score descending
    by_day: dict[str, list[BudgetCandidate]] = defaultdict(list)
    for p in photos:
        by_day[p.date.strftime("%Y-%m-%d")].append(p)
    for day_photos in by_day.values():
        day_photos.sort(key=lambda p: p.score, reverse=True)

    selected: list[BudgetCandidate] = []
    used_ids: set[str] = set()
    running = 0.0

    def _try_add(photo: BudgetCandidate) -> bool:
        nonlocal running
        if photo.asset_id in used_ids:
            return False
        if running + photo.duration > remaining_budget:
            return False
        if not _passes_ratio_check(video_count, len(selected), max_photo_ratio, videos_are_scarce):
            return False
        selected.append(photo)
        used_ids.add(photo.asset_id)
        running += photo.duration
        return True

    # Round 1: best photo from each day (covers maximum unique days)
    days_sorted = sorted(by_day.keys())
    for day in days_sorted:
        if by_day[day]:
            _try_add(by_day[day][0])

    # Round 2: second-best per day (soft cap of 2 per day)
    for day in days_sorted:
        if len(by_day[day]) > 1:
            _try_add(by_day[day][1])

    # Round 3: remaining photos ranked by score, still capped at 2/day.
    # WHY: prevents one event from dominating (e.g., 6 race photos from 1 day).
    # The cap is soft: rounds 1+2 already placed up to 2/day, round 3 only
    # adds more if some days haven't hit the cap yet.
    day_counts: dict[str, int] = defaultdict(int)
    for p in selected:
        day_counts[p.date.strftime("%Y-%m-%d")] += 1

    remaining = [p for p in photos if p.asset_id not in used_ids]
    remaining.sort(key=lambda p: p.score, reverse=True)
    for photo in remaining:
        day_key = photo.date.strftime("%Y-%m-%d")
        if day_counts[day_key] >= _MAX_PHOTOS_PER_DAY:
            continue
        if _try_add(photo):
            day_counts[day_key] += 1

    selected.sort(key=lambda p: p.date)
    return selected
