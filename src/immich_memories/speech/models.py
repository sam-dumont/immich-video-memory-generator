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
