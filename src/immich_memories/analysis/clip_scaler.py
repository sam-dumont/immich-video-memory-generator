"""Scaling and temporal deduplication service for the smart pipeline."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)


def is_same_moment(
    when: datetime | None,
    others: list[datetime],
    time_window_minutes: float,
) -> bool:
    """Is this within the window of a moment already in the cut?

    Distance, not a grid. Bucketing on int(epoch / window) made "the same
    moment" depend on where two shots fell against an arbitrary boundary
    rather than how far apart they were: 15:55 and 15:57 collapsed, while
    15:54 and 15:56 — the same two minutes — both shipped.
    """
    if when is None or time_window_minutes <= 0:
        return False
    window = time_window_minutes * 60
    return any(abs((when - other).total_seconds()) <= window for other in others)


def group_by_moment(
    clips: list[ClipWithSegment],
    time_window_minutes: float,
) -> list[list[ClipWithSegment]]:
    """Group clips into runs separated by more than the window.

    A cluster ends where the gap to the next shot exceeds the window, so a
    held shutter stays one moment however it lands on the clock.
    """
    dated = sorted(
        (c for c in clips if c.clip.asset.file_created_at is not None),
        key=lambda c: c.clip.asset.file_created_at,
    )
    undated = [c for c in clips if c.clip.asset.file_created_at is None]
    groups: list[list[ClipWithSegment]] = []
    window = time_window_minutes * 60
    for clip in dated:
        when = clip.clip.asset.file_created_at
        # Inclusive: two shots exactly the window apart are the same moment.
        # A 2023 hike put 08:34 and 08:39 in a cut five minutes apart, which a
        # strict comparison called distinct by one second of arithmetic.
        if groups and (when - groups[-1][-1].clip.asset.file_created_at).total_seconds() <= window:
            groups[-1].append(clip)
        else:
            groups.append([clip])
    return groups + [[c] for c in undated]


def _fit_temporally_distributed(
    clips: list[ClipWithSegment], max_duration: float
) -> list[ClipWithSegment]:
    """Greedily retain high-quality clips while rewarding temporal distance."""
    ordered = sorted(clips, key=lambda c: c.clip.asset.file_created_at or datetime.min)
    selected: list[ClipWithSegment] = []
    remaining = max_duration
    while candidates := [
        c for c in ordered if c not in selected and c.end_time - c.start_time <= remaining
    ]:
        if not selected:
            chosen = max(candidates, key=lambda c: (c.clip.asset.is_favorite, c.score))
        else:
            selected_dates = [c.clip.asset.file_created_at for c in selected]
            # Spread to the nearest day, THEN quality. Distance in raw seconds
            # never ties, so score was unreachable and favorites were never
            # consulted at all: a February with 19 starred clips in the
            # protected set shipped two of them, beaten by whatever happened
            # to sit furthest from the rest. Days are the unit a viewer feels.
            chosen = max(
                candidates,
                key=lambda c: (
                    round(
                        min(
                            abs((c.clip.asset.file_created_at - d).total_seconds())
                            for d in selected_dates
                        )
                        / 86400
                    ),
                    c.clip.asset.is_favorite,
                    c.score,
                ),
            )
        selected.append(chosen)
        remaining -= chosen.end_time - chosen.start_time
    return sorted(selected, key=lambda c: c.clip.asset.file_created_at or datetime.min)


# Beyond a month, a period carries its own weight: a year that skips March
# because six other months are starred reads as a gap. Within a month it does
# not — the story is the month, and a Tuesday nobody photographed is not owed
# a slot.
_SPAN_WHERE_PERIODS_MATTER_DAYS = 31
_FAVORITE_SHARE_ACROSS_PERIODS = 0.7


def _fit_protected(protected: list[ClipWithSegment], max_duration: float) -> list[ClipWithSegment]:
    """Fit a protected set that cannot fit, favorites first.

    When the user has starred more than the runtime can hold, the runtime is
    theirs: a February with 41 favorites has no reason to show anything else.
    Over a longer span coverage keeps a share, so a whole month cannot vanish
    from a year.
    """
    starred = [c for c in protected if c.clip.asset.is_favorite]
    rest = [c for c in protected if not c.clip.asset.is_favorite]
    if not starred or not rest:
        return _fit_temporally_distributed(protected, max_duration)

    dates = [c.clip.asset.file_created_at for c in protected if c.clip.asset.file_created_at]
    span_days = (max(dates) - min(dates)).days if dates else 0
    share = 1.0 if span_days <= _SPAN_WHERE_PERIODS_MATTER_DAYS else _FAVORITE_SHARE_ACROSS_PERIODS

    kept = _fit_temporally_distributed(starred, max_duration * share)
    spent = sum(c.end_time - c.start_time for c in kept)
    kept += _fit_temporally_distributed(rest, max_duration - spent)
    return sorted(kept, key=lambda c: c.clip.asset.file_created_at or datetime.min)


def _find_sole_month_representatives(
    clips: list[ClipWithSegment],
    extra_ids: set[str] | None = None,
) -> tuple[list[ClipWithSegment], list[ClipWithSegment]]:
    """Split clips into sole monthly representatives and the rest.

    Returns (sole_reps, regular). Sole reps are clips that are the only
    one from their month — removing them would create a temporal gap.
    """
    month_counts: dict[str, int] = defaultdict(int)
    for c in clips:
        month_counts[c.clip.asset.file_created_at.strftime("%Y-%m")] += 1

    protected_ids = (
        {
            c.clip.asset.id
            for c in clips
            if month_counts[c.clip.asset.file_created_at.strftime("%Y-%m")] == 1
        }
        # A favorite is protected too. Coverage fillers exist because nothing
        # better was known about a period; a favorite is the user saying
        # outright what they want. Measured on a real February: the protected
        # set overflowed the runtime on fillers alone, the scaler fell back to
        # distributing only those, and 19 favorites became 2.
        | {c.clip.asset.id for c in clips if c.clip.asset.is_favorite}
        | (extra_ids or set())
    )

    sole = [c for c in clips if c.clip.asset.id in protected_ids]
    regular = [c for c in clips if c.clip.asset.id not in protected_ids]
    return sole, regular


class ClipScaler:
    """Scales clip selections to target durations and removes temporal duplicates."""

    def scale_to_target_duration(
        self,
        clips: list[ClipWithSegment],
        target_duration: float,
        protected_ids: set[str] | None = None,
        max_overrun_seconds: float = 0.0,
    ) -> list[ClipWithSegment]:
        """Scale down selection to fit target duration.

        Removes lowest-scored clips (non-favorites first) until total
        duration is within target plus the explicit overrun. Clips in protected_ids are
        treated as protected (same priority as high-density week clips).
        """
        if not clips:
            return clips

        total = sum(c.end_time - c.start_time for c in clips)
        max_allowed = max(0.0, target_duration + max_overrun_seconds)

        if total <= max_allowed:
            logger.info(
                "Duration %.0fs within target %.0fs (+%.0fs)",
                total,
                target_duration,
                max_overrun_seconds,
            )
            return clips

        logger.info(
            f"Duration {total:.0f}s exceeds target {target_duration:.0f}s, removing clips..."
        )

        sole_reps, regular = _find_sole_month_representatives(clips, protected_ids)
        sole_duration = sum(c.end_time - c.start_time for c in sole_reps)

        if sole_duration > max_allowed:
            logger.info(
                "Protected clips exceed the strict duration budget; retaining a distributed subset"
            )
            return _fit_protected(sole_reps, max_allowed)

        if sole_reps:
            logger.info(
                f"Protected {len(sole_reps)} sole monthly representatives "
                f"({sole_duration:.0f}s reserved)"
            )

        favorites_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for c in regular:
            if c.clip.asset.is_favorite:
                week = c.clip.asset.file_created_at.strftime("%Y-W%W")
                favorites_by_week[week].append(c)

        num_favorite_weeks = len(favorites_by_week)
        if num_favorite_weeks > 0:
            avg_per_week = (
                len([c for c in regular if c.clip.asset.is_favorite]) / num_favorite_weeks
            )
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

        def removability_score(c: ClipWithSegment) -> tuple:
            is_fav = c.clip.asset.is_favorite
            week = c.clip.asset.file_created_at.strftime("%Y-W%W")
            is_protected = week in protected_weeks
            return (is_fav, is_protected, c.score)

        regular_sorted = sorted(regular, key=removability_score)

        result = sole_reps.copy()
        running_total = sole_duration
        removed_count = 0

        for c in reversed(regular_sorted):
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

    def _keep_best_from_cluster(
        self,
        time_key: str,
        cluster_clips: list[ClipWithSegment],
        keep: int,
        protected_ids: set[str],
    ) -> tuple[list[ClipWithSegment], int]:
        """Return (kept_clips, num_removed) for a temporal cluster of 2+ clips.

        A favourite outranks any score: what the viewer starred survives the
        cluster whatever the scorer made of it. Ranking on score alone is how
        a starred shot gets dropped behind two ordinary ones.

        Protected clips are exempt from the per-moment cap rather than merely
        ranked above it. A dense day is given three clips so it reads as a day
        rather than a glimpse; the duration scaler and the photo cap both
        honour that, and thinning it back to one here undoes it (#490, #510).
        A favourite is exempt on the same terms: ranking it first is no use
        once protection has taken every slot.
        """
        protected = [c for c in cluster_clips if c.clip.asset.id in protected_ids]
        ranked = sorted(
            (c for c in cluster_clips if c.clip.asset.id not in protected_ids),
            key=lambda c: (c.clip.asset.is_favorite, c.score),
            reverse=True,
        )
        # A star is exempt from the cap for the same reason coverage is. With
        # the slots already filled by protected clips, room reaches zero and
        # the ranking that puts favourites first has nothing left to put them
        # in — and backfill can never re-admit a clip from a moment already in
        # the cut, so the star is simply gone.
        starred = [c for c in ranked if c.clip.asset.is_favorite]
        rest = [c for c in ranked if not c.clip.asset.is_favorite]
        room = max(0, keep - len(protected) - len(starred))
        kept, dropped = protected + starred + rest[:room], rest[room:]

        if dropped:
            kept_desc = ", ".join(
                f"{c.clip.asset.original_file_name or c.clip.asset.id[:8]} "
                f"(score={c.score:.2f}, fav={c.clip.asset.is_favorite})"
                for c in kept
            )
            dropped_favorites = sum(1 for c in dropped if c.clip.asset.is_favorite)
            logger.debug(
                f"Temporal cluster {time_key}: keeping {kept_desc}; "
                f"removing {len(dropped)} ({dropped_favorites} fav)"
            )

        return kept, len(dropped)

    def deduplicate_temporal_clusters(
        self,
        clips: list[ClipWithSegment],
        time_window_minutes: float = 10.0,
        keep_per_moment: int = 1,
        protected_ids: set[str] | None = None,
    ) -> list[ClipWithSegment]:
        """Thin near-duplicate clips from the same moment.

        One per moment suits a sixty-second month. On a five-minute trip it is
        wrong: several distinct shots minutes apart are what a travel day looks
        like, and cutting to one left selection too short to fill the runtime,
        so duration backfill put the very clips this had just rejected back in.
        Measured on a 967-asset trip: 39 clips in, 16 out, then backfill
        rebuilt the cut to 55.

        keep_per_moment lets the caller say how many a long memory can afford,
        and protected_ids names the clips no per-moment cap may drop.
        """
        if not clips:
            return clips

        result = []
        removed_count = 0
        clusters_with_duplicates = 0

        keep = max(1, keep_per_moment)
        protected = protected_ids or set()
        for cluster_clips in group_by_moment(clips, time_window_minutes):
            if len(cluster_clips) <= keep:
                result.extend(cluster_clips)
                continue

            when = cluster_clips[0].clip.asset.file_created_at
            kept, removed = self._keep_best_from_cluster(str(when), cluster_clips, keep, protected)
            result.extend(kept)
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
