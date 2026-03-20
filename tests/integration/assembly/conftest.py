"""Assembly-specific fixtures: multi-clip sets at various resolutions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def test_clip_1080p(fixtures_dir: Path) -> Path:
    """5-second 1080p H.264 clip with audio."""
    out = fixtures_dir / "test_1080p.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
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
def test_clip_1080p_b(fixtures_dir: Path) -> Path:
    """Different 5-second 1080p clip."""
    out = fixtures_dir / "test_1080p_b.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5:alpha=160",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=5",
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
def test_clip_1080p_c(fixtures_dir: Path) -> Path:
    """Third 5-second 1080p clip."""
    out = fixtures_dir / "test_1080p_c.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=5:alpha=80",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=5",
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


def make_n_clips(
    fixtures_dir: Path, n: int, resolution: str = "1920x1080", duration: int = 5
) -> list[Path]:
    """Generate n synthetic clips at a given resolution. Deterministic."""
    clips = []
    for i in range(n):
        out = fixtures_dir / f"perf_clip_{resolution}_{i:02d}.mp4"
        if not out.exists():
            freq = 220 + i * 110  # Different audio per clip
            alpha = 40 + i * 30  # Different visual per clip
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size={resolution}:rate=30:duration={duration}:alpha={alpha % 256}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={freq}:duration={duration}",
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
                timeout=60,
            )
        clips.append(out)
    return clips
