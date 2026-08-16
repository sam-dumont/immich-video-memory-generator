"""Where a cut may land, given the audio it must not cut through.

Protected ranges (VAD speech regions unioned with the PANNs events VAD is blind
to) come in; the spans between them -- and the best placement for a segment's
two edges inside those spans -- come out. Segment *generation* and audio
*scoring* live in segment_generation.py; this module only answers "may I cut
here, and if not, where instead".
"""

from __future__ import annotations

from immich_memories.speech.boundary_scoring import best_boundary, candidates_from_gaps
from immich_memories.speech.models import BoundaryCandidate

# Fraction of the VAD's min-silence window a protected-range buffer may use.
# Must stay below 0.5 -- see speech_buffer_seconds.
_BUFFER_SHARE_OF_MIN_SILENCE = 0.4
_MAX_PROTECTED_BUFFER_S = 0.3


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


def gaps_between(
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


def cap_end_to_gap(
    new_start: float,
    cap_time: float,
    gaps: list[tuple[float, float]],
    min_segment_duration: float,
) -> float:
    """Best gap-snapped end at or before `cap_time`, falling back to `cap_time` itself.

    The straight `new_start + proportional_max` cap is speech-blind -- it lands
    wherever the arithmetic lands, discarding the gap snap `select_segment_boundaries`
    just computed. Clipping each gap to the reachable window (rather than ranking
    whole gaps, which only reaches a gap when its full-width midpoint happens to
    fall within range) surfaces the same near-edge silences `_edge_options` already
    relies on. No candidate clearing the minimum duration means no safe cut exists
    before the cap, and the cap -- a real constraint -- wins.
    """
    window_start = new_start + min_segment_duration
    if window_start >= cap_time:
        return cap_time

    candidates = _candidates_within(gaps, window_start, cap_time)
    chosen = best_boundary(candidates, cap_time, max_shift=cap_time - window_start)
    return chosen.snapped_time if chosen is not None else cap_time
