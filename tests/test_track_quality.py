"""The cheap degeneracy gate flags metronomic repetition and passes varied music.

Detection is numpy + ffmpeg on purpose, matching ``test_track_tempo``: librosa
is not a project dependency. The gate reduced to onset periodicity after the
control-render calibration in #1007: a metronome and a stuck loop both pin nearly
all their onset energy to one repeat lag, while a varied track spreads it.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from immich_memories.audio.track_quality import score_track

_SAMPLE_RATE = 22050


def _metronome(path: Path, bpm: int = 100, seconds: int = 12) -> Path:
    """A 1200 Hz pulse gated on for 60 ms each beat - the degenerate metronome."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1200:duration={seconds}:sample_rate={_SAMPLE_RATE}",
            "-af",
            f"atrim=0:{seconds},asetrate={_SAMPLE_RATE},"
            f"volume='if(lt(mod(t*{bpm}/60,1),0.06),1,0)':eval=frame",
            str(path),
        ],
        check=True,
    )
    return path


def _irregular_rhythm(path: Path, seconds: int = 12) -> Path:
    """Tone bursts at non-repeating onsets over a light bed - varied, not metronomic."""
    t = np.arange(0, seconds * _SAMPLE_RATE) / _SAMPLE_RATE
    x = np.zeros_like(t)
    for onset in (0.3, 1.7, 2.05, 3.9, 5.2, 5.55, 8.0, 8.7, 10.3, 11.2):
        i = int(onset * _SAMPLE_RATE)
        j = min(i + int(0.09 * _SAMPLE_RATE), x.size)
        x[i:j] = 0.6 * np.sin(2 * np.pi * 440 * np.arange(j - i) / _SAMPLE_RATE)
    x = np.tanh(x + 0.04 * np.random.default_rng(0).standard_normal(x.size))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes((x * 32767).astype(np.int16).tobytes())
    return path


def _pink_noise(path: Path, seconds: int = 12) -> Path:
    """A steady bed with no rhythm at all - continuous, so not metronomic."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=duration={seconds}:c=pink:a=0.5",
            str(path),
        ],
        check=True,
    )
    return path


def test_a_metronome_is_flagged(tmp_path: Path) -> None:
    quality = score_track(_metronome(tmp_path / "tick.wav"))

    assert quality is not None
    assert quality.flagged


def test_a_metronome_at_any_plausible_rate_is_flagged(tmp_path: Path) -> None:
    for bpm in (60, 120, 180):
        quality = score_track(_metronome(tmp_path / f"tick{bpm}.wav", bpm=bpm))

        assert quality is not None
        assert quality.flagged


def test_varied_music_with_irregular_onsets_is_not_flagged(tmp_path: Path) -> None:
    quality = score_track(_irregular_rhythm(tmp_path / "music.wav"))

    assert quality is not None
    assert not quality.flagged


def test_a_steady_noise_bed_is_not_flagged(tmp_path: Path) -> None:
    quality = score_track(_pink_noise(tmp_path / "bed.wav"))

    assert quality is not None
    assert not quality.flagged


def test_a_tick_outranks_music_for_the_keep_best_choice(tmp_path: Path) -> None:
    tick = score_track(_metronome(tmp_path / "tick.wav"))
    music = score_track(_irregular_rhythm(tmp_path / "music.wav"))

    assert tick is not None and music is not None
    assert tick.score > music.score


def test_an_unreadable_file_is_none_not_flagged(tmp_path: Path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")

    assert score_track(broken) is None
