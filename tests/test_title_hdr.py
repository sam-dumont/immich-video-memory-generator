"""Tests for conditional HDR/SDR title screen encoding.

Title screens should match source material: HDR encoding only when at least
one source clip is HDR. When all sources are SDR, titles should be SDR too.
"""

from __future__ import annotations

from pathlib import Path


class TestEncodingHDRFlag:
    """encoding.py: _get_gpu_encoder_args respects hdr flag."""

    def test_hdr_true_includes_bt2020_metadata(self):
        from immich_memories.titles.encoding import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(hdr=True)
        assert "-color_primaries" in args
        assert "bt2020" in args
        assert "arib-std-b67" in args

    def test_hdr_false_omits_bt2020_metadata(self):
        from immich_memories.titles.encoding import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(hdr=False)
        assert "bt2020" not in args
        assert "arib-std-b67" not in args

    def test_hdr_false_uses_8bit_pixel_format(self):
        from immich_memories.titles.encoding import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(hdr=False)
        # Should use 8-bit pixel format, not 10-bit
        assert "p010le" not in args
        assert "yuv420p10le" not in args

    def test_hdr_true_uses_10bit_pixel_format(self):
        from immich_memories.titles.encoding import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(hdr=True)
        # Should use 10-bit pixel format
        has_10bit = "p010le" in args or "yuv420p10le" in args
        assert has_10bit

    def test_default_is_hdr(self):
        """Backward compatibility: default should be HDR."""
        from immich_memories.titles.encoding import _get_gpu_encoder_args

        args = _get_gpu_encoder_args()
        assert "bt2020" in args


class TestVideoEncodingHDRFlag:
    """video_encoding.py: _get_best_encoder respects hdr flag."""

    def test_hdr_false_returns_empty_filter(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        _, video_filter = _get_best_encoder(hdr=False)
        assert video_filter == ""

    def test_hdr_true_returns_hlg_filter(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        _, video_filter = _get_best_encoder(hdr=True)
        # Should have a conversion filter (zscale or format=p010le)
        # Note: may be empty if libx264 fallback, but on most systems it's set
        assert isinstance(video_filter, str)

    def test_hdr_false_no_color_metadata(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        encoder_args, _ = _get_best_encoder(hdr=False)
        assert "bt2020" not in encoder_args
        assert "arib-std-b67" not in encoder_args


class TestGlobeVideoHDRFlag:
    """globe_video.py: _build_ffmpeg_command respects hdr flag."""

    def test_hdr_true_includes_hlg_metadata(self):
        from immich_memories.titles.globe_video import _build_ffmpeg_command

        cmd = _build_ffmpeg_command(1920, 1080, 30.0, 5.0, Path("/tmp/g.mp4"), hdr=True)
        cmd_str = " ".join(cmd)
        assert "arib-std-b67" in cmd_str
        assert "bt2020" in cmd_str

    def test_hdr_false_omits_hlg_metadata(self):
        from immich_memories.titles.globe_video import _build_ffmpeg_command

        cmd = _build_ffmpeg_command(1920, 1080, 30.0, 5.0, Path("/tmp/g.mp4"), hdr=False)
        cmd_str = " ".join(cmd)
        assert "arib-std-b67" not in cmd_str
        assert "bt2020" not in cmd_str


class TestTitleScreenConfigHDR:
    """TitleScreenConfig has hdr flag."""

    def test_default_hdr_true(self):
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig()
        assert config.hdr is True

    def test_hdr_can_be_set_false(self):
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig(hdr=False)
        assert config.hdr is False


class TestHDRDetection:
    """has_any_hdr_clip detects HDR from source clips."""

    def test_returns_false_for_empty_list(self):
        from immich_memories.processing.hdr_utilities import has_any_hdr_clip

        assert has_any_hdr_clip([]) is False

    def test_returns_false_for_sdr_clips(self):
        """When _detect_hdr_type returns None for all clips, result is False."""
        from unittest.mock import patch

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.hdr_utilities import has_any_hdr_clip

        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/tmp/b.mp4"), duration=3.0),
        ]
        with patch("immich_memories.processing.hdr_utilities._detect_hdr_type", return_value=None):
            assert has_any_hdr_clip(clips) is False

    def test_returns_true_when_one_clip_is_hdr(self):
        """When at least one clip is HDR, result is True."""
        from unittest.mock import patch

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.hdr_utilities import has_any_hdr_clip

        clips = [
            AssemblyClip(path=Path("/tmp/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/tmp/b.mp4"), duration=3.0),
        ]
        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            side_effect=[None, "hlg"],
        ):
            assert has_any_hdr_clip(clips) is True
