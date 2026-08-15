"""Standalone functions for generating candidate video segments.

These functions handle boundary detection, merging, candidate segment
generation, and audio-related scoring helpers.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.analyzer_models import CutPoint
from immich_memories.analysis.scenes import SceneDetector
from immich_memories.analysis.silence_detection import detect_silence_gaps
from immich_memories.speech.boundary_scoring import best_boundary, candidates_from_gaps
from immich_memories.speech.models import BoundaryCandidate

if TYPE_CHECKING:
    from immich_memories.audio.audio_models import AudioAnalysisResult

logger = logging.getLogger(__name__)

# Fraction of the VAD's min-silence window a protected-range buffer may use.
# Must stay below 0.5 -- see speech_buffer_seconds.
_BUFFER_SHARE_OF_MIN_SILENCE = 0.4
_MAX_PROTECTED_BUFFER_S = 0.3


def detect_visual_boundaries(
    video_path: Path,
    scene_detector: SceneDetector,
) -> list[float]:
    """Detect visual scene boundaries using PySceneDetect.

    Args:
        video_path: Path to video file.
        scene_detector: SceneDetector instance.

    Returns:
        List of boundary timestamps in seconds.
    """
    try:
        scenes = scene_detector.detect(video_path, extract_keyframes=False)

        # Extract unique boundary times
        boundaries: set[float] = set()
        for scene in scenes:
            boundaries.add(scene.start_time)
            boundaries.add(scene.end_time)

        return sorted(boundaries)

    except (RuntimeError, OSError, ValueError) as e:
        logger.warning(f"Visual scene detection failed: {e}")
        return []


def detect_audio_boundaries(
    video_path: Path,
    silence_threshold_db: float,
    min_silence_duration: float,
) -> list[float]:
    """Detect audio boundaries (silence gap edges).

    Args:
        video_path: Path to video file.
        silence_threshold_db: Audio level threshold for silence detection.
        min_silence_duration: Minimum silence gap duration to detect.

    Returns:
        List of boundary timestamps (silence gap start/end points).
    """
    try:
        silence_gaps = detect_silence_gaps(
            video_path,
            threshold_db=silence_threshold_db,
            min_silence_duration=min_silence_duration,
        )

        # Extract all silence gap boundary times
        boundaries: set[float] = set()
        for gap_start, gap_end in silence_gaps:
            boundaries.add(gap_start)
            boundaries.add(gap_end)

        sorted_boundaries = sorted(boundaries)
        logger.info(
            f"Audio analysis: found {len(silence_gaps)} silence gaps, "
            f"{len(sorted_boundaries)} boundary points "
            f"(threshold={silence_threshold_db}dB, min_duration={min_silence_duration}s)"
        )
        return sorted_boundaries

    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.warning(f"Audio boundary detection failed: {e}")
        return []


def merge_audio_time_into_points(
    t: float,
    all_times: dict[float, CutPoint],
    cut_point_merge_tolerance: float,
) -> None:
    """Merge one audio timestamp into all_times, finding a nearby visual point or adding new.

    Args:
        t: Audio timestamp.
        all_times: Existing cut points keyed by time.
        cut_point_merge_tolerance: Time window for merging nearby points.
    """
    for existing_time, cp in list(all_times.items()):
        if abs(t - existing_time) <= cut_point_merge_tolerance:
            cp.is_audio = True
            return
    all_times[t] = CutPoint(time=t, is_visual=False, is_audio=True)


def merge_boundaries(
    visual: list[float],
    audio: list[float],
    video_duration: float,
    cut_point_merge_tolerance: float,
) -> list[CutPoint]:
    """Merge visual and audio boundaries into unified cut points.

    Boundaries within the merge tolerance are considered the same point.
    Points that are both visual and audio are marked with higher priority.

    Args:
        visual: Visual boundary timestamps.
        audio: Audio boundary timestamps.
        video_duration: Total video duration.
        cut_point_merge_tolerance: Time window for merging nearby points.

    Returns:
        List of CutPoint sorted by time.
    """
    all_times: dict[float, CutPoint] = {}
    for t in visual:
        all_times[t] = CutPoint(time=t, is_visual=True, is_audio=False)

    for t in audio:
        merge_audio_time_into_points(t, all_times, cut_point_merge_tolerance)

    if 0.0 not in all_times:
        all_times[0.0] = CutPoint(time=0.0, is_visual=True, is_audio=True)
    if video_duration not in all_times:
        all_times[video_duration] = CutPoint(time=video_duration, is_visual=True, is_audio=True)

    cut_points = sorted(all_times.values(), key=lambda cp: cp.time)

    deduped: list[CutPoint] = []
    for cp in cut_points:
        if not deduped or cp.time - deduped[-1].time > 0.3:
            deduped.append(cp)
        else:
            deduped[-1].is_visual = deduped[-1].is_visual or cp.is_visual
            deduped[-1].is_audio = deduped[-1].is_audio or cp.is_audio

    return deduped


def collect_mixed_boundary_candidates(
    cut_points: list[CutPoint],
    video_duration: float,
    min_segment_duration: float,
    max_segment_duration: float,
    dynamic_optimal: float,
) -> list[tuple[CutPoint, CutPoint]]:
    """Return valid segments with at least one audio boundary, sorted by priority.

    Args:
        cut_points: Available cut points.
        video_duration: Total video duration.
        min_segment_duration: Minimum segment duration.
        max_segment_duration: Maximum segment duration.
        dynamic_optimal: Dynamic optimal clip duration.

    Returns:
        Up to 20 candidate segment pairs sorted by audio priority then duration.
    """
    candidates = []
    for i, start_cp in enumerate(cut_points):
        for end_cp in cut_points[i + 1 :]:
            duration = end_cp.time - start_cp.time
            if duration < min_segment_duration or duration > max_segment_duration:
                continue
            if start_cp.is_audio or end_cp.is_audio:
                candidates.append((start_cp, end_cp))

    if not candidates:
        return []

    candidates.sort(
        key=lambda pair: (
            -(pair[0].is_audio + pair[1].is_audio),
            abs((pair[1].time - pair[0].time) - dynamic_optimal),
        ),
    )
    return candidates[:20]


def generate_segments_from_points(
    points: list[CutPoint],
    min_segment_duration: float,
    max_segment_duration: float,
    dynamic_optimal: float | None = None,
) -> list[tuple[CutPoint, CutPoint]]:
    """Generate all valid segments from a list of cut points.

    Args:
        points: List of cut points to use as boundaries.
        min_segment_duration: Minimum segment duration.
        max_segment_duration: Maximum segment duration.
        dynamic_optimal: Dynamic optimal clip duration for sorting.

    Returns:
        List of (start, end) point pairs for valid segments.
    """
    candidates = []

    for i, start_cp in enumerate(points):
        for end_cp in points[i + 1 :]:
            duration = end_cp.time - start_cp.time

            if duration < min_segment_duration:
                continue
            if duration > max_segment_duration:
                continue

            candidates.append((start_cp, end_cp))

    # Sort by proximity to optimal duration
    if candidates and dynamic_optimal is not None:
        candidates.sort(key=lambda pair: abs((pair[1].time - pair[0].time) - dynamic_optimal))

    return candidates


def generate_candidate_segments(
    cut_points: list[CutPoint],
    video_duration: float,
    min_segment_duration: float,
    max_segment_duration: float,
    dynamic_optimal: float,
) -> list[tuple[CutPoint, CutPoint]]:
    """Generate candidate segments from cut points.

    Segments preferentially start and end on audio boundaries.
    Falls back to visual-only if no audio boundaries available.

    Args:
        cut_points: List of available cut points.
        video_duration: Total video duration.
        min_segment_duration: Minimum segment duration.
        max_segment_duration: Maximum segment duration.
        dynamic_optimal: Dynamic optimal clip duration.

    Returns:
        List of (start_point, end_point) tuples for candidate segments.
    """
    if len(cut_points) < 2:
        return []

    audio_points = [cp for cp in cut_points if cp.is_audio]
    if len(audio_points) >= 2:
        candidates = generate_segments_from_points(
            audio_points, min_segment_duration, max_segment_duration, dynamic_optimal
        )
        if candidates:
            return candidates

    candidates = collect_mixed_boundary_candidates(
        cut_points, video_duration, min_segment_duration, max_segment_duration, dynamic_optimal
    )
    if candidates:
        return candidates

    logger.warning("No audio boundaries found, using visual-only segments")
    return generate_segments_from_points(
        cut_points, min_segment_duration, max_segment_duration, dynamic_optimal
    )


def find_nearest_cut_point(
    cut_points: list[CutPoint],
    time: float,
) -> CutPoint | None:
    """Find the cut point nearest to a given time.

    Args:
        cut_points: List of available cut points.
        time: Target time in seconds.

    Returns:
        Nearest CutPoint, or None if no points available.
    """
    if not cut_points:
        return None

    nearest = min(cut_points, key=lambda cp: abs(cp.time - time))
    return nearest


def generate_fallback_segments(
    video_duration: float,
    cut_points: list[CutPoint],
    min_segment_duration: float,
    proportional_max: float,
) -> list[tuple[CutPoint, CutPoint]]:
    """Generate fallback segments when normal generation fails.

    Creates segments based on fixed intervals or the entire video.
    Uses proportional max duration based on source length.

    Args:
        video_duration: Total video duration.
        cut_points: Available cut points.
        min_segment_duration: Minimum segment duration.
        proportional_max: Proportional max duration for this source.

    Returns:
        List of fallback segment candidates.
    """
    target_duration = min(proportional_max, video_duration)
    logger.debug(
        f"Fallback segments: source={video_duration:.1f}s, "
        f"proportional_max={proportional_max:.1f}s, target={target_duration:.1f}s"
    )

    # Generate evenly spaced segments
    candidates: list[tuple[CutPoint, CutPoint]] = []
    step = target_duration / 2  # 50% overlap

    current_start = 0.0
    while current_start + min_segment_duration <= video_duration:
        end_time = min(current_start + target_duration, video_duration)

        # Find nearest cut points
        start_cp = find_nearest_cut_point(cut_points, current_start)
        end_cp = find_nearest_cut_point(cut_points, end_time)

        if start_cp and end_cp and start_cp.time < end_cp.time:
            candidates.append((start_cp, end_cp))

        current_start += step

    # Always include a fallback using video bounds
    if not candidates:
        start_cp = CutPoint(time=0.0, is_visual=True, is_audio=True)
        end_time = min(target_duration, video_duration)
        end_cp = CutPoint(time=end_time, is_visual=True, is_audio=True)
        candidates.append((start_cp, end_cp))

    return candidates


def speech_buffer_seconds(min_silence_ms: int) -> float:
    """Padding to widen each protected range by, derived from the VAD's min silence.

    Buffering widens a range on both sides, so two ranges closer together than
    twice the buffer merge into one. The VAD closes a speech region only after
    `min_silence_ms` of silence, so any buffer at or above half that pause
    re-merges exactly the utterance splits it was configured to make -- and a
    candidate whose two ends both land inside the resulting blob can no longer
    be nudged anywhere. Staying under half leaves those pauses intact.

    Capped at the historical 0.3 s so a long `min_silence_ms` does not turn a
    safety margin into a boundary-swallowing one.
    """
    return min(_MAX_PROTECTED_BUFFER_S, min_silence_ms / 1000.0 * _BUFFER_SHARE_OF_MIN_SILENCE)


def merge_buffered_ranges(
    protected_ranges: list[tuple[float, float]],
    video_duration: float,
    buffer: float,
) -> list[tuple[float, float]]:
    """Buffer and merge overlapping protected audio ranges.

    Args:
        protected_ranges: Raw protected ranges from audio analysis.
        video_duration: Total video duration.
        buffer: Buffer to add around each range (seconds), from
            `speech_buffer_seconds`.

    Returns:
        Merged list of buffered ranges.
    """
    buffered = [
        (max(0, start - buffer), min(video_duration, end + buffer))
        for start, end in protected_ranges
    ]

    merged: list[tuple[float, float]] = []
    for start, end in sorted(buffered):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _gaps_between(
    ranges: list[tuple[float, float]],
    video_duration: float,
) -> list[tuple[float, float]]:
    """Spans between protected ranges — the places a cut may land."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for range_start, range_end in sorted(ranges):
        if range_start > cursor:
            gaps.append((cursor, range_start))
        cursor = max(cursor, range_end)
    if cursor < video_duration:
        gaps.append((cursor, video_duration))
    return gaps


