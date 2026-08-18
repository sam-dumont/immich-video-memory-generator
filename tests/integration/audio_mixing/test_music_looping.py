"""Real-FFmpeg checks that looped music has no audible seam.

The probe tone ramps from silence to full amplitude and ends a quarter cycle
short, so its last sample sits at peak while its first sits at zero. A butt
spliced loop leaves a step of ~1.0 there; a crossfaded one does not.
"""

from __future__ import annotations

import subprocess
import wave

import numpy as np
import pytest

from immich_memories.audio.mixer import loop_audio_to_duration

# WHY: FFmpeg only — unlike its neighbours here it needs no ML model, so it is
# not pinned to the heavy audio_ml xdist group.
pytestmark = pytest.mark.integration

SAMPLE_RATE = 44100
TONE_SECONDS = 4.0
# f * duration = whole cycles + 1/4, so the tone ends on a positive peak.
TONE_HZ = (1760 + 0.25) / TONE_SECONDS


def _write_probe_tone(path) -> None:
    count = int(SAMPLE_RATE * TONE_SECONDS)
    t = np.arange(count) / SAMPLE_RATE
    envelope = t / TONE_SECONDS
    signal = envelope * np.sin(2 * np.pi * TONE_HZ * t)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype("<i2").tobytes())


def _samples(path) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


@pytest.fixture
def probe_tone(tmp_path):
    path = tmp_path / "tone.wav"
    _write_probe_tone(path)
    return path


def test_the_probe_tone_really_does_end_far_from_where_it_starts(probe_tone):
    """Guards the test itself: without this gap the seam check proves nothing."""
    samples = _samples(probe_tone)

    assert abs(samples[-1] - samples[0]) > 0.8


def test_looping_reaches_the_requested_duration(probe_tone, tmp_path):
    out = loop_audio_to_duration(probe_tone, 15.0, tmp_path / "looped.wav")

    assert len(_samples(out)) / SAMPLE_RATE == pytest.approx(15.0, abs=0.15)


def test_loop_seams_do_not_step(probe_tone, tmp_path):
    out = loop_audio_to_duration(probe_tone, 15.0, tmp_path / "looped.wav")

    biggest_step = float(np.abs(np.diff(_samples(out))).max())

    # The waveform's own slope peaks near 0.06; a butt splice would step ~1.0.
    assert biggest_step < 0.25


def test_a_track_longer_than_the_target_is_simply_trimmed(probe_tone, tmp_path):
    out = loop_audio_to_duration(probe_tone, 2.0, tmp_path / "trimmed.wav")

    assert len(_samples(out)) / SAMPLE_RATE == pytest.approx(2.0, abs=0.15)
