"""Fixtures for audio integration tests.

These tests run real ML models (Demucs, ACE-Step) — expensive and slow.
Only run via: make test-integration-audio
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# WHY: audio ML tests are heavy (model downloads, GPU inference).
# Serialize on one worker, skip if deps missing.
pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("audio_ml")]


def _has_demucs() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("demucs") is not None
    except (ImportError, ModuleNotFoundError):
        return False


def _has_acestep() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("acestep") is not None
    except (ImportError, ModuleNotFoundError):
        return False


requires_demucs = pytest.mark.skipif(not _has_demucs(), reason="demucs not installed")
requires_acestep = pytest.mark.skipif(not _has_acestep(), reason="acestep not installed")


@pytest.fixture(scope="session")
def audio_fixtures_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("audio_fixtures")


@pytest.fixture(scope="session")
def test_audio_5s(audio_fixtures_dir) -> Path:
    """5-second stereo WAV with mixed sine waves (simulates simple music)."""
    out = audio_fixtures_dir / "test_5s.wav"
    # WHY: Demucs htdemucs expects stereo input (2 channels).
    # Two sine waves panned L/R via amerge to get a stereo file.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=5",
            "-filter_complex",
            "amerge=inputs=2",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out
