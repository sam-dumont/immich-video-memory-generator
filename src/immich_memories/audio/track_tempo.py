"""Measure a bundled track's tempo, and pick the one that fits the cut cadence.

#312's first half asks a generator for a tempo whose beat divides the photo
cadence. A bundled track cannot be asked — its tempo is already fixed — so the
choice runs the other way: measure what ships, then pick from the tracks that
land on the cadence.

Detection is numpy over an ffmpeg decode on purpose. librosa would be one line,
but it is not a dependency of this project (it arrives transitively with the
torch extras), so importing it would fail `make dep-check` and break a plain
install that has music but no GPU stack.

The method is an onset envelope plus autocorrelation: frame the signal, take the
positive energy differences between frames, and find the lag whose repetition is
strongest. That is the beat period.
"""

from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 22050
_FRAME = 512
# Outside this range a "tempo" is either a half-time artefact or a drone.
_MIN_BPM = 60
_MAX_BPM = 180
# How near a whole number of beats a cadence must fall to count as aligned.
# Set by what the detector can actually resolve, not by taste. Autocorrelation
# quantizes the beat period to whole 23 ms frames, so a click track built at a
# known tempo reads back a few BPM out — 120 measures 117.5, 110 measures 107.7 —
# and a track that truly lands on a 4 s cadence still measures up to ~0.18 beats
# off. A tighter gate would reject tracks that do fit and would only be measuring
# the detector's own noise. A real miss (100 BPM against 4 s) measures 0.37, so
# the two are still separated. Narrow this only alongside a finer onset hop.
_BEAT_TOLERANCE = 0.2


def _decode_mono(path: Path) -> np.ndarray | None:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(_SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32)


def detect_bpm(path: Path) -> float | None:
    """Tempo of a track in BPM, or None when it cannot be read or has no pulse."""
    samples = _decode_mono(path)
    if samples is None or samples.size < _SAMPLE_RATE:
        return None

    frames = samples[: samples.size - samples.size % _FRAME].reshape(-1, _FRAME)
    energy = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    # WHY only rises: a beat is where energy arrives, not where it decays.
    onset = np.diff(energy, prepend=energy[:1]).clip(min=0)
    onset -= onset.mean()
    if not onset.any():
        return None

    correlation = np.correlate(onset, onset, mode="full")[onset.size - 1 :]
    frames_per_second = _SAMPLE_RATE / _FRAME
    lo = int(frames_per_second * 60 / _MAX_BPM)
    hi = min(int(frames_per_second * 60 / _MIN_BPM), correlation.size - 1)
    if hi <= lo:
        return None

    lag = lo + int(np.argmax(correlation[lo:hi]))
    return round(60 * frames_per_second / lag, 1) if lag else None


def _beat_misfit(bpm: float, cadence_seconds: float) -> float:
    """How far the cadence sits from a whole number of beats, in beats."""
    beats = cadence_seconds * bpm / 60
    return abs(beats - round(beats))


def track_for_cadence(candidates: list[Path], cadence_seconds: float) -> Path | None:
    """One of the candidates whose beat lands on the cadence, chosen at random.

    Returning the single least-misfitting track would hand the same memory the
    same song forever, however badly the winner actually fit — so the tolerance
    decides who qualifies and chance decides between them.

    A track that cannot be read costs itself its turn, not the run. None means
    nothing was near enough, and the caller's own fallback applies.
    """
    if not candidates or cadence_seconds <= 0:
        return None

    aligned = []
    for track in candidates:
        bpm = detect_bpm(track)
        if bpm is None:
            logger.debug("No tempo read from %s; skipping it for cadence matching", track.name)
            continue
        if _beat_misfit(bpm, cadence_seconds) <= _BEAT_TOLERANCE:
            aligned.append(track)

    if not aligned:
        logger.debug("No bundled track lands on a %.1fs cadence", cadence_seconds)
        return None
    chosen = random.choice(aligned)  # noqa: S311  # WHY: variety, not secrecy
    logger.info(
        "Bundled track %s fits a %.1fs cadence (%d of %d did)",
        chosen.name,
        cadence_seconds,
        len(aligned),
        len(candidates),
    )
    return chosen
