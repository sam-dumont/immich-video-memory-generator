"""Live Photo burst alignment reads the clips' own audio, on a default install.

Run with: make test-integration-processing
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _clip(source: Path, start: float, seconds: float, dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-t", str(seconds), "-i", str(source),
            "-c:a", "aac", "-b:a", "128k", str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )  # fmt: skip
    return dest


def test_the_second_clip_is_placed_where_its_audio_is_not_where_its_shutter_says(tmp_path):
    from immich_memories.processing.live_photo_merger import align_clips_spectrogram

    # A chirp is unique at every instant, so there is exactly one right answer.
    scene = tmp_path / "scene.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "aevalsrc=sin(2*PI*(200*t+300*t*t)):s=48000:d=6",
            str(scene),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )  # fmt: skip
    first = _clip(scene, 0.0, 3.0, tmp_path / "first.m4a")
    second = _clip(scene, 1.5, 3.0, tmp_path / "second.m4a")

    # The shutters claim a 1.0 s gap; the audio says the second clip starts 1.5 s in.
    video_trims, _ = align_clips_spectrogram([first, second], [100.0, 101.0], [3.0, 3.0])

    assert video_trims[0][1] == pytest.approx(1.5, abs=0.05)
