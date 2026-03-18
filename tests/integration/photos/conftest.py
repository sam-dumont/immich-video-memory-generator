"""Fixtures for photo animation integration tests.

Generates test JPEG images via FFmpeg testsrc2 — no real photos in repo.
Landscape (1920x1080), portrait (1080x1920), and square (1080x1080) variants.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def test_photo_landscape(fixtures_dir) -> Path:
    """1920x1080 landscape test JPEG."""
    out = fixtures_dir / "photo_landscape.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=1:duration=1",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="session")
def test_photo_portrait(fixtures_dir) -> Path:
    """1080x1920 portrait test JPEG."""
    out = fixtures_dir / "photo_portrait.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1920:rate=1:duration=1",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="session")
def test_photo_square(fixtures_dir) -> Path:
    """1080x1080 square test JPEG."""
    out = fixtures_dir / "photo_square.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1080:rate=1:duration=1",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="session")
def test_photo_4k(fixtures_dir) -> Path:
    """3840x2160 4K landscape test JPEG (high-res source for zoom quality)."""
    out = fixtures_dir / "photo_4k.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=3840x2160:rate=1:duration=1",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out