def _candidates_within(
    gaps: list[tuple[float, float]],
    window_start: float,
    window_end: float,
) -> list[BoundaryCandidate]:
    """Candidates built from the parts of `gaps` lying inside the window.

    Ranking on whole gaps would reach a gap only when its *midpoint* falls
    within max_shift, which rejects exactly the silences worth cutting in: a
    long lead-in or tail whose near edge is adjacent to the target but whose
    centre is seconds away. Clipping first makes the reachable part of every
    gap a candidate in its own right, and its midpoint is still real silence.
    """
    clipped = [
        (max(gap_start, window_start), min(gap_end, window_end)) for gap_start, gap_end in gaps
    ]
    return candidates_from_gaps([(lo, hi) for lo, hi in clipped if hi > lo])


def _escape_time(
    gaps: list[tuple[float, float]],
    target: float,
) -> float | None:
    """Nearest silence for a boundary with none in reach, or None if it is already safe.

    Distance is unbounded and `min_gap` is ignored here, both on purpose. This
    fires only for a boundary sitting inside protected audio, where the real
    comparison is against cutting mid-word: a pause too narrow to be a
    first-choice cut still beats slicing through a syllable, and so does a
    wider pause further away. The `min_segment_duration` and proportional-max
    guards bound the damage.
    """
    if any(gap_start <= target <= gap_end for gap_start, gap_end in gaps):
        return None

    candidates = candidates_from_gaps(gaps, min_gap=0.0)
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c.snapped_time - target)).snapped_time


