from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechRegion:
    """A contiguous span of detected voice activity."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class BoundaryCandidate:
    """A candidate cut point with the evidence that produced its rank."""

    time: float
    gap_start: float
    gap_end: float
    completion_score: float = 0.0

    @property
    def gap_width(self) -> float:
        return self.gap_end - self.gap_start

    @property
    def snapped_time(self) -> float:
        """Midpoint of the enclosing silence gap.

        Cutting at the midpoint is what makes a ~100 ms aligner acceptable: the
        question is containment, not precision. A boundary error is harmless if
        the cut still lands inside the pause.
        """
        return (self.gap_start + self.gap_end) / 2.0
