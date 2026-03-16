"""Fixtures for integration tests that run real FFmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.integration
requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    """Session-scoped temp dir for generated test fixtures."""
    return tmp_path_factory.mktemp("integration_fixtures")


@pytest.fixture(scope="session")
def test_clip_720p(fixtures_dir) -> Path:
    """3-second 720p H.264 clip with AAC audio."""
    out = fixtures_dir / "test_720p.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
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
def test_clip_720p_b(fixtures_dir) -> Path:
    """Different 3-second 720p clip (different testsrc2 seed)."""
    out = fixtures_dir / "test_720p_b.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=3:alpha=160",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
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
def test_music_short(fixtures_dir) -> Path:
    """5-second sine wave as MP3."""
    out = fixtures_dir / "test_music.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=261:duration=5",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


def ffprobe_json(path: Path) -> dict:
    """Run ffprobe and return parsed JSON output."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    import json

    return json.loads(result.stdout)


def has_stream(probe_data: dict, codec_type: str) -> bool:
    """Check if probe data has a stream of given type."""
    return any(s.get("codec_type") == codec_type for s in probe_data.get("streams", []))


def get_duration(probe_data: dict) -> float:
    """Get duration from probe data."""
    return float(probe_data.get("format", {}).get("duration", 0))
