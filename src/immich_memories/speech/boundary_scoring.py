from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from immich_memories.speech.models import BoundaryCandidate
from immich_memories.speech.turn_detection import TurnDetector, window_ending_at


@dataclass(frozen=True)
class BoundaryWeights:
    """Relative influence of each boundary signal.

    gap dominates deliberately: containment inside a pause is what keeps a cut
    off a word, and it is the only term measured directly rather than predicted.

    completion defaults to 0.0 because smart-turn-v3 was measured on this library
    and does not separate. Of four points where an utterance had just ended, only
    one scored above its paired mid-utterance point, and one unambiguously
    mid-sentence moment scored 0.969, the highest of all seven probes.

    This is not a wiring bug. The same implementation scores 0.93 for complete and
    0.02 for incomplete on clean synthetic speech at equal padding. The failure is
    domain mismatch -- children's voices, distant microphone, room noise -- on
    clips short enough that little real signal reaches the model's 8s window.
    The signal stays wired and weighted zero, ready for a real validation set.

    Scene-cut and protected-overlap terms are deliberately absent. The spec calls
    them a small bonus, nothing populates them yet, and shipping scored fields
    with no producer is the dead abstraction the project style forbids. Add them
    when there is a consumer.
    """

    gap: float = 1.0
    completion: float = 0.0


MIN_CUTTABLE_GAP_S = 0.3


def candidates_from_gaps(
    gaps: list[tuple[float, float]],
    min_gap: float = MIN_CUTTABLE_GAP_S,
) -> list[BoundaryCandidate]:
    """One candidate per silence gap wide enough to cut inside."""
    return [
        BoundaryCandidate(time=(start + end) / 2.0, gap_start=start, gap_end=end)
        for start, end in gaps
        if end - start >= min_gap
    ]


def score_candidate(
    candidate: BoundaryCandidate,
    max_gap: float,
    weights: BoundaryWeights | None = None,
) -> float:
    w = weights or BoundaryWeights()
    normalised_gap = candidate.gap_width / max_gap if max_gap > 0 else 0.0

    return w.gap * normalised_gap + w.completion * candidate.completion_score


def score_completions(
    candidates: list[BoundaryCandidate],
    audio: np.ndarray,
    sample_rate: int,
    detector: TurnDetector,
    threshold: float = 0.85,
) -> None:
    """Attach utterance-completion probabilities to candidates, in place.

    Probabilities below the threshold are zeroed rather than kept as a weak
    signal. The asymmetry is deliberate: a false 'complete' cuts someone off
    permanently in the rendered video, a false 'incomplete' only picks a
    different candidate.
    """
    for candidate in candidates:
        probability = detector.completion_probability(
            window_ending_at(audio, sample_rate, candidate.snapped_time), sample_rate
        )
        candidate.completion_score = probability if probability >= threshold else 0.0


def best_boundary(
    candidates: list[BoundaryCandidate],
    target_time: float,
    max_shift: float,
    weights: BoundaryWeights | None = None,
) -> BoundaryCandidate | None:
    """Highest-scoring candidate within `max_shift` seconds of `target_time`."""
    reachable = [c for c in candidates if abs(c.time - target_time) <= max_shift]
    if not reachable:
        return None

    max_gap = max(c.gap_width for c in reachable)
    return max(reachable, key=lambda c: score_candidate(c, max_gap, weights))
