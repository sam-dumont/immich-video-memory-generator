"""Cheap degeneracy gate: is a generated track metronomically repetitive?

The control renders behind #1007 were degenerate in a measurable way: the
generated tracks lock onto one rhythm with no syncopation, variation or
structure. The bundled (curated) tracks measure an onset-autocorrelation
periodicity of 0.35-0.61; the failing generated tracks measure 0.65-0.98. The
"tic tac" was just the extreme end of that clip, not a separate disease.

So the gate reduces to one feature: how much of the onset energy sits at a
single repeat lag. A rich track spreads its onsets (fills, syncopation, chord
changes) so the beat accounts for only part of the energy; a metronomic loop
puts nearly all of it on one lag. The crest factor of the frame-energy envelope
is still computed and reported because it separates a sparse tick from a dense
drone when someone is triaging a catch, but it does not gate: real degenerate
tracks were every bit as "filled" as good ones after mastering.

A full music-quality model is out of scope for a gate that runs on every take,
so this is numpy over an ffmpeg mono decode, the same budget as ``track_tempo``
and for the same reason (librosa is not a dependency).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from immich_memories.audio.audio_decode import decode_mono_float

_SAMPLE_RATE = 22050
_FRAME = 512
# The smallest repeat lag considered, in frames. Below this, "repetition" is
# two adjacent frames of one sustained transient, not a recurring tick. At
# 22050/512 = 43 fps this floor is just under 100 ms, safely short of a clock.
_MIN_LAG_FRAMES = 4

# Calibrated on the retained control-render tracks: the curated/bundled library
# measures 0.35-0.61, the degenerate generated tracks 0.65-0.98. The two bands
# do not touch. The cost of an over-eager flag is a bounded regeneration, never
# a dropped track, so this leans trigger-happy on purpose; a motorik EDM track
# would be a false rejection, which is the documented limit of this cheap gate.
_PULSE_THRESHOLD = 0.65


@dataclass(frozen=True, slots=True)
class TrackQuality:
    """Degeneracy features of a track; higher periodicity is more metronomic."""

    periodicity: float  # 0..1, dominant peak of the onset autocorrelation
    spikiness: float  # >= 1, crest factor of the frame-energy envelope (diagnostic)

    @property
    def flagged(self) -> bool:
        """Too metronomic to ship without trying another take."""
        return self.periodicity >= _PULSE_THRESHOLD

    @property
    def score(self) -> float:
        """0..1 degeneracy for ranking candidates (lower = better)."""
        return min(self.periodicity, 1.0)


def _energy_envelope(samples: np.ndarray) -> np.ndarray:
    frames = samples[: samples.size - samples.size % _FRAME].reshape(-1, _FRAME)
    return np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))


def score_track(path: Path) -> TrackQuality | None:
    """Degeneracy features of ``path``, or None when it cannot be read.

    A file that cannot be decoded is a technical failure, not a quality
    verdict; callers keep their own fallback for that case.
    """
    samples = decode_mono_float(path, _SAMPLE_RATE)
    if samples is None or samples.size < _SAMPLE_RATE:
        return None

    energy = _energy_envelope(samples)

    onset = np.diff(energy, prepend=energy[:1]).clip(min=0)
    onset -= onset.mean()
    zero_lag = float(onset @ onset)
    if zero_lag > 0:
        corr = np.correlate(onset, onset, mode="full")[onset.size - 1 :]
        peak = float(corr[_MIN_LAG_FRAMES:].max())
        periodicity = peak / zero_lag
    else:
        periodicity = 0.0

    rms = float(np.sqrt((energy**2).mean()))
    spikiness = float(energy.max() / rms) if rms > 0 else 0.0

    return TrackQuality(periodicity=periodicity, spikiness=spikiness)
