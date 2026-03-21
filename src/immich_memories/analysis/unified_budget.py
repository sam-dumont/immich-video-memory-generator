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


def _fill_with_photos(
    photos: list[BudgetCandidate],
    remaining_budget: float,
    video_count: int,
    max_photo_ratio: float,
) -> list[BudgetCandidate]:
    """Fill remaining budget with highest-scored photos, respecting ratio cap."""
    ranked = sorted(photos, key=lambda p: p.score, reverse=True)

    selected: list[BudgetCandidate] = []
    running = 0.0

    for photo in ranked:
        if running + photo.duration > remaining_budget:
            continue

        # Check ratio: (selected_photos + 1) / (videos + selected_photos + 1) <= ratio
        # WHY: skip ratio check when no videos — can't improve ratio by dropping photos
        if video_count > 0:
            total_after = video_count + len(selected) + 1
            photo_ratio_after = (len(selected) + 1) / total_after
            if photo_ratio_after > max_photo_ratio:
                continue

        selected.append(photo)
        running += photo.duration

    # Sort by date for chronological ordering
    selected.sort(key=lambda p: p.date)
    return selected
