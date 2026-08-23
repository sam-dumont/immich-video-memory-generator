"""Segment extent policy: how long a clip may run, and where it may be cut.

Sits between `boundary_placement`, which is pure gap geometry over a list of
ranges, and the analyzer's `analyze` orchestration. Every decision here is
about a segment's extents given the source length and the speech-protected
ranges -- nothing in this module detects, scores or ranks anything.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from immich_memories.analysis.boundary_placement import (
    cap_end_to_gap,
    protected_gaps,
    select_segment_boundaries,
)
from immich_memories.analysis.segment_generation import adjust_candidates_for_audio

if TYPE_CHECKING:
    from immich_memories.analysis.analyzer_models import ScoredSegment
    from immich_memories.audio.audio_models import AudioAnalysisResult

logger = logging.getLogger(__name__)


def max_segment_for_source(
    source_duration: float,
    max_segment_duration: float,
    has_good_scene: bool = False,
) -> float:
    """Longest segment allowed out of a source of this length.

    Short sources may be used whole; medium ones are capped at
    `max_segment_duration` with 15% grace for a good scene; sources over 60s
    get a proportional 20% limit so one long video cannot dominate a memory.
    """
    grace = 1.15 if has_good_scene else 1.0
    max_with_grace = max_segment_duration * grace

    if source_duration <= max_segment_duration:
        return source_duration

    if source_duration <= 60:
        return min(max_with_grace, source_duration)

    proportional = source_duration * 0.20
    return max(max_segment_duration, min(proportional, max_with_grace))


def dynamic_optimal_duration(
    source_duration: float,
    optimal_clip_duration: float,
    max_optimal_duration: float,
    target_extraction_ratio: float,
) -> float:
    """Sweet-spot clip length for a source of this length.

    Sources under 20s keep the base optimal; longer ones scale toward
    `max_optimal_duration` at `target_extraction_ratio` of the source.
    """
    if source_duration > 20.0:
        return min(
            max_optimal_duration,
            max(optimal_clip_duration, source_duration * target_extraction_ratio),
        )
    return optimal_clip_duration


def safe_cut_gaps(
    audio_content_result: AudioAnalysisResult,
    video_duration: float,
    min_silence_ms: int,
) -> list[tuple[float, float]]:
    """Spans this source may be cut in, buffered as every other pass does."""
    return protected_gaps(
        audio_content_result.protected_ranges,
        video_duration,
        min_silence_ms,
    )


def adjust_candidates_for_protected_audio(
    candidates: list,
    audio_content_result: AudioAnalysisResult | None,
    video_duration: float,
    min_segment_duration: float,
    max_segment_duration: float,
    min_silence_ms: int,
) -> list:
    """Step 3b: pull candidate boundaries out of protected audio ranges."""
    if audio_content_result and audio_content_result.protected_ranges:
        logger.info("Step 3b: Adjusting boundaries to avoid cutting mid-laugh/speech")
        original_count = len(candidates)
        proportional_max = max_segment_for_source(video_duration, max_segment_duration)
        candidates = adjust_candidates_for_audio(
            candidates,
            audio_content_result,
            video_duration,
            min_segment_duration,
            proportional_max,
            min_silence_ms=min_silence_ms,
        )
        logger.info(f"  -> Adjusted {original_count} candidates to {len(candidates)} candidates")
        if candidates:
            sample = candidates[0]
            logger.info(f"     Example segment: {sample[0].time:.2f}s - {sample[1].time:.2f}s")
    elif audio_content_result:
        logger.info(
            "Step 3b: SKIPPED - no protected ranges to avoid "
            f"(detected {len(audio_content_result.events)} audio events, "
            f"but none were speech/laughter above confidence threshold)"
        )
    else:
        logger.debug("Step 3b: SKIPPED - audio content analysis not enabled/available")
    return candidates


def repair_best_segment(
    best: ScoredSegment,
    audio_content_result: AudioAnalysisResult,
    video_duration: float,
    min_segment_duration: float,
    max_segment_duration: float,
    min_silence_ms: int,
) -> None:
    """Fix best-segment boundaries that cut through protected audio ranges.

    Modifies the segment in place. Derives its gaps from `safe_cut_gaps`, the
    same helper step 3b uses, so this pass cannot undo what that one decided:
    walking the raw ranges one at a time let a boundary pushed out of one range
    land inside the next when two overlap, and inverting the unbuffered ranges
    let this pass cut inside step 3b's safety margin.
    """
    gaps = safe_cut_gaps(audio_content_result, video_duration, min_silence_ms)
    best.safe_cut_gaps = gaps
    new_start, new_end, adjusted = select_segment_boundaries(
        best.start_time, best.end_time, gaps, video_duration, min_segment_duration
    )

    if adjusted:
        best.start_time = new_start
        best.end_time = new_end
        logger.info(f"  -> Adjusted best segment: {best.start_time:.1f}s-{best.end_time:.1f}s")

    proportional_max = max_segment_for_source(video_duration, max_segment_duration)
    final_duration = best.end_time - best.start_time
    if final_duration > proportional_max:
        # `start + proportional_max` is speech-blind and this is the segment
        # that actually gets rendered -- snap the cap to a real gap, exactly
        # as the candidate pass does.
        best.end_time = cap_end_to_gap(
            best.start_time,
            best.start_time + proportional_max,
            gaps,
            min_segment_duration,
        )
        logger.info(
            f"  -> Re-trimmed to proportional max: {best.start_time:.1f}s-{best.end_time:.1f}s "
            f"(was {final_duration:.1f}s, max={proportional_max:.1f}s for {video_duration:.1f}s source)"
        )
