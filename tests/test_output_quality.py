"""Tests for configurable output quality presets."""

from __future__ import annotations

from immich_memories.processing.clip_encoder import encoder_args_for_plan
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.hdr_utilities import quality_to_crf


class TestQualityToCrf:
    """Quality preset maps to encoder-appropriate CRF values."""

    def test_high_is_better_than_balanced(self):
        assert quality_to_crf("high") < quality_to_crf("balanced")

    def test_fast_keeps_the_balanced_picture(self):
        """~0.980 bands on gradients, so `fast` buys speed from the preset."""
        assert quality_to_crf("fast") == quality_to_crf("balanced")

    def test_high_stays_on_the_part_of_the_curve_that_still_pays(self):
        """CRF 12 was past SSIM 0.999 — invisible quality at several times the bits."""
        assert quality_to_crf("high") >= 18

    def test_medium_is_the_retired_name_for_balanced(self):
        assert quality_to_crf("medium") == quality_to_crf("balanced")

    def test_default_is_balanced(self):
        from immich_memories.config_models_render import OutputConfig

        config = OutputConfig()
        assert config.quality == "balanced"


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
