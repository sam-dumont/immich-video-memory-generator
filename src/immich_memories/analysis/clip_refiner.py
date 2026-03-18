"""Refinement service for the smart pipeline.

Handles Phase 4: selecting, distributing, and refining the final clip selection.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.clip_scaler import ClipScaler
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
    from immich_memories.api.models import VideoClipInfo

logger = logging.getLogger(__name__)


def enforce_photo_cap(
    clips: list[ClipWithSegment],
    max_ratio: float,
) -> list[ClipWithSegment]:
    """Drop lowest-scored photos until photo ratio <= max_ratio.

    Videos are never dropped. If only photos exist (no videos),
    all are kept since the ratio can't be improved by dropping.
    """
    from immich_memories.api.models import AssetType

    videos = [c for c in clips if c.clip.asset.type != AssetType.IMAGE]
    photos = [c for c in clips if c.clip.asset.type == AssetType.IMAGE]

    if not photos or not videos:
        # No photos to cap, or no videos to establish ratio against
        return clips

    max_photos = int(len(clips) * max_ratio)

    if len(photos) <= max_photos:
        return clips

    # Keep highest-scored photos
    photos.sort(key=lambda c: c.score, reverse=True)
    kept_photos = photos[:max_photos]

    logger.info(
        f"Photo cap: {len(photos)} → {len(kept_photos)} photos "
        f"({max_ratio:.0%} of {len(clips)} total)"
    )

    return videos + kept_photos


class ClipRefiner:
    """Selects, distributes, and refines the final clip selection."""

    def __init__(self, config: PipelineConfig, scaler: ClipScaler):
        self.config = config
        self.scaler = scaler

    def _detect_density_hotspots(
        self,
        favorites_by_week: dict[str, int],
    ) -> dict[str, float]:
        """Detect weeks with unusually high favorites density.

        Returns boost multiplier for each week based on relative favorites concentration.
        """
        if not favorites_by_week:
            return {}

        total_favorites = sum(favorites_by_week.values())
        num_weeks = len(favorites_by_week)

        if 0 in (total_favorites, num_weeks):
            return {}

        avg_favorites = total_favorites / num_weeks

        boosts = {}
        for week, fav_count in favorites_by_week.items():
            if fav_count == 0:
                boosts[week] = 1.0
                continue

            ratio = fav_count / max(avg_favorites, 0.5)

            if ratio >= 4.0:
                boosts[week] = 3.0
            elif ratio >= 2.5:
                boosts[week] = 2.0
            elif ratio >= 1.5:
                boosts[week] = 1.5
            else:
                boosts[week] = 1.0

        hotspots = [(w, b) for w, b in boosts.items() if b > 1.0]
        if hotspots:
            logger.info(f"Detected {len(hotspots)} density hotspots: {hotspots[:5]}...")

        return boosts

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

    def select_clips_distributed_by_date(
        self,
        clips: list[ClipWithSegment],
        target_count: int,
    ) -> list[ClipWithSegment]:
        """Select clips using density-aware favorites-first approach."""
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
            non_favorites.sort(key=lambda c: c.score, reverse=True)
            return non_favorites[:target_count]

        _favorites_by_week, protected_weeks = self._classify_favorites_by_week(favorites)

        selected_favorites = favorites.copy()
        selected_ids: set[str] = {c.clip.asset.id for c in favorites}
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

    def phase_refine(
        self,
        analyzed: list[ClipWithSegment],
        tracker: object,
    ) -> object:
        """Phase 4: Refine final selection."""
        from immich_memories.analysis.progress import PipelinePhase
        from immich_memories.analysis.smart_pipeline import PipelineResult

        tracker.start_phase(PipelinePhase.REFINING, 1)
        tracker.start_item("Refining selection")

        target_with_buffer = int(self.config.target_clips * 1.2)

        if self.config.overnight_bases:
            selected = self.select_clips_by_trip_segments(analyzed, target_with_buffer)
        else:
            selected = self.select_clips_distributed_by_date(analyzed, target_with_buffer)

        target_duration = self.config.target_clips * self.config.avg_clip_duration
        selected = self.scaler.scale_to_target_duration(selected, target_duration)

        if self.config.temporal_dedup_window_minutes > 0:
            selected = self.scaler.deduplicate_temporal_clusters(
                selected, time_window_minutes=self.config.temporal_dedup_window_minutes
            )

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
        if self.config.photo_max_ratio < 1.0:
            selected = enforce_photo_cap(selected, self.config.photo_max_ratio)

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
                "total_analyzed": len(analyzed),
                "selected_count": len(selected_clips),
                "error_count": len(errors),
                "elapsed_seconds": tracker.progress.elapsed_seconds,
            },
        )
