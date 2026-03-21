"""Tests for configurable output quality presets."""

from __future__ import annotations

from immich_memories.processing.hdr_utilities import _get_gpu_encoder_args, quality_to_crf


class TestQualityToCrf:
    """Quality preset maps to encoder-appropriate CRF values."""

    def test_high_quality_low_crf(self):
        assert quality_to_crf("high") <= 15

    def test_medium_quality_moderate_crf(self):
        crf = quality_to_crf("medium")
        assert 16 <= crf <= 23

    def test_low_quality_high_crf(self):
        assert quality_to_crf("low") >= 24

    def test_default_is_high(self):
        from immich_memories.config_models import OutputConfig

        config = OutputConfig()
        assert config.quality == "high"


class TestEncoderArgsQuality:
    """GPU encoder args reflect quality preset."""

    def test_high_quality_macos_has_high_vt_quality(self):
        import sys

        if sys.platform != "darwin":
            return  # Skip on non-macOS
        args = _get_gpu_encoder_args(crf=quality_to_crf("high"), preserve_hdr=True)
        # Should use hevc_videotoolbox with high -q:v
        q_idx = args.index("-q:v")
        vt_quality = int(args[q_idx + 1])
        assert vt_quality >= 65

    def test_low_quality_macos_has_low_vt_quality(self):
        import sys

        if sys.platform != "darwin":
            return
        args = _get_gpu_encoder_args(crf=quality_to_crf("low"), preserve_hdr=True)
        q_idx = args.index("-q:v")
        vt_quality = int(args[q_idx + 1])
        assert vt_quality <= 45
