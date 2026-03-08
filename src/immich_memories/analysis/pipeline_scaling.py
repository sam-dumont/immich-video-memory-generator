"""Scaling and temporal deduplication mixin for the smart pipeline.

Contains methods for scaling clip selections to target durations
and removing temporal duplicates.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)


class ScalingMixin:
    """Mixin providing scaling and temporal deduplication methods for SmartPipeline."""

    def _scale_to_target_duration(
        self,
        clips: list[ClipWithSegment],
        target_duration: float,
    ) -> list[ClipWithSegment]:
        """Scale down selection to fit target duration.

        Process:
        1. Calculate actual total duration from segments
        2. If under target + 10%, return as-is
        3. If over: remove lowest-scored clips (non-favorites first)

        Note: Does NOT trim individual segments - that's handled in unified_analyzer.

        Args:
            clips: Selected clips with segments.
            target_duration: Target total duration in seconds.

        Returns:
            Clips that fit within target duration.
        """
        if not clips:
            return clips

        # Calculate current total duration
        total = sum(c.end_time - c.start_time for c in clips)
        max_allowed = target_duration * 1.10  # Allow 10% over

        if total <= max_allowed:
            logger.info(f"Duration {total:.0f}s within target {target_duration:.0f}s (+10%)")
            return clips

        logger.info(
            f"Duration {total:.0f}s exceeds target {target_duration:.0f}s, removing clips..."
        )

        # Protect high-density weeks and first/last weeks
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

        # Also protect first/last weeks
        sorted_weeks = sorted(favorites_by_week.keys())
        if sorted_weeks:
            protected_weeks.add(sorted_weeks[0])
            protected_weeks.add(sorted_weeks[-1])

        logger.debug(f"Protected weeks: {sorted(protected_weeks)}")

        # Sort by removability: non-favorites first, then low-score favorites from non-protected weeks
        def removability_score(c: ClipWithSegment) -> tuple:
            is_fav = c.clip.asset.is_favorite
            week = c.clip.asset.file_created_at.strftime("%Y-W%W")
            is_protected = week in protected_weeks
            return (is_fav, is_protected, c.score)

        clips_sorted = sorted(clips, key=removability_score)

        # Remove from most removable until under budget
        result = []
        running_total = 0.0
        removed_count = 0

        for c in reversed(clips_sorted):  # Start from least removable
            clip_duration = c.end_time - c.start_time
            if running_total + clip_duration <= max_allowed:
                result.append(c)
                running_total += clip_duration
            else:
                removed_count += 1
                logger.debug(
                    f"Removing {c.clip.asset.original_file_name or c.clip.asset.id[:8]} "
                    f"({clip_duration:.1f}s, score={c.score:.2f}, fav={c.clip.asset.is_favorite})"
                )

        # Sort back by date for chronological order
        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        logger.info(
            f"Removed {removed_count} clips, final duration: {running_total:.0f}s ({len(result)} clips)"
        )
        return result

    def _deduplicate_temporal_clusters(
        self,
        clips: list[ClipWithSegment],
        time_window_minutes: float = 10.0,
    ) -> list[ClipWithSegment]:
        """Remove near-duplicate clips from the same time period.

        When multiple favorites are taken within the same time window (e.g., 10 minutes),
        they're likely capturing the same moment from slightly different angles or times.
        Keep only the best-scored one to allow more diverse content.

        Args:
            clips: Selected clips with segments and scores.
            time_window_minutes: Time window in minutes to consider as "same moment".

        Returns:
            Deduplicated clips with best representative per time cluster.
        """
        if not clips:
            return clips

        # Group clips by time buckets
        time_clusters: dict[str, list[ClipWithSegment]] = defaultdict(list)

        for clip in clips:
            timestamp = clip.clip.asset.file_created_at
            # Create time bucket key (round to nearest time_window)
            bucket_minutes = int(timestamp.timestamp() / 60 / time_window_minutes)
            time_key = f"{timestamp.date()}_{bucket_minutes}"
            time_clusters[time_key].append(clip)

        result = []
        removed_count = 0
        clusters_with_duplicates = 0

        for time_key, cluster_clips in time_clusters.items():
            if len(cluster_clips) == 1:
                result.append(cluster_clips[0])
                continue

            # Multiple clips in same time window
            favorites_in_cluster = [c for c in cluster_clips if c.clip.asset.is_favorite]
            non_favorites_in_cluster = [c for c in cluster_clips if not c.clip.asset.is_favorite]

            if len(favorites_in_cluster) >= 1:
                # At least one favorite in cluster - keep only the best favorite
                # Remove ALL other clips (extra favorites AND non-favorites from same moment)
                favorites_in_cluster.sort(key=lambda c: c.score, reverse=True)
                best = favorites_in_cluster[0]

                result.append(best)

                # Count what we're removing
                removed_favorites = len(favorites_in_cluster) - 1
                removed_non_favorites = len(non_favorites_in_cluster)
                total_removed = removed_favorites + removed_non_favorites

                if total_removed > 0:
                    removed_count += total_removed
                    clusters_with_duplicates += 1

                    logger.debug(
                        f"Temporal cluster {time_key}: keeping favorite {best.clip.asset.original_file_name} "
                        f"(score={best.score:.2f}), removing {removed_favorites} favorites + "
                        f"{removed_non_favorites} non-favorites"
                    )
            else:
                # No favorites in cluster - keep only the best non-favorite
                if len(non_favorites_in_cluster) > 1:
                    non_favorites_in_cluster.sort(key=lambda c: c.score, reverse=True)
                    best = non_favorites_in_cluster[0]
                    result.append(best)
                    removed_count += len(non_favorites_in_cluster) - 1
                    clusters_with_duplicates += 1

                    logger.debug(
                        f"Temporal cluster {time_key}: keeping non-favorite {best.clip.asset.original_file_name} "
                        f"(score={best.score:.2f}), removing {len(non_favorites_in_cluster) - 1} duplicates"
                    )
                else:
                    # Only 1 non-favorite, keep it
                    result.extend(non_favorites_in_cluster)

        if removed_count > 0:
            logger.info(
                f"Temporal deduplication: removed {removed_count} clips from same-moment clusters "
                f"from {clusters_with_duplicates} time clusters (window={time_window_minutes:.0f}min)"
            )

        # Sort by date
        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        return result
