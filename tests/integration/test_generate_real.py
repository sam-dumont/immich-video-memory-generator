"""Integration test for generate_memory() — real FFmpeg, real assembly, no mocks."""

from __future__ import annotations

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


@pytest.fixture
def two_clips(test_clip_720p, test_clip_720p_b):
    """Return two real test clips as VideoClipInfo objects with local paths."""
    from tests.conftest import make_clip

    clip_a = make_clip("clip-a", duration=3.0, width=1280, height=720)
    clip_a.local_path = str(test_clip_720p)
    clip_b = make_clip("clip-b", duration=3.0, width=1280, height=720)
    clip_b.local_path = str(test_clip_720p_b)
    return [clip_a, clip_b]


class TestGenerateMemoryIntegration:
    def test_generates_valid_video_from_clips(self, two_clips, tmp_path):
        """generate_memory() should produce a real video file from test clips."""
        from datetime import date

        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        output = tmp_path / "test_memory.mp4"
        progress_calls = []

        config = Config()
        config.title_screens.enabled = False  # Skip title generation for speed

        params = GenerationParams(
            clips=two_clips,
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            progress_callback=lambda phase, _pct, _msg: progress_calls.append(phase),
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 0

        # Verify it's a valid video
        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 0

        # Progress callback was called
        assert len(progress_calls) > 0

    def test_single_clip_passthrough(self, test_clip_720p, tmp_path):
        """Single clip should produce valid output (no crossfade needed)."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory
        from tests.conftest import make_clip

        clip = make_clip("single", duration=3.0, width=1280, height=720)
        clip.local_path = str(test_clip_720p)

        from datetime import date

        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "single.mp4"
        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            transition="cut",
            date_start=date(2025, 6, 1),
            date_end=date(2025, 6, 30),
        )

        result = generate_memory(params)
        assert result.exists()
        assert result.stat().st_size > 0
