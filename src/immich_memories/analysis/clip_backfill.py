"""Filling a cut to its runtime once selection has chosen.

Selection decides what belongs in a memory. This decides what to do when what
belongs does not add up to the requested length — which is most of the work on
a long memory, and was most of the redundancy in one: a trip whose selection
filled a fifth of its runtime had the other four fifths supplied here.

The order of concessions is the whole design. Loosening who appears barely
shows; admitting more stills shows a little; admitting a moment already in the
cut shows most of all. Past that, holding the best clips longer beats reaching
for a clip nobody would choose, and finishing short beats both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from immich_memories.analysis.boundary_placement import extend_end_to_gap

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BackfillContext:
    """Selection state used to evaluate one duration-backfill candidate."""

    config: PipelineConfig
    selected_count: int
    photo_count: int
    non_favorite_count: int
    temporal_window: float
    occupied_moments: list[datetime]


@dataclass(frozen=True)
class _BackfillCandidates:
    """Resolved candidate set plus any constraint relaxation that produced it."""

    items: list[ClipWithSegment]
    photo_limit: float | None
    tier: str = "strict"
    used_overrun: bool = False


def _is_backfill_candidate_admissible(
    candidate: ClipWithSegment,
    *,
    context: _BackfillContext,
    photo_limit: float | None,
    remaining_budget: float,
    enforce_favorite_ratio: bool = True,
    enforce_temporal_spacing: bool = True,
) -> bool:
    """Return whether a leftover preserves active selection constraints."""
    from immich_memories.analysis.clip_scaler import is_same_moment
    from immich_memories.api.models import AssetType

    candidate_duration = candidate.end_time - candidate.start_time
    if candidate_duration <= 0 or candidate_duration > remaining_budget + 1e-6:
        return False

    # Conceding temporal spacing relaxes the window back to the configured
    # base, never to nothing. A long memory measures a moment in tens of
    # minutes, so a wide window empties the strict pass sooner and the ladder
    # reaches this concession more often — and dropping the rule outright put
    # two clips stamped the same minute in a rendered year recap. The
    # concession is "an evening already in the cut", not "this shot twice".
    spacing = (
        context.temporal_window
        if enforce_temporal_spacing
        else context.config.temporal_dedup_window_minutes
    )
    if is_same_moment(candidate.clip.asset.file_created_at, context.occupied_moments, spacing):
        return False

    new_total = context.selected_count + 1
    if (
        candidate.clip.asset.type == AssetType.IMAGE
        and photo_limit is not None
        and (context.photo_count + 1) / new_total > photo_limit + 1e-9
    ):
        return False

    if (
        enforce_favorite_ratio
        and not candidate.clip.asset.is_favorite
        and context.config.prioritize_favorites
        and context.config.max_non_favorite_ratio < 1.0
    ):
        favorite_count = context.selected_count - context.non_favorite_count
        max_non_favorites = max(
            int(new_total * context.config.max_non_favorite_ratio),
            max(0, context.config.target_clips - favorite_count),
        )
        if context.non_favorite_count + 1 > max_non_favorites:
            return False

    return True


def _admissible_backfill_candidates(
    available: list[ClipWithSegment],
    *,
    context: _BackfillContext,
    photo_limit: float | None,
    remaining_budget: float,
    enforce_favorite_ratio: bool = True,
    enforce_temporal_spacing: bool = True,
) -> list[ClipWithSegment]:
    """Filter leftovers through the active backfill constraints."""
    return [
        candidate
        for candidate in available
        if _is_backfill_candidate_admissible(
            candidate,
            context=context,
            photo_limit=photo_limit,
            remaining_budget=remaining_budget,
            enforce_favorite_ratio=enforce_favorite_ratio,
            enforce_temporal_spacing=enforce_temporal_spacing,
        )
    ]


def _resolve_backfill_candidates(
    available: list[ClipWithSegment],
    *,
    context: _BackfillContext,
    active_photo_limit: float | None,
    remaining_budget: float,
) -> _BackfillCandidates:
    """Find candidates by progressively relaxing editorial constraints."""
    exact = _admissible_backfill_candidates(
        available,
        context=context,
        photo_limit=active_photo_limit,
        remaining_budget=remaining_budget,
    )
    if exact:
        return _BackfillCandidates(exact, active_photo_limit)

    # Order of concessions, worst-last. Loosening who appears (non-favorites)
    # barely shows. Admitting a few more stills shows a little. Admitting a
    # clip from a moment already in the cut shows most of all: a real August
    # filled a 10.9s gap that way and shipped four near-identical videos of
    # the same field, two of them four minutes apart. The photo ratio used to
    # be spent last, on the reasoning that stills turn a film into a
    # slideshow — true, but a bounded number of stills reads better than the
    # same shot twice.
    relaxed_favorites = _admissible_backfill_candidates(
        available,
        context=context,
        photo_limit=active_photo_limit,
        remaining_budget=remaining_budget,
        enforce_favorite_ratio=False,
    )
    if relaxed_favorites:
        return _BackfillCandidates(relaxed_favorites, active_photo_limit, "favorite_ratio")

    relaxed_photo_limit = active_photo_limit
    if active_photo_limit is not None and active_photo_limit < 0.70:
        relaxed_photo_limit = 0.70
        relaxed_photos = _admissible_backfill_candidates(
            available,
            context=context,
            photo_limit=relaxed_photo_limit,
            remaining_budget=remaining_budget,
            enforce_favorite_ratio=False,
        )
        if relaxed_photos:
            return _BackfillCandidates(relaxed_photos, relaxed_photo_limit, "photo_ratio_70")

    relaxed_temporal = _admissible_backfill_candidates(
        available,
        context=context,
        photo_limit=relaxed_photo_limit,
        remaining_budget=remaining_budget,
        enforce_favorite_ratio=False,
        enforce_temporal_spacing=False,
    )
    if relaxed_temporal:
        return _BackfillCandidates(relaxed_temporal, relaxed_photo_limit, "temporal_spacing")

    if relaxed_photo_limit is not None:
        unlimited_photos = _admissible_backfill_candidates(
            available,
            context=context,
            photo_limit=None,
            remaining_budget=remaining_budget,
            enforce_favorite_ratio=False,
            enforce_temporal_spacing=False,
        )
        if unlimited_photos:
            return _BackfillCandidates(unlimited_photos, None, "photo_ratio_unlimited")

    overrun = _admissible_backfill_candidates(
        available,
        context=context,
        photo_limit=None,
        remaining_budget=remaining_budget + 2.0,
        enforce_favorite_ratio=False,
        enforce_temporal_spacing=False,
    )
    return _BackfillCandidates(
        overrun,
        None,
        "bounded_overrun",
        used_overrun=bool(overrun),
    )


def _choose_backfill_candidate(
    candidates: list[ClipWithSegment],
    *,
    selected_dates: list[datetime],
    photo_cap_bypassed: bool,
) -> ClipWithSegment:
    """Choose a constraint-safe leftover by type, favorite, spread, then score."""
    from immich_memories.api.models import AssetType

    def rank(item: ClipWithSegment) -> tuple[bool, bool, float, float]:
        temporal_distance = min(
            (
                abs((item.clip.asset.file_created_at - date).total_seconds())
                for date in selected_dates
            ),
            default=0.0,
        )
        # Favorite before the prefer-a-video rule: preferring footage is a
        # heuristic about pacing, and a star is the user telling us directly.
        return (
            item.clip.asset.is_favorite,
            photo_cap_bypassed and item.clip.asset.type != AssetType.IMAGE,
            temporal_distance,
            item.score,
        )

    return max(candidates, key=rank)


def _initial_backfill_photo_limit(
    config: PipelineConfig,
    *,
    photo_cap_bypassed: bool,
) -> float | None:
    """Return the strict photo limit, or no limit when scarcity bypasses it."""
    if photo_cap_bypassed or config.photo_max_ratio >= 1.0:
        return None
    return config.photo_max_ratio


_MAX_EXTRA_PER_CLIP = 2.0  # seconds a strong clip may be held longer
_MIN_USEFUL_EXTRA = 0.05  # below this the hold is not worth a re-cut


def _safe_end_within(member: ClipWithSegment, limit: float) -> float | None:
    """Furthest this clip's end may be held out to, or None to leave it alone.

    The end was put inside a pause by the speech-aware snap, and nothing
    between here and FFmpeg checks it again, so an end that moves has to land
    in another pause. `safe_cut_gaps` is None when this run never measured the
    audio — the analysis cache restores boundaries without the evidence behind
    them — and an end that cannot be vouched for does not move at all.
    Finishing short is what this step already does when nothing can be added.
    """
    from immich_memories.analysis.source_filter import is_a_still

    # A Live Photo is footage: it carries a video component and is rendered
    # from it, so it answers to the gap rule like any other clip.
    if is_a_still(member.clip.asset):
        return limit
    gaps = member.clip.safe_cut_gaps
    if gaps is None:
        return None
    return extend_end_to_gap(member.end_time, limit, gaps)


def _hold_best_clips_longer(
    selected: list[ClipWithSegment],
    gap_seconds: float,
) -> tuple[float, int]:
    """Hold the strongest clips longer to cover a gap. Returns (gained, clips held).

    Better than the alternative it replaces. When the pool has nothing good
    left, backfill used to relax its constraints until something fit, and a
    weak clip nobody would choose ended up in the cut to buy four seconds.
    Letting the best clip breathe for two more seconds costs nothing and shows
    something worth watching.
    """
    if gap_seconds <= 0 or not selected:
        return 0.0, 0

    gained = 0.0
    held = 0
    for member in sorted(selected, key=lambda c: c.score, reverse=True):
        if gained >= gap_seconds:
            break
        budget = min(_MAX_EXTRA_PER_CLIP, gap_seconds - gained)
        limit = min(member.end_time + budget, member.clip.duration_seconds)
        new_end = _safe_end_within(member, limit)
        if new_end is None or new_end - member.end_time <= _MIN_USEFUL_EXTRA:
            continue
        gained += new_end - member.end_time
        member.end_time = new_end
        held += 1
    return gained, held


def _occupied_moments(
    selected: list[ClipWithSegment],
    temporal_window: float,
) -> list[datetime]:
    """When the cut is already covered, so backfill cannot re-add a moment."""
    if temporal_window <= 0:
        return []
    return [
        item.clip.asset.file_created_at
        for item in selected
        if item.clip.asset.file_created_at is not None
    ]


def _build_backfill_context(
    selected: list[ClipWithSegment],
    *,
    config: PipelineConfig,
    temporal_window: float,
    occupied_moments: list[datetime],
) -> _BackfillContext:
    """Summarize the changing selection state for candidate evaluation."""
    from immich_memories.api.models import AssetType

    return _BackfillContext(
        config=config,
        selected_count=len(selected),
        photo_count=sum(1 for item in selected if item.clip.asset.type == AssetType.IMAGE),
        non_favorite_count=sum(1 for item in selected if not item.clip.asset.is_favorite),
        temporal_window=temporal_window,
        occupied_moments=occupied_moments,
    )


def _log_backfill_resolution(
    resolved: _BackfillCandidates,
    *,
    original_photo_limit: float,
    remaining: float,
    logged_tiers: set[str],
) -> set[str]:
    """Log each progressive relaxation at most once per selection."""
    if not resolved.items or resolved.tier == "strict" or resolved.tier in logged_tiers:
        return logged_tiers

    messages = {
        "photo_ratio_70": (
            "relaxing photo ratio from %.0f%% to 70%%",
            (original_photo_limit * 100,),
        ),
        "favorite_ratio": ("allowing additional non-favorites", ()),
        "temporal_spacing": ("allowing a nearby moment", ()),
        "photo_ratio_unlimited": ("allowing photos beyond the ratio cap", ()),
        "bounded_overrun": ("accepting up to 2.0s runtime overrun", ()),
    }
    message, args = messages[resolved.tier]
    logger.info("Post-filter backfill: %s to fill %.1fs gap", message % args, remaining)
    logged_tiers.add(resolved.tier)
    return logged_tiers


def _log_backfill_summary(
    *,
    backfilled: int,
    initial_duration: float,
    total_duration: float,
    max_duration: float,
) -> None:
    """Log the aggregate duration result after the backfill loop."""
    if backfilled:
        logger.info(
            "Post-filter backfill: added %d leftover clips (%.1fs → %.1fs of %.1fs)",
            backfilled,
            initial_duration,
            total_duration,
            max_duration,
        )
    elif max_duration - total_duration > 0.5:
        logger.info(
            "Post-filter backfill: no valid leftover fits the remaining %.1fs",
            max_duration - total_duration,
        )
