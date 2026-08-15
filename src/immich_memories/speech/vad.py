from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Protocol

import numpy as np

from immich_memories.speech.models import SpeechRegion

logger = logging.getLogger(__name__)

VAD_SAMPLE_RATE = 16000


def extract_audio_16k(video_path: Path) -> np.ndarray | None:
    """Extract the first audio stream as 16 kHz mono float32.

    Mirrors the stream selection proven in analysis/silence_detection.py --
    `-map 0:a:0` skips iPhone spatial-audio streams that would otherwise get
    mixed in and blur the VAD signal.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(VAD_SAMPLE_RATE),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-",
    ]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, check=True, timeout=60
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        logger.debug("Audio extraction failed for %s: %s", video_path, type(exc).__name__)
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32)


def silence_gaps(regions: list[SpeechRegion], duration: float) -> list[tuple[float, float]]:
    """Complement of the speech regions across [0, duration].

    Gap boundaries are clamped to [0, duration] -- a region whose end exceeds
    duration (e.g. detected on a longer audio slice than the caller now has)
    must not produce a gap outside that window.
    """
    if not regions:
        return [(0.0, duration)]

    ordered = sorted(regions, key=lambda r: r.start)
    gaps: list[tuple[float, float]] = []
    cursor = 0.0

    for region in ordered:
        if cursor >= duration:
            break
        gap_end = min(region.start, duration)
        if gap_end > cursor:
            gaps.append((cursor, gap_end))
        cursor = max(cursor, min(region.end, duration))

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
