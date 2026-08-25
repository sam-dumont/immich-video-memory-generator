"""Refinement service for the smart pipeline.

Handles Phase 4: selecting, distributing, and refining the final clip selection.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.clip_scaler import ClipScaler
    from immich_memories.analysis.selection_structure import StructurePass
    from immich_memories.analysis.smart_pipeline import (
        ClipWithSegment,
        PipelineConfig,
        PipelineResult,
    )
    from immich_memories.api.models import VideoClipInfo

from immich_memories.analysis.arithmetic_funnel import cap_ratios, narrow_by_arithmetic
from immich_memories.analysis.clip_backfill import (
    _build_backfill_context,
    _choose_backfill_candidate,
    _hold_best_clips_longer,
    _initial_backfill_photo_limit,
    _log_backfill_resolution,
    _log_backfill_summary,
    _occupied_moments,
    _resolve_backfill_candidates,
)
from immich_memories.analysis.clip_distribution import (
    _EVENT_CLIPS_PER_PERIOD,
    _event_periods_of,
    _fill_gap_periods,
    _period_key,
    moment_window_for,
    span_days_of,
    spread_across_moments,
)
from immich_memories.analysis.favourite_law import let_the_favourite_win

logger = logging.getLogger(__name__)

# No single moment may supply more than this share of a cut.
_MAX_SHARE_FROM_ONE_MOMENT = 0.25


def _clips_per_moment(target_clips: int, moments: int) -> int:
    """How many clips one moment may contribute.

    One suits a sixty-second month. A long memory thinned to one per moment
    could not fill its runtime, and backfill restored the same clips by
    relaxing constraints — so the rule scales with what the cut needs. Never
    more than a quarter of it from a single moment, which is what keeps a
    deduplicated slot from being refilled by its own duplicate.

    Scarce moments, not merely fewer of them. Selection is already thinned to
    about the target count by the time dedup runs, so `moments < target_clips`
    holds for nearly every memory, and ceil() alone therefore handed every
    memory two per moment: a real December shipped an eight-clip month with
    the same group photographed twice, two minutes apart. A moment doubles up
    only when there are at most half as many as the cut needs clips — the
    967-asset trip had 16 against a target of 55.
    """
    if target_clips <= 0 or moments <= 0:
        return 1
    if moments * 2 > target_clips:
        return 1
    share = math.ceil(target_clips / moments)
    return max(1, min(share, int(target_clips * _MAX_SHARE_FROM_ONE_MOMENT)))


def _warn_if_an_absorber_will_have_to_act(
    selected: list[ClipWithSegment], target_duration: float
) -> None:
    """Say so when generation will have to fix this cut rather than render it.

    Two absorbers sit below selection and neither of them says anything. The
    stride sampler in apply_final_content_budget keeps only every nth clip once
    the cut holds more than the budget can give a minimum-length slot; the
    proportional trim beside it shortens every clip when the content runs long.
    Both turn a selection problem into a render nobody can explain, so the
    trace names the condition while the cut is still readable.

    Measured against the content budget selection was given, which is the
    closest thing available here to the one generation will plan with — the
    title cards come out of it later, so this errs on the permissive side.
    """
    # One definition, imported where it is used: the sampler's floor lives with
    # the sampler. A copy here would drift from the number actually applied.
    from immich_memories.analysis import selection_trace as trace
    from immich_memories.generate_clips import MIN_CLIP_DURATION

    if target_duration <= 0 or not selected:
        return
    slots = max(1, int(target_duration // MIN_CLIP_DURATION))
    if len(selected) > slots:
        trace.warn(
            f"the cut hands generation {len(selected)} clips against a "
            f"{target_duration:.0f}s budget — the stride sampler will drop every "
            "other one before it renders"
        )
    total = sum(item.end_time - item.start_time for item in selected)
    # A hair of tolerance: backfill fills to exactly the ceiling on the
    # arithmetic path, and float noise there is not an overrun.
    if total - target_duration * 1.1 > 0.05:
        trace.warn(
            f"the cut hands generation {total:.0f}s of content against a "
            f"{target_duration:.0f}s budget — every clip will be trimmed to fit"
        )


class ClipRefiner:
    """Selects, distributes, and refines the final clip selection."""

    def __init__(
        self,
        config: PipelineConfig,
        scaler: ClipScaler,
        *,
        structure: StructurePass | None = None,
    ):
        self.config = config
        self.scaler = scaler
        # The editor that decides which moments the story needs (#764). Absent
        # — no LLM configured — or unable to answer, the arithmetic funnel
        # makes the cut instead, exactly as it always has.
        self._structure = structure
        # What dedup has refused this run. Backfill may not spend freed seconds
        # on it, however far its relaxation ladder goes.
        self._refused_by_dedup: set[str] = set()

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
        # An event's several slots go to several of its moments. Taking the
        # top N by score gave them all to one instant, and coverage protection
        # then forbade dedup from noticing.
        window = moment_window_for(span_days_of(clips), self.config.temporal_dedup_window_minutes)
        for period in densest_first[:reserved]:
            take = _EVENT_CLIPS_PER_PERIOD if period in events else 1
            for best in spread_across_moments(by_period[period], take, window):
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

    def _remember_refusals(
        self, before: list[ClipWithSegment], after: list[ClipWithSegment]
    ) -> None:
        """Note what a stage removed, so backfill cannot spend seconds on it.

        Dedup cutting a set to 12 and backfill rebuilding it to 14 out of the
        same near-duplicates is the loop this closes.
        """
        kept = {item.clip.asset.id for item in after}
        self._refused_by_dedup |= {
            item.clip.asset.id for item in before if item.clip.asset.id not in kept
        }

    def _backfill_to_duration(
        self,
        selected: list[ClipWithSegment],
        candidates: list[ClipWithSegment],
        max_duration: float,
        *,
        photo_cap_bypassed: bool,
        temporal_window: float,
    ) -> list[ClipWithSegment]:
        """Fill post-filter duration holes from unused, constraint-safe candidates.

        Never from clips an earlier stage refused. Freed seconds go to the
        next-ranked candidate, and when nothing admissible is left the memory
        simply runs shorter — a cut four seconds under target beats one padded
        with the near-duplicate dedup had just removed.
        """

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
                refused_ids=frozenset(self._refused_by_dedup),
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

        # Nothing admissible is left, and the cut is still short. Hold the
        # strongest clips a little longer rather than reaching for whatever
        # remains — a weak clip nobody would choose is a worse answer to four
        # missing seconds than two extra seconds of the best one.
        gap = max_duration - total_duration
        if gap > 0.5:
            gained, held = _hold_best_clips_longer(selected, gap)
            total_duration += gained
            if gained > 0.05:
                logger.info(
                    "Held %d strong clip(s) %.1fs longer rather than padding the cut",
                    held,
                    gained,
                )

        # And if it is still short, let it be short. Below this the material
        # genuinely could not fill the runtime, and a recap that ends early
        # beats one padded with clips that earned no place in it.
        shortfall = max_duration - total_duration
        if shortfall > 0.5:
            logger.info(
                "Finishing %.0fs short of %.0fs: the pool had nothing better to add",
                shortfall,
                max_duration,
            )

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
        from immich_memories.analysis.selection_coverage import coverage_of
        from immich_memories.analysis.smart_pipeline import PipelineResult

        tracker.start_phase(PipelinePhase.REFINING, 1)
        tracker.start_item("Refining selection")

        # Prefer two photos/day during initial selection, while retaining the
        # overflow as a fallback if the diverse pool cannot fill the target.
        all_analyzed = analyzed
        # A moment is relative to the story: five minutes inside a month, an
        # evening inside a year.
        moment_window = moment_window_for(
            span_days_of(all_analyzed), self.config.temporal_dedup_window_minutes
        )
        target_duration = self.config.duration_target
        max_overrun = (
            0.0 if self.config.target_duration_seconds is not None else target_duration * 0.10
        )

        structure = (
            self._structure.choose(
                all_analyzed,
                target_duration=target_duration,
                moment_window=moment_window,
                target_clips=self.config.target_clips,
            )
            if self._structure is not None
            else None
        )
        if structure is not None:
            # A condemned moment's members must never come back through the
            # relaxation ladder, whichever stage settles the length.
            self._refused_by_dedup |= set(structure.dropped)
        # The story settles the length only when it said what to give up first.
        # Otherwise the counting stages narrow what it kept — the same chain,
        # over a smaller pool, so the dedup, the caps, backfill and the
        # favourites law below all still run over the result.
        by_arithmetic = structure is None or not structure.narrowed
        if by_arithmetic:
            narrowed = narrow_by_arithmetic(
                self,
                all_analyzed if structure is None else structure.kept,
                target_duration=target_duration,
                max_overrun=max_overrun,
            )
            analyzed = narrowed.analyzed
            selected = narrowed.selected
            coverage_ids = narrowed.coverage_ids
        elif structure is not None:
            # `elif` only to re-narrow the type for mypy: by_arithmetic is
            # False exactly when structure is a cut that settled its own
            # length, so there is no third branch to reach.
            analyzed, selected, coverage_ids = all_analyzed, structure.kept, set()

        if moment_window > 0:
            before_dedup = selected
            # How many clips one moment may keep depends on how many the cut
            # needs: thinning to one left a long memory too short to fill, and
            # backfill then restored the same clips by relaxing constraints.
            from immich_memories.analysis.clip_scaler import group_by_moment

            moments = len(group_by_moment(selected, moment_window))
            selected = self.scaler.deduplicate_temporal_clusters(
                selected,
                time_window_minutes=moment_window,
                keep_per_moment=_clips_per_moment(self.config.target_clips, moments),
                protected_ids=coverage_ids,
            )
            trace.record("same-moment dedup", before_dedup, selected)
            self._remember_refusals(before_dedup, selected)

            # The clock is a proxy for "the same thing" and it fails both ways.
            # This asks what the clips show, from descriptions already cached.
            before_content = selected
            selected = self.scaler.deduplicate_by_content(selected, protected_ids=coverage_ids)
            trace.record("same-thing dedup", before_content, selected)
            self._remember_refusals(before_content, selected)

        photo_cap_bypassed = False
        if by_arithmetic:
            selected, photo_cap_bypassed = cap_ratios(
                self.config,
                selected,
                analyzed,
                coverage_ids=coverage_ids,
                target_duration=target_duration,
            )

        before_backfill = selected
        selected = self._backfill_to_duration(
            selected,
            all_analyzed,
            target_duration + max_overrun,
            photo_cap_bypassed=photo_cap_bypassed,
            temporal_window=moment_window,
        )
        trace.record("duration backfill", before_backfill, selected)

        # Last, and once: every stage above can drop a favourite while a
        # neighbour from the same moment survives, each for its own good
        # reason. The rule they all answer to is applied to what they produced.
        before_law = selected
        # Structure made an editorial rejection, not a mechanical drop for the
        # law to repair. Its grouping can differ from the law's, so exclusion
        # by id is what keeps a condemned moment condemned.
        favourite_pool = (
            [item for item in all_analyzed if item.clip.asset.id not in structure.dropped]
            if structure is not None
            else all_analyzed
        )
        selected = let_the_favourite_win(selected, favourite_pool)
        trace.record("the favourite wins its moment", before_law, selected)

        selected.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        _warn_if_an_absorber_will_have_to_act(selected, target_duration)

        clip_segments: dict[str, tuple[float, float]] = {}
        selected_clips: list[VideoClipInfo] = []

        for item in selected:
            selected_clips.append(item.clip)
            clip_segments[item.clip.asset.id] = (item.start_time, item.end_time)

        errors = [{"clip_id": e.item_id, "error": e.error} for e in tracker.progress.errors]

        tracker.complete_item("selection")
        tracker.complete_phase()

        logger.info(f"Phase 4: Final selection of {len(selected_clips)} clips")

        coverage = coverage_of(all_analyzed)
        trace.record_coverage(coverage)

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
            coverage=coverage,
        )
