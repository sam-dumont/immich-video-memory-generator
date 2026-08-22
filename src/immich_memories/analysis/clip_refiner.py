"""Refinement service for the smart pipeline.

Handles Phase 4: selecting, distributing, and refining the final clip selection.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.clip_scaler import ClipScaler
    from immich_memories.analysis.smart_pipeline import (
        ClipWithSegment,
        PipelineConfig,
        PipelineResult,
    )
    from immich_memories.api.models import VideoClipInfo

from immich_memories.analysis.clip_distribution import (
    _EVENT_CLIPS_PER_PERIOD,
    _event_periods_of,
    _fill_gap_periods,
    _partition_photos_per_day,
    _period_key,
    enforce_photo_cap,
    photos_per_day_for,
)

logger = logging.getLogger(__name__)

# An event is defined by CONTRAST with the rest of the period, not by an
# absolute share: in a flat month every day clears any fixed threshold and the
# tie-break decides who wins, which is arbitrary. Measured on April 2021, the
# two events held 133 and 99 assets against a median day of 5 (#488).
# At most this many periods get the event treatment, so a month recap keeps
# room for the ordinary days that make it a month.


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

    if enforce_temporal_spacing and is_same_moment(
        candidate.clip.asset.file_created_at,
        context.occupied_moments,
        context.temporal_window,
    ):
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


class ClipRefiner:
    """Selects, distributes, and refines the final clip selection."""

    def __init__(self, config: PipelineConfig, scaler: ClipScaler):
        self.config = config
        self.scaler = scaler

    def _select_without_favorites(
        self,
        clips: list[ClipWithSegment],
        non_favorites: list[ClipWithSegment],
        target_count: int,
        event_periods: set[str],
    ) -> list[ClipWithSegment]:
        """Select when the user starred nothing — the common case for an
        older library (#488).

        Ranking alone cannot do this job here: measured on a real recap,
        83% of the pool carried a metadata fallback score and 55% shared one
        identical value, so "top N by score" is largely list order. The
        structure of the month is the only real signal, so periods holding
        the most material are represented first, and the rest of the slots
        go to score.
        """
        dates = [c.clip.asset.file_created_at for c in clips]
        span_days = (max(dates) - min(dates)).days if dates else 0

        by_period: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for c in non_favorites:
            by_period[_period_key(c.clip.asset.file_created_at, span_days)].append(c)

        densest_first = sorted(by_period, key=lambda k: (-len(by_period[k]), k))
        reserved = max(1, target_count // 2)

        selected: list[ClipWithSegment] = []
        selected_ids: set[str] = set()
        coverage_ids: set[str] = set()
        events = event_periods
        for period in densest_first[:reserved]:
            take = _EVENT_CLIPS_PER_PERIOD if period in events else 1
            ranked = sorted(by_period[period], key=lambda c: c.score, reverse=True)
            for best in ranked[:take]:
                selected.append(best)
                selected_ids.add(best.clip.asset.id)
                coverage_ids.add(best.clip.asset.id)

        for c in sorted(non_favorites, key=lambda c: c.score, reverse=True):
            if len(selected) >= target_count:
                break
            if c.clip.asset.id not in selected_ids:
                selected.append(c)
                selected_ids.add(c.clip.asset.id)

        self._coverage_ids = coverage_ids
        selected.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        logger.info(
            f"No favorites: {len(coverage_ids)} clips from the densest periods "
            f"+ {len(selected) - len(coverage_ids)} by score"
        )
        return selected

    def _classify_favorites_by_week(
        self,
        favorites: list[ClipWithSegment],
    ) -> tuple[dict[str, list[ClipWithSegment]], set[str]]:
        """Classify favorites by week and identify protected weeks."""
        favorites_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for fav in favorites:
            week_key = fav.clip.asset.file_created_at.strftime("%Y-W%W")
            favorites_by_week[week_key].append(fav)

        sorted_weeks = sorted(favorites_by_week.keys())
        num_weeks = len(sorted_weeks)
        avg_per_week = len(favorites) / max(num_weeks, 1)

        logger.info(
            f"Density: {len(favorites)} favorites across {num_weeks} weeks "
            f"(avg {avg_per_week:.1f}/week)"
        )

        high_density_weeks: set[str] = set()
        for week, week_favs in favorites_by_week.items():
            if len(week_favs) >= avg_per_week * 1.5:
                high_density_weeks.add(week)

        special_weeks: set[str] = set()
        if sorted_weeks:
            special_weeks.add(sorted_weeks[0])
            special_weeks.add(sorted_weeks[-1])

        if self.config.birthday_month:
            for fav in favorites:
                fav_date = fav.clip.asset.file_created_at
                if fav_date.month == self.config.birthday_month and abs(fav_date.day - 7) <= 10:
                    birthday_week = fav_date.strftime("%Y-W%W")
                    special_weeks.add(birthday_week)
                    high_density_weeks.add(birthday_week)
                    logger.info(f"Birthday week: {birthday_week}")
                    break

        protected_weeks = high_density_weeks | special_weeks
        logger.info(f"Protected weeks (high density + special): {sorted(protected_weeks)}")

        return favorites_by_week, protected_weeks

    def _scale_down_favorites(
        self,
        selected_favorites: list[ClipWithSegment],
        selected_ids: set[str],
        protected_weeks: set[str],
        max_duration: float,
    ) -> list[ClipWithSegment]:
        """Scale down favorites to fit within duration budget."""
        total_duration = sum(c.end_time - c.start_time for c in selected_favorites)
        if total_duration <= max_duration:
            return selected_favorites

        logger.info(
            f"Duration {total_duration:.0f}s exceeds max {max_duration:.0f}s, "
            f"scaling down favorites from low-density weeks..."
        )

        removable: list[ClipWithSegment] = []
        protected: list[ClipWithSegment] = []

        for clip in selected_favorites:
            week = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            if week in protected_weeks:
                protected.append(clip)
            else:
                removable.append(clip)

        removable.sort(key=lambda c: c.score)

        removed_count = 0
        while total_duration > max_duration and removable:
            candidate = removable[0]
            candidate_week = candidate.clip.asset.file_created_at.strftime("%Y-W%W")

            week_count = sum(
                1
                for c in removable
                if c.clip.asset.file_created_at.strftime("%Y-W%W") == candidate_week
            )
            if week_count <= 1:
                protected.append(removable.pop(0))
                continue

            removed = removable.pop(0)
            total_duration -= removed.end_time - removed.start_time
            selected_ids.discard(removed.clip.asset.id)
            removed_count += 1

        logger.info(
            f"Scaled down: removed {removed_count} favorites, "
            f"kept {len(protected) + len(removable)}"
        )
        return protected + removable

    def _fill_empty_weeks(
        self,
        selected_favorites: list[ClipWithSegment],
        non_favorites: list[ClipWithSegment],
        favorites: list[ClipWithSegment],
        selected_ids: set[str],
    ) -> list[ClipWithSegment]:
        """Fill weeks with no selected clips using non-favorites."""
        from datetime import timedelta

        non_favs_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for clip in non_favorites:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            non_favs_by_week[week_key].append(clip)

        selected_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for clip in selected_favorites:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            selected_by_week[week_key].append(clip)

        first_date = favorites[0].clip.asset.file_created_at
        last_date = favorites[-1].clip.asset.file_created_at

        all_weeks_in_range: list[str] = []
        current = first_date
        while current <= last_date:
            week_key = current.strftime("%Y-W%W")
            if week_key not in all_weeks_in_range:
                all_weeks_in_range.append(week_key)
            current += timedelta(days=7)

        gap_fillers: list[ClipWithSegment] = []
        for week in all_weeks_in_range:
            if len(selected_by_week.get(week, [])) == 0:
                week_non_favs = non_favs_by_week.get(week, [])
                if week_non_favs:
                    week_non_favs.sort(key=lambda c: c.score, reverse=True)
                    for clip in week_non_favs[:2]:
                        if clip.clip.asset.id not in selected_ids:
                            gap_fillers.append(clip)
                            selected_ids.add(clip.clip.asset.id)

        logger.info(f"Added {len(gap_fillers)} gap-fillers from non-favorites")
        return gap_fillers

    def _fill_remaining_slots(
        self,
        selected: list[ClipWithSegment],
        non_favorites: list[ClipWithSegment],
        target_count: int,
        selected_ids: set[str],
    ) -> None:
        """Fill remaining slots with distribution-aware non-favorites."""
        remaining_slots = target_count - len(selected)
        if remaining_slots <= 0:
            return

        clips_per_week: defaultdict[str, int] = defaultdict(int)
        for clip in selected:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            clips_per_week[week_key] += 1

        remaining_non_favs = [c for c in non_favorites if c.clip.asset.id not in selected_ids]
        distribution_scores: dict[str, float] = {}
        for clip in remaining_non_favs:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            existing = clips_per_week.get(week_key, 0)
            distribution_scores[clip.clip.asset.id] = clip.score - (existing * 0.1)

        remaining_non_favs.sort(key=lambda c: distribution_scores[c.clip.asset.id], reverse=True)

        for clip in remaining_non_favs[:remaining_slots]:
            selected.append(clip)
            selected_ids.add(clip.clip.asset.id)

        logger.info(
            f"Added {min(remaining_slots, len(remaining_non_favs))} additional non-favorites"
        )

    def _ensure_temporal_coverage(
        self,
        selected: list[ClipWithSegment],
        all_clips: list[ClipWithSegment],
        selected_ids: set[str],
        event_periods: set[str] | None = None,
    ) -> list[ClipWithSegment]:
        """Guarantee at least 1 clip per time period across the full date range.

        Adaptive granularity: daily for ≤1 month, weekly for ≤3 months,
        monthly for ≤1 year, quarterly for >1 year.
        """
        if not all_clips:
            return []

        dates = [c.clip.asset.file_created_at for c in all_clips]
        span_days = (max(dates) - min(dates)).days

        covered = {_period_key(c.clip.asset.file_created_at, span_days) for c in selected}

        unselected_by_period: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for c in all_clips:
            if c.clip.asset.id not in selected_ids:
                key = _period_key(c.clip.asset.file_created_at, span_days)
                unselected_by_period[key].append(c)

        gap_fillers = _fill_gap_periods(
            unselected_by_period,
            covered,
            _event_periods_of(all_clips) if event_periods is None else event_periods,
        )
        selected_ids.update(filler.clip.asset.id for filler in gap_fillers)

        if gap_fillers:
            logger.info(
                f"Temporal coverage: added {len(gap_fillers)} clips "
                f"across {len(covered)} periods, densest first "
                f"(granularity: {span_days}d span)"
            )

        return gap_fillers

    def select_clips_distributed_by_date(
        self,
        clips: list[ClipWithSegment],
        target_count: int,
        event_periods: set[str] | None = None,
    ) -> list[ClipWithSegment]:
        """Select clips using density-aware favorites-first approach.

        event_periods must be measured on the pool before any per-day cap:
        a cap compresses every day into the same narrow range, so a set
        derived here would be reading its own output. Callers that still hold
        the raw pool pass it; otherwise it is derived from `clips`.
        """
        if event_periods is None:
            event_periods = _event_periods_of(clips)
        if not clips:
            return []

        favorites = [c for c in clips if c.clip.asset.is_favorite]
        non_favorites = [c for c in clips if not c.clip.asset.is_favorite]

        favorites.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        non_favorites.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        logger.info(
            f"Distribution input: {len(favorites)} favorites, {len(non_favorites)} non-favorites"
        )

        if not favorites:
            return self._select_without_favorites(clips, non_favorites, target_count, event_periods)

        _favorites_by_week, protected_weeks = self._classify_favorites_by_week(favorites)

        selected_favorites = favorites.copy()
        selected_ids = {c.clip.asset.id for c in favorites}
        logger.info(f"Starting with ALL {len(selected_favorites)} favorites")

        target_duration = target_count * 5.0
        max_duration = target_duration * 1.25
        selected_favorites = self._scale_down_favorites(
            selected_favorites, selected_ids, protected_weeks, max_duration
        )

        gap_fillers = self._fill_empty_weeks(
            selected_favorites, non_favorites, favorites, selected_ids
        )

        selected = selected_favorites + gap_fillers
        self._fill_remaining_slots(selected, non_favorites, target_count, selected_ids)

        # Ensure every time period has at least 1 clip
        coverage = self._ensure_temporal_coverage(selected, clips, selected_ids, event_periods)
        selected.extend(coverage)
        self._coverage_ids = {c.clip.asset.id for c in coverage}

        final_favorites = sum(1 for c in selected if c.clip.asset.is_favorite)
        final_non_favorites = len(selected) - final_favorites
        months_covered = {c.clip.asset.file_created_at.strftime("%Y-%m") for c in selected}

        logger.info(
            f"Final selection: {len(selected)} clips "
            f"({final_favorites} favorites, {final_non_favorites} non-favorites) "
            f"across {len(months_covered)} months"
        )

        return selected

    def select_clips_by_trip_segments(
        self,
        analyzed: list[ClipWithSegment],
        target: int,
    ) -> list[ClipWithSegment]:
        """Select clips proportionally across overnight stop segments."""
        from immich_memories.analysis.trip_detection import (
            distribute_clip_budget,
            tag_clips_to_segments,
        )

        bases = self.config.overnight_bases
        if not bases:
            return analyzed[:target]

        clip_dates = {}
        for c in analyzed:
            dt = c.clip.asset.file_created_at or datetime.min
            clip_dates[c.clip.asset.id] = dt.date()

        tags = tag_clips_to_segments(clip_dates, bases)
        budget = distribute_clip_budget(target, [b.nights for b in bases])

        by_seg: dict[int, list[ClipWithSegment]] = defaultdict(list)
        for c in analyzed:
            seg_idx = tags.get(c.clip.asset.id, 0)
            by_seg[seg_idx].append(c)
        for clips in by_seg.values():
            clips.sort(key=lambda c: c.score, reverse=True)

        selected: list[ClipWithSegment] = []
        for seg_idx in range(len(budget)):
            n = budget[seg_idx]
            selected.extend(by_seg.get(seg_idx, [])[:n])

        logger.info(
            "Trip segment distribution: %s",
            ", ".join(f"seg{i}={budget[i]}" for i in range(len(budget))),
        )
        return selected

    def _backfill_to_duration(
        self,
        selected: list[ClipWithSegment],
        candidates: list[ClipWithSegment],
        max_duration: float,
        *,
        photo_cap_bypassed: bool,
    ) -> list[ClipWithSegment]:
        """Fill post-filter duration holes from unused, constraint-safe candidates."""

        selected_ids = {item.clip.asset.id for item in selected}
        available = [item for item in candidates if item.clip.asset.id not in selected_ids]
        total_duration = sum(item.end_time - item.start_time for item in selected)
        initial_duration = total_duration
        backfilled = 0
        active_photo_limit = _initial_backfill_photo_limit(
            self.config,
            photo_cap_bypassed=photo_cap_bypassed,
        )
        logged_relaxation_tiers: set[str] = set()

        temporal_window = self.config.temporal_dedup_window_minutes
        occupied_moments = _occupied_moments(
            selected,
            temporal_window,
        )

        while available:
            remaining = max_duration - total_duration
            if remaining <= 0:
                break

            context = _build_backfill_context(
                selected,
                config=self.config,
                temporal_window=temporal_window,
                occupied_moments=occupied_moments,
            )
            resolved = _resolve_backfill_candidates(
                available,
                context=context,
                active_photo_limit=active_photo_limit,
                remaining_budget=remaining,
            )
            active_photo_limit = resolved.photo_limit
            logged_relaxation_tiers = _log_backfill_resolution(
                resolved,
                original_photo_limit=self.config.photo_max_ratio,
                remaining=remaining,
                logged_tiers=logged_relaxation_tiers,
            )

            if not resolved.items:
                break

            chosen = _choose_backfill_candidate(
                resolved.items,
                selected_dates=[item.clip.asset.file_created_at for item in selected],
                photo_cap_bypassed=photo_cap_bypassed,
            )

            selected.append(chosen)
            selected_ids.add(chosen.clip.asset.id)
            available.remove(chosen)
            total_duration += chosen.end_time - chosen.start_time
            backfilled += 1
            if temporal_window > 0 and chosen.clip.asset.file_created_at is not None:
                occupied_moments.append(chosen.clip.asset.file_created_at)

        _log_backfill_summary(
            backfilled=backfilled,
            initial_duration=initial_duration,
            total_duration=total_duration,
            max_duration=max_duration,
        )

        return selected

    def phase_refine(
        self,
        analyzed: list[ClipWithSegment],
        tracker: object,
    ) -> PipelineResult:
        """Phase 4: Refine final selection."""
        from immich_memories.analysis import selection_trace as trace
        from immich_memories.analysis.progress import PipelinePhase
        from immich_memories.analysis.smart_pipeline import PipelineResult

        tracker.start_phase(PipelinePhase.REFINING, 1)
        tracker.start_item("Refining selection")

        # Prefer two photos/day during initial selection, while retaining the
        # overflow as a fallback if the diverse pool cannot fill the target.
        all_analyzed = analyzed
        # Measured before the per-day cap flattens every day into the same
        # narrow range (#488).
        event_periods = _event_periods_of(all_analyzed)
        active_days = len(
            {
                c.clip.asset.file_created_at.date()
                for c in all_analyzed
                if c.clip.asset.file_created_at
            }
        )
        analyzed, _photo_overflow = _partition_photos_per_day(
            all_analyzed,
            photos_per_day_for(self.config.target_clips, active_days),
        )
        trace.record("per-day photo cap", all_analyzed, analyzed)

        target_with_buffer = int(self.config.target_clips * 1.2)

        if self.config.overnight_bases:
            selected = self.select_clips_by_trip_segments(analyzed, target_with_buffer)
        else:
            selected = self.select_clips_distributed_by_date(
                analyzed, target_with_buffer, event_periods
            )
        trace.record("distribute by date", analyzed, selected)

        target_duration = self.config.duration_target
        max_overrun = (
            0.0 if self.config.target_duration_seconds is not None else target_duration * 0.10
        )
        coverage_ids: set[str] = getattr(self, "_coverage_ids", set())
        before_scale = selected
        selected = self.scaler.scale_to_target_duration(
            selected,
            target_duration,
            protected_ids=coverage_ids,
            max_overrun_seconds=max_overrun,
        )
        trace.record(f"fit to {target_duration:.0f}s", before_scale, selected)

        if self.config.temporal_dedup_window_minutes > 0:
            before_dedup = selected
            selected = self.scaler.deduplicate_temporal_clusters(
                selected, time_window_minutes=self.config.temporal_dedup_window_minutes
            )
            trace.record("same-moment dedup", before_dedup, selected)

        if self.config.max_non_favorite_ratio < 1.0 and self.config.prioritize_favorites:
            favorites = [c for c in selected if c.clip.asset.is_favorite]
            non_favorites = [c for c in selected if not c.clip.asset.is_favorite]

            max_non_favorites = int(len(selected) * self.config.max_non_favorite_ratio)
            min_non_favorites = max(0, self.config.target_clips - len(favorites))
            max_non_favorites = max(max_non_favorites, min_non_favorites)

            if len(non_favorites) > max_non_favorites:
                non_favorites.sort(key=lambda c: c.score, reverse=True)
                non_favorites = non_favorites[:max_non_favorites]

                logger.info(
                    f"Final selection: limiting non-favorites to {len(non_favorites)} "
                    f"({self.config.max_non_favorite_ratio:.0%} of {len(selected)})"
                )

                selected = favorites + non_favorites

        # Enforce photo ratio cap (drop lowest-scored photos if over limit)
        photo_cap_bypassed = False
        if self.config.photo_max_ratio < 1.0:
            from immich_memories.api.models import AssetType

            # WHY: Scarcity is determined from the available supply, not an
            # already photo-biased selection. Match unified_budget exactly:
            # photos fill freely only when videos cannot fill half the budget.
            available_video_duration = sum(
                c.end_time - c.start_time for c in analyzed if c.clip.asset.type != AssetType.IMAGE
            )
            videos_scarce = available_video_duration < target_duration * 0.5
            photo_cap_bypassed = videos_scarce
            # Even when photos may ultimately fill freely, first normalize a
            # photo-biased selection so backfill has room to use every valid
            # video candidate. The backfill exemption then admits photos again.
            before_cap = selected
            selected = enforce_photo_cap(
                selected,
                self.config.photo_max_ratio,
                videos_scarce=False,
                protected_ids=coverage_ids,
            )
            trace.record("photo ratio cap", before_cap, selected)

        before_backfill = selected
        selected = self._backfill_to_duration(
            selected,
            all_analyzed,
            target_duration + max_overrun,
            photo_cap_bypassed=photo_cap_bypassed,
        )
        trace.record("duration backfill", before_backfill, selected)

        selected.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        clip_segments: dict[str, tuple[float, float]] = {}
        selected_clips: list[VideoClipInfo] = []

        for item in selected:
            selected_clips.append(item.clip)
            clip_segments[item.clip.asset.id] = (item.start_time, item.end_time)

        errors = [{"clip_id": e.item_id, "error": e.error} for e in tracker.progress.errors]

        tracker.complete_item("selection")
        tracker.complete_phase()

        logger.info(f"Phase 4: Final selection of {len(selected_clips)} clips")

        return PipelineResult(
            selected_clips=selected_clips,
            clip_segments=clip_segments,
            errors=errors,
            stats={
                "total_analyzed": len(all_analyzed),
                "selected_count": len(selected_clips),
                "error_count": len(errors),
                "elapsed_seconds": tracker.progress.elapsed_seconds,
            },
        )