def _edge_options(
    gaps: list[tuple[float, float]],
    target: float,
    max_shift: float,
    *,
    trims_later: bool,
) -> list[float]:
    """Safe placements for one edge, most-preferred first.

    Both directions are safe -- every candidate midpoint is silence -- so the
    order is decided by duration: inward first because it shortens the clip,
    then outward, then the nearest silence at any distance for an edge still
    inside protected audio. `trims_later` is True for a start edge, where
    moving later trims, and False for an end edge. Growing is the fallback,
    not the default; the old nudger only ever grew, which is why noisy clips
    kept their full duration.
    """
    ahead = (target, target + max_shift)
    behind = (target - max_shift, target)
    inward, outward = (ahead, behind) if trims_later else (behind, ahead)

    options: list[float] = []
    for window_start, window_end in (inward, outward):
        chosen = best_boundary(
            _candidates_within(gaps, window_start, window_end), target, max_shift
        )
        if chosen is not None and chosen.snapped_time not in options:
            options.append(chosen.snapped_time)

    escape = _escape_time(gaps, target)
    if escape is not None and escape not in options:
        options.append(escape)
    return options


def _best_placement(
    start_options: list[float],
    end_options: list[float],
    video_duration: float,
    min_segment_duration: float,
) -> tuple[float, float] | None:
    """Most-preferred pair of edges that still clears the minimum duration.

    Ranking on the summed preference index keeps the inward-trimming pick
    unless it collapses the segment, in which case the next-best placement is
    used. Reverting both edges to the original instead -- what a single
    all-or-nothing guard did -- put the cut back inside speech, which is the
    one outcome worth avoiding.
    """
    placements = []
    for start_rank, raw_start in enumerate(start_options):
        for end_rank, raw_end in enumerate(end_options):
            new_start = max(0.0, raw_start)
            new_end = min(video_duration, raw_end)
            if new_end - new_start >= min_segment_duration:
                placements.append((start_rank + end_rank, new_end - new_start, new_start, new_end))

    if not placements:
        return None
    _, _, best_start, best_end = min(placements)
    return best_start, best_end


