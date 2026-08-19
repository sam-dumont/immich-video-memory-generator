"""Tempo picked so the photo cut cadence lands on whole beats.

The pipeline decides its cuts before it has any music: clips are chosen for
content, and photos get a fixed screen time. So the music adapts to the cuts
rather than the other way round — we ask the generator for a tempo whose beat
divides the photo cadence exactly, instead of moving cuts to fit a track.
"""

from __future__ import annotations

# How far the tempo may be pulled to reach a whole-beat cadence. Alignment is
# worth a nudge, not a mood: short photos are a whole beat only at tempos that
# would turn a serene track into a dance one.
_MAX_TEMPO_SHIFT = 0.15


def beat_aligned_bpm(
    natural_bpm: float,
    cadence_seconds: float,
    tempo_range: tuple[int, int],
) -> int:
    """Nearest tempo to ``natural_bpm`` whose beat divides ``cadence_seconds``.

    Only tempos where the cadence is a whole number of beats are considered, so
    a photo lasts exactly N beats and every cut in a run of photos falls on the
    same grid. Stays inside the style's own tempo range and within a short reach
    of the mood's own tempo; where neither holds, the mood wins and the natural
    tempo is returned unchanged.
    """
    if cadence_seconds <= 0 or natural_bpm <= 0:
        return int(round(natural_bpm))

    low, high = tempo_range
    beats = _beats_per_cadence(natural_bpm, cadence_seconds)
    span = natural_bpm * _MAX_TEMPO_SHIFT
    candidates = [
        n
        for n in (beats, beats + 1)
        if n >= 1
        and low <= _bpm_for(n, cadence_seconds) <= high
        and abs(_bpm_for(n, cadence_seconds) - natural_bpm) <= span
    ]
    if not candidates:
        return int(round(natural_bpm))

    best = min(candidates, key=lambda n: abs(_bpm_for(n, cadence_seconds) - natural_bpm))
    return int(round(_bpm_for(best, cadence_seconds)))


def _beats_per_cadence(bpm: float, cadence_seconds: float) -> int:
    return int(cadence_seconds * bpm / 60.0)


def _bpm_for(beats: int, cadence_seconds: float) -> float:
    return 60.0 * beats / cadence_seconds
