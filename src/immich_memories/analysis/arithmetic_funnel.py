"""The narrowing stages that decide a cut by counting.

A per-day photo cap, a spread across dates, a fit to the runtime, a
non-favourite ratio and a photo ratio: five stages that arrive at a cut
arithmetically. They are the legacy path, kept as the fallback for the
structure pass, and they go when the editing passes land (#764).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.clip_distribution import (
    _event_periods_of,
    _partition_photos_per_day,
    enforce_photo_cap,
    photos_per_day_for,
)

if TYPE_CHECKING:
    from immich_memories.analysis.clip_scaler import ClipScaler
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig

logger = logging.getLogger(__name__)


class ClipSelector(Protocol):
    """What the funnel needs from the refiner that runs it.

    A Protocol rather than the concrete ClipRefiner: the refiner is what
    imports this module, and naming the two selection entry points it lends
    keeps the dependency pointing one way.
    """

    config: PipelineConfig
    scaler: ClipScaler

    def select_clips_by_trip_segments(
        self,
        analyzed: list[ClipWithSegment],
        target: int,
    ) -> list[ClipWithSegment]: ...

    def select_clips_distributed_by_date(
        self,
        clips: list[ClipWithSegment],
        target_count: int,
        event_periods: set[str] | None = ...,
    ) -> list[ClipWithSegment]: ...


@dataclass(frozen=True)
class ArithmeticNarrowing:
    """What the counting stages leave for the rest of the phase.

    `analyzed` is the pool after the per-day cap — the supply the photo ratio
    is later judged scarce against, which is not the same set the cut came
    from. `coverage_ids` are the clips standing for a period on their own,
    which every later stage treats as untouchable.
    """

    analyzed: list[ClipWithSegment]
    selected: list[ClipWithSegment]
    coverage_ids: set[str]


def narrow_by_arithmetic(
    selector: ClipSelector,
    all_analyzed: list[ClipWithSegment],
    *,
    target_duration: float,
    max_overrun: float,
) -> ArithmeticNarrowing:
    """Cap the photos per day, spread the cut across dates, fit it to runtime."""
    config = selector.config
    # Measured before the per-day cap flattens every day into the same
    # narrow range (#488).
    event_periods = _event_periods_of(all_analyzed)
    active_days = len(
        {c.clip.asset.file_created_at.date() for c in all_analyzed if c.clip.asset.file_created_at}
    )
    analyzed, _photo_overflow = _partition_photos_per_day(
        all_analyzed,
        photos_per_day_for(config.target_clips, active_days),
    )
    trace.record("per-day photo cap", all_analyzed, analyzed)

    target_with_buffer = int(config.target_clips * 1.2)

    if config.overnight_bases:
        selected = selector.select_clips_by_trip_segments(analyzed, target_with_buffer)
    else:
        selected = selector.select_clips_distributed_by_date(
            analyzed, target_with_buffer, event_periods
        )
    trace.record("distribute by date", analyzed, selected)

    coverage_ids: set[str] = getattr(selector, "_coverage_ids", set())
    before_scale = selected
    selected = selector.scaler.scale_to_target_duration(
        selected,
        target_duration,
        protected_ids=coverage_ids,
        max_overrun_seconds=max_overrun,
    )
    trace.record(f"fit to {target_duration:.0f}s", before_scale, selected)

    return ArithmeticNarrowing(
        analyzed=analyzed,
        selected=selected,
        coverage_ids=coverage_ids,
    )


def _trim_non_favorites(
    non_favorites: list[ClipWithSegment],
    max_non_favorites: int,
    coverage_ids: set[str],
) -> list[ClipWithSegment]:
    """Cut the ratio down to size without cutting what covers a period.

    The scaler, the photo cap and the moment dedup all treat a coverage clip
    as untouchable. This was the last drop site that did not: it sorted by
    score and truncated, so the one clip standing for a whole month could be
    cut for scoring low — which is exactly why it was added in the first
    place, since the month had nothing better.
    """
    covering = [c for c in non_favorites if c.clip.asset.id in coverage_ids]
    rest = sorted(
        (c for c in non_favorites if c.clip.asset.id not in coverage_ids),
        key=lambda c: c.score,
        reverse=True,
    )
    room = max(0, max_non_favorites - len(covering))
    return covering + rest[:room]


def cap_ratios(
    config: PipelineConfig,
    selected: list[ClipWithSegment],
    analyzed: list[ClipWithSegment],
    *,
    coverage_ids: set[str],
    target_duration: float,
) -> tuple[list[ClipWithSegment], bool]:
    """Hold the cut to its non-favourite share and its photo share.

    Returns the cut and whether the photo cap was bypassed for scarcity —
    backfill has to know, because its exemption then admits photos again.
    """
    if config.max_non_favorite_ratio < 1.0 and config.prioritize_favorites:
        favorites = [c for c in selected if c.clip.asset.is_favorite]
        non_favorites = [c for c in selected if not c.clip.asset.is_favorite]

        max_non_favorites = int(len(selected) * config.max_non_favorite_ratio)
        min_non_favorites = max(0, config.target_clips - len(favorites))
        max_non_favorites = max(max_non_favorites, min_non_favorites)

        if len(non_favorites) > max_non_favorites:
            non_favorites = _trim_non_favorites(non_favorites, max_non_favorites, coverage_ids)

            logger.info(
                f"Final selection: limiting non-favorites to {len(non_favorites)} "
                f"({config.max_non_favorite_ratio:.0%} of {len(selected)})"
            )

            selected = favorites + non_favorites

    # Enforce photo ratio cap (drop lowest-scored photos if over limit)
    photo_cap_bypassed = False
    if config.photo_max_ratio < 1.0:
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
            config.photo_max_ratio,
            protected_ids=coverage_ids,
        )
        trace.record("photo ratio cap", before_cap, selected)

    return selected, photo_cap_bypassed