def select_segment_boundaries(
    start: float,
    end: float,
    gaps: list[tuple[float, float]],
    video_duration: float,
    min_segment_duration: float,
    max_shift: float = 2.0,
) -> tuple[float, float, bool]:
    """Move a segment's edges to the best nearby silence gap.

    Replaces the previous outward nudging, which pushed boundaries away from
    speech and gave up entirely when a segment sat inside one long protected
    range -- the case that left noisy clips untrimmed.
    """
    if not gaps:
        return start, end, False

    start_options = _edge_options(gaps, start, max_shift, trims_later=True)
    end_options = _edge_options(gaps, end, max_shift, trims_later=False)
    if not start_options and not end_options:
        return start, end, False

    placement = _best_placement(
        start_options or [start], end_options or [end], video_duration, min_segment_duration
    )
    if placement is None:
        return start, end, False

    new_start, new_end = placement
    return new_start, new_end, (new_start, new_end) != (start, end)


def adjust_candidates_for_audio(
    candidates: list[tuple[CutPoint, CutPoint]],
    audio_result: AudioAnalysisResult,
    video_duration: float,
    min_segment_duration: float,
    proportional_max: float,
    max_adjustment: float = 5.0,
    *,
    min_silence_ms: int,
) -> list[tuple[CutPoint, CutPoint]]:
    """Adjust candidate segment boundaries to avoid cutting during protected audio events.

    Args:
        candidates: List of (start, end) cut point pairs.
        audio_result: Audio analysis with protected ranges.
        video_duration: Total video duration.
        min_segment_duration: Minimum segment duration.
        proportional_max: Max segment duration for this source.
        max_adjustment: Maximum adjustment per boundary in seconds.
        min_silence_ms: `SpeechConfig.min_silence_ms`, the pause width the VAD
            split on -- sets how far each protected range may be widened.

    Returns:
        Adjusted list of candidate segments.
    """
    if not audio_result.protected_ranges:
        return candidates

    merged_ranges = merge_buffered_ranges(
        audio_result.protected_ranges,
        video_duration,
        speech_buffer_seconds(min_silence_ms),
    )
    logger.info(f"     Buffered+merged ranges: {[(f'{s:.2f}-{e:.2f}') for s, e in merged_ranges]}")

    gaps = _gaps_between(merged_ranges, video_duration)

    adjusted: list[tuple[CutPoint, CutPoint]] = []
    adjustments_made = 0

    for start_cp, end_cp in candidates:
        new_start, new_end, was_adjusted = select_segment_boundaries(
            start_cp.time,
            end_cp.time,
            gaps,
            video_duration,
            min_segment_duration,
        )

        if was_adjusted:
            adjustments_made += 1
            logger.info(
                f"     Adjusted: {start_cp.time:.2f}s-{end_cp.time:.2f}s -> {new_start:.2f}s-{new_end:.2f}s"
            )

        # Enforce proportional max
        if new_end - new_start > proportional_max:
            logger.info(
                f"     Trimming oversized segment {new_start:.2f}s-{new_end:.2f}s "
                f"({new_end - new_start:.1f}s) to proportional max {proportional_max:.1f}s "
                f"(source={video_duration:.1f}s)"
            )
            new_end = new_start + proportional_max

        if new_end - new_start >= min_segment_duration:
            adj_start = CutPoint(time=new_start, is_visual=start_cp.is_visual, is_audio=True)
            adj_end = CutPoint(time=new_end, is_visual=end_cp.is_visual, is_audio=True)
            adjusted.append((adj_start, adj_end))
        elif new_end - new_start > 0:
            if was_adjusted:
                logger.warning(
                    f"     ⚠️ Keeping original segment {start_cp.time:.2f}s-{end_cp.time:.2f}s "
                    f"(adjustment would make it too short: {new_end - new_start:.2f}s < {min_segment_duration}s). "
                    f"This segment may still cut through speech!"
                )
            adjusted.append((start_cp, end_cp))

    if adjustments_made > 0:
        logger.info(f"     Made {adjustments_made} boundary adjustments to avoid mid-speech cuts")

    return adjusted


