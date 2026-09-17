"""Real-FFmpeg checks that multi-block assembly reaches its duration seam-free.

Three probe tones at different frequencies stand in for three distinct same-caption
takes. Each starts at zero and ends on a peak, so a butt splice would step; the
crossfaded fold must not.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from immich_memories.audio.mixer import assemble_music

# WHY: FFmpeg only — no ML model, so it is not pinned to the heavy audio_ml xdist group.
pytestmark = pytest.mark.integration

SAMPLE_RATE = 44100
TONE_SECONDS = 4.0


def _write_tone(path: Path, hz: float) -> None:
    count = int(SAMPLE_RATE * TONE_SECONDS)
    t = np.arange(count) / SAMPLE_RATE
    envelope = t / TONE_SECONDS
    signal = envelope * np.sin(2 * np.pi * (hz + 0.25 / TONE_SECONDS) * t)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype("<i2").tobytes())


def _samples(path: Path) -> np.ndarray:
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
def blocks(tmp_path: Path) -> list[Path]:
    paths = []
    for i, hz in enumerate((440.0, 660.0, 880.0)):
        path = tmp_path / f"block{i}.wav"
        _write_tone(path, hz)
        paths.append(path)
    return paths


def test_assembly_reaches_the_requested_duration(blocks, tmp_path: Path) -> None:
    out = assemble_music(blocks, 15.0, tmp_path / "assembled.wav")

    assert len(_samples(out)) / SAMPLE_RATE == pytest.approx(15.0, abs=0.15)


def test_assembly_seams_do_not_step(blocks, tmp_path: Path) -> None:
    out = assemble_music(blocks, 15.0, tmp_path / "assembled.wav")

    biggest_step = float(np.abs(np.diff(_samples(out))).max())

    assert biggest_step < 0.25


def test_assembly_short_target_trims_without_a_step(blocks, tmp_path: Path) -> None:
    out = assemble_music(blocks, 6.0, tmp_path / "short.wav")

    assert len(_samples(out)) / SAMPLE_RATE == pytest.approx(6.0, abs=0.15)
    assert float(np.abs(np.diff(_samples(out))).max()) < 0.25
