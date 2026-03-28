"""Fixtures for processing integration tests: probing, runner, filter graphs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def portrait_clip(fixtures_dir: Path) -> Path:
    """2-second 720x1280 portrait H.264 clip with AAC audio."""
    out = fixtures_dir / "portrait_720x1280.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@pytest.fixture(scope="session")
def no_audio_clip(fixtures_dir: Path) -> Path:
    """2-second 1280x720 H.264 clip WITHOUT audio."""
    out = fixtures_dir / "no_audio_720p.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-an",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@pytest.fixture(scope="session")
def short_clip(fixtures_dir: Path) -> Path:
    """1-second 640x480 H.264 clip with audio. Exercises small-file probing."""
    out = fixtures_dir / "short_640x480.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x480:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out