def find_overlapping_events(
    start_time: float,
    end_time: float,
    audio_result: AudioAnalysisResult,
) -> list[tuple]:
    """Find audio events that overlap with the given time range.

    Args:
        start_time: Segment start time.
        end_time: Segment end time.
        audio_result: Full video audio analysis result.

    Returns:
        List of (event, overlap_duration) tuples.
    """
    segment_events = []
    for event in audio_result.events:
        if event.end_time > start_time and event.start_time < end_time:
            overlap_start = max(event.start_time, start_time)
            overlap_end = min(event.end_time, end_time)
            overlap_duration = overlap_end - overlap_start
            if overlap_duration > 0:
                segment_events.append((event, overlap_duration))
    return segment_events


def classify_segment_events(
    segment_events: list[tuple],
) -> tuple[float, float, bool, bool, bool, set[str]]:
    """Classify and weight overlapping audio events.

    Args:
        segment_events: List of (event, overlap_duration) tuples.

    Returns:
        Tuple of (total_weighted, total_duration, has_laughter, has_speech,
        has_music, categories).
    """
    from immich_memories.audio.audio_models import classify_audio_event

    total_weighted = 0.0
    total_duration = 0.0
    has_laughter = False
    has_speech = False
    has_music = False
    categories: set[str] = set()

    for event, duration in segment_events:
        total_weighted += event.weight * event.confidence * duration
        total_duration += duration

        cat = classify_audio_event(event.event_class)
        if cat:
            categories.add(cat)

        event_lower = event.event_class.lower()
        if "laugh" in event_lower or "giggle" in event_lower:
            has_laughter = True
        if "speech" in event_lower or "talk" in event_lower:
            has_speech = True
        if "music" in event_lower:
            has_music = True

    return total_weighted, total_duration, has_laughter, has_speech, has_music, categories


def score_segment_audio(
    start_time: float,
    end_time: float,
    audio_result: AudioAnalysisResult,
) -> dict:
    """Score a specific segment based on audio content analysis.

    Args:
        start_time: Segment start time.
        end_time: Segment end time.
        audio_result: Full video audio analysis result.

    Returns:
        Dict with score, has_laughter, has_speech, has_music, audio_categories.
    """
    segment_events = find_overlapping_events(start_time, end_time, audio_result)

    if not segment_events:
        return {
            "score": 0.5,
            "has_laughter": False,
            "has_speech": False,
            "has_music": False,
            "audio_categories": set(),
        }

    total_weighted, total_duration, has_laughter, has_speech, has_music, categories = (
        classify_segment_events(segment_events)
    )

    segment_duration = end_time - start_time
    if segment_duration > 0 and total_duration > 0:
        coverage = total_duration / segment_duration
        quality = total_weighted / total_duration
        score = quality * min(1.0, coverage)
    else:
        score = 0.5

    return {
        "score": min(1.0, max(0.0, score)),
        "has_laughter": has_laughter,
        "has_speech": has_speech,
        "has_music": has_music,
        "audio_categories": categories,
    }
