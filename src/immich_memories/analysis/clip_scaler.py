"""Scaling and temporal deduplication service for the smart pipeline."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)


class ClipScaler:
    """Scales clip selections to target durations and removes temporal duplicates."""

    def scale_to_target_duration(
        self,
        clips: list[ClipWithSegment],
        target_duration: float,
    ) -> list[ClipWithSegment]:
        """Scale down selection to fit target duration.

        Removes lowest-scored clips (non-favorites first) until total
        duration is within target + 10%.
        """
        if not clips:
            return clips

        total = sum(c.end_time - c.start_time for c in clips)
        max_allowed = target_duration * 1.10

        if total <= max_allowed:
            logger.info(f"Duration {total:.0f}s within target {target_duration:.0f}s (+10%)")
            return clips

        logger.info(
            f"Duration {total:.0f}s exceeds target {target_duration:.0f}s, removing clips..."
        )

        favorites_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for c in clips:
            if c.clip.asset.is_favorite:
                week = c.clip.asset.file_created_at.strftime("%Y-W%W")
                favorites_by_week[week].append(c)

        num_favorite_weeks = len(favorites_by_week)
        if num_favorite_weeks > 0:
            avg_per_week = len([c for c in clips if c.clip.asset.is_favorite]) / num_favorite_weeks
            protected_weeks = {
                w
                for w, clips_list in favorites_by_week.items()
                if len(clips_list) >= avg_per_week * 1.5
            }
        else:
            protected_weeks = set()

        sorted_weeks = sorted(favorites_by_week.keys())
        if sorted_weeks:
            protected_weeks.add(sorted_weeks[0])
            protected_weeks.add(sorted_weeks[-1])

        logger.debug(f"Protected weeks: {sorted(protected_weeks)}")

        def removability_score(c: ClipWithSegment) -> tuple:
            is_fav = c.clip.asset.is_favorite
            week = c.clip.asset.file_created_at.strftime("%Y-W%W")
            is_protected = week in protected_weeks
            return (is_fav, is_protected, c.score)

        clips_sorted = sorted(clips, key=removability_score)

        result = []
        running_total = 0.0
        removed_count = 0

        for c in reversed(clips_sorted):
            clip_duration = c.end_time - c.start_time
            if running_total + clip_duration <= max_allowed:
                result.append(c)
                running_total += clip_duration
            else:
                removed_count += 1
                logger.debug(
                    f"Removing {c.clip.asset.original_file_name or c.clip.asset.id[:8]} "
                    f"({clip_duration:.1f}s, score={c.score:.2f}, "
                    f"fav={c.clip.asset.is_favorite})"
                )

        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        logger.info(
            f"Removed {removed_count} clips, final duration: "
            f"{running_total:.0f}s ({len(result)} clips)"
        )
        return result

    def _pick_best_from_cluster(
        self,
        time_key: str,
        cluster_clips: list[ClipWithSegment],
    ) -> tuple[ClipWithSegment, int]:
        """Return (best_clip, num_removed) for a temporal cluster of 2+ clips."""
        favorites = [c for c in cluster_clips if c.clip.asset.is_favorite]
        non_favorites = [c for c in cluster_clips if not c.clip.asset.is_favorite]

        if favorites:
            favorites.sort(key=lambda c: c.score, reverse=True)
            best = favorites[0]
            removed = len(favorites) - 1 + len(non_favorites)
            if removed > 0:
                logger.debug(
                    f"Temporal cluster {time_key}: keeping favorite "
                    f"{best.clip.asset.original_file_name} "
                    f"(score={best.score:.2f}), removing {len(favorites) - 1} fav "
                    f"+ {len(non_favorites)} non-fav"
                )
            return best, removed

        non_favorites.sort(key=lambda c: c.score, reverse=True)
        best = non_favorites[0]
        removed = len(non_favorites) - 1
        if removed > 0:
            logger.debug(
                f"Temporal cluster {time_key}: keeping non-favorite "
                f"{best.clip.asset.original_file_name} "
                f"(score={best.score:.2f}), removing {removed} duplicates"
            )
        return best, removed

    def deduplicate_temporal_clusters(
        self,
        clips: list[ClipWithSegment],
        time_window_minutes: float = 10.0,
    ) -> list[ClipWithSegment]:
        """Remove near-duplicate clips from the same time period.

        Keeps only the best-scored clip per time window bucket.
        """
        if not clips:
            return clips

        time_clusters: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for clip in clips:
            timestamp = clip.clip.asset.file_created_at
            bucket_minutes = int(timestamp.timestamp() / 60 / time_window_minutes)
            time_clusters[f"{timestamp.date()}_{bucket_minutes}"].append(clip)

        result = []
        removed_count = 0
        clusters_with_duplicates = 0

        for time_key, cluster_clips in time_clusters.items():
            if len(cluster_clips) == 1:
                result.append(cluster_clips[0])
                continue

            best, removed = self._pick_best_from_cluster(time_key, cluster_clips)
            result.append(best)
            if removed > 0:
                removed_count += removed
                clusters_with_duplicates += 1

        if removed_count > 0:
            logger.info(
                f"Temporal deduplication: removed {removed_count} clips from "
                f"same-moment clusters from {clusters_with_duplicates} time clusters "
                f"(window={time_window_minutes:.0f}min)"
            )

        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        return result
