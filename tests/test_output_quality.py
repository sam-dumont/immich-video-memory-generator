"""Tests for configurable output quality presets."""

from __future__ import annotations

from immich_memories.processing.clip_encoder import encoder_args_for_plan
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.hdr_utilities import quality_to_crf


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
    """Resolved software encoder args retain the configured quality."""

    @staticmethod
    def _software_h264_plan(crf: int) -> EncodingPlan:
        return EncodingPlan(
            codec=OutputCodec.H264,
            encoder="libx264",
            encoder_args=("-preset", "medium", "-crf", str(crf)),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv420p",
            container="mp4",
        )

    def test_high_quality_crf_reaches_ffmpeg_command(self):
        crf = quality_to_crf("high")
        args = encoder_args_for_plan(self._software_h264_plan(crf))
        assert args[args.index("-crf") + 1] == str(crf)

    def test_low_quality_crf_reaches_ffmpeg_command(self):
        crf = quality_to_crf("low")
        args = encoder_args_for_plan(self._software_h264_plan(crf))
        assert args[args.index("-crf") + 1] == str(crf)
