"""Integration tests that run real FFmpeg — not mocked.

These verify the actual video assembly pipeline produces valid output.
Skipped if FFmpeg is not available. Run with: make test-integration
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg]


@pytest.fixture
def assembler():
    """Create a VideoAssembler with fast settings."""
    from immich_memories.processing.assembly_config import AssemblySettings, TransitionType

    settings = AssemblySettings(
        transition=TransitionType.CROSSFADE,
        transition_duration=0.3,
        output_crf=28,  # fast, lower quality
    )

    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(settings)


class TestSingleClipAssembly:
    def test_single_clip_produces_valid_output(self, assembler, test_clip_720p, tmp_path):
        """Assembling one clip should produce a valid video file."""
        from immich_memories.processing.assembly_config import AssemblyClip

        output = tmp_path / "single.mp4"
        clip = AssemblyClip(path=test_clip_720p, duration=3.0)
        result = assembler.assemble([clip], output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 0


class TestDefaultSettings:
    def test_none_defaults_use_config_fallback(self, test_clip_720p, tmp_path):
        """AssemblySettings with None CRF/transition uses config defaults."""
        from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
        from immich_memories.processing.video_assembler import VideoAssembler

        # None triggers the `or 0.5` / config fallback paths
        settings = AssemblySettings(output_crf=None, transition_duration=None)
        assembler = VideoAssembler(settings)
        output = tmp_path / "defaults.mp4"
        clip = AssemblyClip(path=test_clip_720p, duration=3.0)
        result = assembler.assemble([clip], output)

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 0


class TestCrossfadeTransition:
    def test_two_clips_with_crossfade(self, assembler, test_clip_720p, test_clip_720p_b, tmp_path):
        """Two clips with crossfade should produce output shorter than sum of inputs."""
        from immich_memories.processing.assembly_config import AssemblyClip

        output = tmp_path / "crossfade.mp4"
        clips = [
            AssemblyClip(path=test_clip_720p, duration=3.0),
            AssemblyClip(path=test_clip_720p_b, duration=3.0),
        ]
        result = assembler.assemble(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # Two 3s clips with 0.3s crossfade = ~5.7s (not 6.0)
        assert 4.0 < duration < 6.5


class TestTitleScreenPIL:
    def test_pil_title_renders_valid_video(self, tmp_path):
        """Title screen generation should produce a valid video segment."""
        from immich_memories.titles.convenience import generate_title_screen

        output = tmp_path / "title.mp4"

        # WHY: convenience.generate_title_screen uses PIL fallback when Taichi unavailable
        generate_title_screen(
            title="Test Title 2024",
            output_path=output,
            duration=2.0,
            resolution=(1280, 720),
        )

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert 1.0 < duration < 4.0
