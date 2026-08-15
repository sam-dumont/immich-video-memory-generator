from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

from immich_memories.speech.models import SpeechRegion

logger = logging.getLogger(__name__)

VAD_SAMPLE_RATE = 16000


def silence_gaps(regions: list[SpeechRegion], duration: float) -> list[tuple[float, float]]:
    """Complement of the speech regions across [0, duration]."""
    if not regions:
        return [(0.0, duration)]

    ordered = sorted(regions, key=lambda r: r.start)
    gaps: list[tuple[float, float]] = []
    cursor = 0.0

    for region in ordered:
        if region.start > cursor:
            gaps.append((cursor, region.start))
        cursor = max(cursor, region.end)

    if cursor < duration:
        gaps.append((cursor, duration))

    return gaps


class SpeechDetector(Protocol):
    def detect(self, audio: np.ndarray, sample_rate: int) -> list[SpeechRegion]: ...


class SileroSpeechDetector:
    """Silero VAD v6 via its bundled ONNX weights.

    Silero anchors a region's end on the frame where speech probability first
    dips below its negative threshold, so word-final fricatives get clipped.
    Downstream code compensates by snapping cuts to gap midpoints rather than
    to region edges.
    """

    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 200) -> None:
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self._model = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._load()
        return self._available

    def _load(self) -> bool:
        try:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad(onnx=True)
            return True
        except (ImportError, RuntimeError, OSError) as exc:
            logger.debug("Silero VAD unavailable: %s", type(exc).__name__)
            return False

    def detect(self, audio: np.ndarray, sample_rate: int) -> list[SpeechRegion]:
        if not self.available:
            return []

        from silero_vad import get_speech_timestamps

        stamps = get_speech_timestamps(
            audio,
            self._model,
            sampling_rate=sample_rate,
            threshold=self.threshold,
            min_silence_duration_ms=self.min_silence_ms,
            return_seconds=True,
        )
        return [SpeechRegion(start=s["start"], end=s["end"]) for s in stamps]
