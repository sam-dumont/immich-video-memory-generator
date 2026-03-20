"""Integration tests for the streaming assembly path in AssemblyEngine.

Verifies that the engine's assemble_scalable() method produces valid output
using the streaming frame blender instead of the filter graph.
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


def _make_settings(**overrides):
    from immich_memories.processing.assembly_config import AssemblySettings, TransitionType

    defaults = {
        "transition": TransitionType.CROSSFADE,
        "transition_duration": 0.3,
        "output_crf": 28,
        "preserve_hdr": False,
        "auto_resolution": False,
        "target_resolution": (1280, 720),
        "normalize_clip_audio": False,
    }
    defaults.update(overrides)
    return AssemblySettings(**defaults)


def _make_clip(path, duration=3.0, **kwargs):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration, **kwargs)


class TestStreamingViaEngine:
    def test_two_clips_crossfade(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Engine should assemble two clips with crossfade via streaming."""
        from immich_memories.processing.video_assembler import VideoAssembler

        settings = _make_settings()
        assembler = VideoAssembler(settings)
        output = tmp_path / "streaming_xfade.mp4"
        clips = [
            _make_clip(test_clip_720p, 3.0),
            _make_clip(test_clip_720p_b, 3.0),
        ]
        result = assembler.assemble(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert get_duration(probe) > 3.0

    def test_many_clips_no_chunking(self, fixtures_dir, tmp_path):
        """Streaming should handle >8 clips without chunking."""
        from immich_memories.processing.video_assembler import VideoAssembler
        from tests.integration.assembly.conftest import make_n_clips

        clip_paths = make_n_clips(fixtures_dir, 10, "320x240", duration=2)
        settings = _make_settings(target_resolution=(320, 240))
        assembler = VideoAssembler(settings)
        output = tmp_path / "streaming_many.mp4"
        clips = [_make_clip(p, 2.0, asset_id=f"many-{i}") for i, p in enumerate(clip_paths)]
        result = assembler.assemble(clips, output)

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 10.0
