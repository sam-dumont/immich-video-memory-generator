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
    from immich_memories.processing.assembly_config import (
        AssemblySettings,
        TransitionType,
        standalone_assembly_encoding_plan,
    )

    settings = AssemblySettings(
        encoding_plan=standalone_assembly_encoding_plan(28),
        transition=TransitionType.CROSSFADE,
        transition_duration=0.3,
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

    def test_public_single_clip_honors_prores_mov_plan(self, test_clip_720p, tmp_path):
        """An H.264 source cannot byte-copy past a resolved ProRes/MOV contract."""
        from immich_memories.processing.assembly_config import (
            AssemblyClip,
            AssemblySettings,
        )
        from immich_memories.processing.encoding_plan import (
            EncodingPlan,
            HdrTransfer,
            OutputCodec,
        )
        from immich_memories.processing.video_assembler import VideoAssembler

        plan = EncodingPlan(
            codec=OutputCodec.PRORES,
            encoder="prores_ks",
            encoder_args=("-profile:v", "3"),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv422p10le",
            container="mov",
        )
        assembler = VideoAssembler(
            AssemblySettings(
                encoding_plan=plan,
                auto_resolution=False,
                target_resolution=(320, 240),
                scale_mode="black",
            )
        )
        output = tmp_path / "single.mov"

        assembler.assemble(
            [AssemblyClip(path=test_clip_720p, duration=0.5)],
            output,
        )

        probe = ffprobe_json(output)
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        assert video["codec_name"] == "prores"
        assert video["color_space"] == "bt709"
        assert video["color_primaries"] == "bt709"
        assert video["color_transfer"] == "bt709"
        assert "mov" in probe["format"]["format_name"]


class TestDefaultSettings:
    def test_none_defaults_use_config_fallback(self, test_clip_720p, tmp_path):
        """AssemblySettings with None CRF/transition uses config defaults."""
        from immich_memories.processing.assembly_config import (
            AssemblyClip,
            AssemblySettings,
            standalone_assembly_encoding_plan,
        )
        from immich_memories.processing.video_assembler import VideoAssembler

        settings = AssemblySettings(
            encoding_plan=standalone_assembly_encoding_plan(), transition_duration=None
        )
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
