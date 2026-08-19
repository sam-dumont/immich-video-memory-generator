"""Real-FFmpeg checks for the music mastering chain.

Generated and bundled tracks come out darker and at inconsistent loudness than
music the ear expects under video: measured against real Suno tracks, ours carried
~1% of energy above 6 kHz against their 2.3-2.7%, and integrated loudness ranged
over 3.8 LU across moods.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from immich_memories.audio.mastering import master_music_track

pytestmark = pytest.mark.integration

SR = 48000


def _write_dull_tone(path) -> None:
    """A deliberately dark, quiet source: harmonics that taper off sharply."""
    count = int(SR * 6)
    t = np.arange(count) / SR
    signal = sum(
        np.sin(2 * np.pi * f * t) / (i + 1) ** 2
        for i, f in enumerate((220, 440, 880, 1760, 3520, 7040))
    )
    signal = 0.2 * signal / np.abs(signal).max()
    raw = (signal * 32767).astype("<i2").tobytes()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(SR),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            str(path),
        ],
        input=raw,
        check=True,
    )


def _samples(path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def _energy_above(x: np.ndarray, hz: int, n: int = 8192) -> float:
    frames = [np.abs(np.fft.rfft(x[i : i + n] * np.hanning(n))) for i in range(0, len(x) - n, n)]
    power = np.mean(frames, axis=0) ** 2
    freqs = np.fft.rfftfreq(n, 1 / SR)
    return 100 * power[freqs >= hz].sum() / power.sum()


@pytest.fixture
def dull_track(tmp_path):
    path = tmp_path / "dull.wav"
    _write_dull_tone(path)
    return path


def test_mastering_lifts_the_high_end(dull_track, tmp_path):
    out = master_music_track(dull_track, tmp_path / "mastered.wav")

    assert _energy_above(_samples(out), 6000) > _energy_above(_samples(dull_track), 6000)


def test_mastering_lifts_the_band_it_claims_to(dull_track, tmp_path):
    """Checked above 10 kHz too: an exciter can game a >6 kHz figure while
    carving a hole higher up, which is how the first attempt at this went wrong."""
    out = master_music_track(dull_track, tmp_path / "mastered.wav")

    assert _energy_above(_samples(out), 10000) > _energy_above(_samples(dull_track), 10000)


def test_mastering_respects_the_ceiling(dull_track, tmp_path):
    """ffmpeg's alimiter auto-normalises unless told not to, ignoring the ceiling."""
    out = master_music_track(dull_track, tmp_path / "mastered.wav")

    assert np.abs(_samples(out)).max() <= 10 ** (-1.0 / 20) + 0.02


def test_mastering_keeps_the_sample_rate(dull_track, tmp_path):
    """loudnorm resamples to 192 kHz unless an output rate is pinned."""
    out = master_music_track(dull_track, tmp_path / "mastered.wav")
    rate = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "default=nw=1:nk=1",
            str(out),
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    assert rate == str(SR)
