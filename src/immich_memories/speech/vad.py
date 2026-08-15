from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Protocol

import numpy as np

from immich_memories.config_models import SpeechConfig
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


def select_detector(config: SpeechConfig) -> SpeechDetector | None:
    """Build the detector for `config.engine`, or `None` for no VAD.

    `"energy"` has no detector implementation yet -- it falls through to
    `None`, leaving PANNs-derived protected ranges untouched (see
    `_apply_vad_ranges`). `config.engine` is a `Literal`, so an unrecognized
    value fails Pydantic validation before it ever reaches this function.
    """
    if not config.enabled:
        return None

    if config.engine == "fireredvad":
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        return FireRedSpeechDetector(
            threshold=config.vad_threshold, min_silence_ms=config.min_silence_ms
        )

    return None
