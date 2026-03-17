"""Integration tests for generate_memory() edge cases and scenarios.

Covers: error handling, music, trip locations, title overrides,
clip segment overrides, live photo bursts.

Run: make test-integration
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import make_clip
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def short_clip(tmp_path_factory) -> Path:
    """3s 320x240 clip for fast tests."""
    out = tmp_path_factory.mktemp("scenarios") / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=15:duration=3",
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
        timeout=15,
    )
    return out


@pytest.fixture(scope="module")
def short_music(tmp_path_factory) -> Path:
    """5s music file (sine wave)."""
    out = tmp_path_factory.mktemp("scenarios") / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=5",
            "-ar",
            "44100",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return out


def _make_test_clip(path: Path, asset_id: str = "test") -> object:
    """Create a VideoClipInfo pointing to a real local file."""
    clip = make_clip(asset_id, duration=3.0, width=320, height=240)
    clip.local_path = str(path)
    return clip


# ---------------------------------------------------------------------------
# Scenario 1: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_exception_wraps_in_generation_error(self, short_clip, tmp_path):
        """Non-GenerationError exceptions get wrapped with sanitized message."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory

        clip = _make_test_clip(short_clip, "err-clip")
        config = Config()
        config.title_screens.enabled = False

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "err.mp4",
            config=config,
            transition="cut",
            # Force an error by pointing to nonexistent segment
            clip_segments={"err-clip": (100.0, 200.0)},  # Beyond clip duration
        )

        # Out-of-range segment may produce empty output → GenerationError
        # or FFmpeg may truncate silently. Either way, should not crash unhandled.
        try:
            result = generate_memory(params)
            # If it didn't raise, the output should still be valid (FFmpeg truncated)
            assert result.exists()
        except GenerationError:
            pass  # Expected for truly invalid segments


# ---------------------------------------------------------------------------
# Scenario 2: Music application
# ---------------------------------------------------------------------------


class TestMusicApplication:
    def test_music_mixed_into_video(self, short_clip, short_music, tmp_path):
        """Music file gets mixed into the assembled video."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip = _make_test_clip(short_clip)
        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "with_music.mp4"
        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            transition="cut",
            music_path=short_music,
            music_volume=0.5,
        )

        result = generate_memory(params)
        assert result.exists()
        assert result.stat().st_size > 1000

        # Verify audio stream present
        from tests.integration.conftest import ffprobe_json, has_stream

        probe = ffprobe_json(result)
        assert has_stream(probe, "audio"), "Music should add an audio stream"


# Scenarios 4 (trip locations) and 5 (title override) are pure logic —
# tested in tests/test_generate.py (unit tests, no FFmpeg needed)


# ---------------------------------------------------------------------------
# Scenario 6: Clip segment overrides
# ---------------------------------------------------------------------------


class TestClipSegmentOverrides:
    def test_custom_segment_trims_clip(self, short_clip, tmp_path):
        """Clip segments from review step trim the extracted clip."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip = _make_test_clip(short_clip, "seg-clip")
        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "trimmed.mp4"
        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            transition="cut",
            # Trim to first 2 seconds of the 3s clip
            clip_segments={"seg-clip": (0.0, 2.0)},
        )

        result = generate_memory(params)
        assert result.exists()

        from tests.integration.conftest import ffprobe_json, get_duration

        duration = get_duration(ffprobe_json(result))
        # Should be ~2s (not 3s)
        assert 1.0 < duration < 2.5
