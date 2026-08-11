"""Tests for conditional HDR/SDR title screen encoding.

Title screens should match source material: HDR encoding only when at least
one source clip is HDR. When all sources are SDR, titles should be SDR too.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import AssemblySettings, TitleScreenSettings
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _software_h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-preset", "medium", "-crf", "18"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _software_prores_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=(),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _hardware_h265_hdr_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="hevc_videotoolbox",
        encoder_args=("-q:v", "50", "-allow_sw", "1"),
        target_transfer=HdrTransfer.HLG,
        tone_map_to_sdr=False,
        pixel_format="p010le",
        container="mp4",
    )


def _software_h265_pq_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-preset", "fast", "-crf", "18"),
        target_transfer=HdrTransfer.PQ,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def test_title_encoder_args_use_the_resolved_software_h264_plan() -> None:
    """Title clips must use the same codec family as the final output."""
    from immich_memories.titles.encoding import title_encoder_args

    args = title_encoder_args(_software_h264_plan())

    assert args[args.index("-c:v") + 1] == "libx264"
    assert "hevc_videotoolbox" not in args
    assert "libx265" not in args
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-color_trc") + 1] == "bt709"


def test_title_config_carries_the_plan_and_derives_prores_suffix() -> None:
    """Every generated title path must use a concat-compatible container."""
    from immich_memories.titles.generator import TitleScreenConfig

    plan = _software_prores_plan()
    config = TitleScreenConfig(encoding_plan=plan)

    assert config.encoding_plan is plan
    assert config.output_suffix == ".mov"
    assert config.hdr is False


def test_title_inserter_threads_the_assembly_plan_into_title_config() -> None:
    """The title generator receives the exact plan already resolved for assembly."""
    from immich_memories.processing.title_inserter import TitleInserter

    plan = _software_prores_plan()
    inserter = TitleInserter(AssemblySettings(encoding_plan=plan), MagicMock())

    config = inserter._build_title_config(TitleScreenSettings(), 1920, 1080, 30)

    assert config.encoding_plan is plan


class TestPlanDerivedTitleEncoding:
    """Every title helper consumes the already-resolved plan."""

    def test_h265_hardware_plan_keeps_hdr_metadata_and_10bit_format(self):
        from immich_memories.titles.encoding import title_encoder_args

        args = title_encoder_args(_hardware_h265_hdr_plan())

        assert args[args.index("-c:v") + 1] == "hevc_videotoolbox"
        assert args[args.index("-pix_fmt") + 1] == "p010le"
        assert args[args.index("-color_primaries") + 1] == "bt2020"
        assert args[args.index("-color_trc") + 1] == "arib-std-b67"
        assert args[args.index("-tag:v") + 1] == "hvc1"

    def test_prores_plan_never_substitutes_h264_or_hevc(self):
        from immich_memories.titles.encoding import title_encoder_args

        args = title_encoder_args(_software_prores_plan())

        assert args[args.index("-c:v") + 1] == "prores_ks"
        assert args[args.index("-pix_fmt") + 1] == "yuv422p10le"
        assert all("264" not in token and "hevc" not in token for token in args)

    def test_standalone_title_plan_is_explicit_h264_sdr(self):
        from immich_memories.titles.encoding import standalone_title_encoding_plan

        plan = standalone_title_encoding_plan()

        assert plan.codec is OutputCodec.H264
        assert plan.encoder == "libx264"
        assert plan.hdr is False


class TestVideoEncodingPlan:
    """PIL title encoding derives color conversion from the plan."""

    def test_sdr_plan_embeds_bt709_tags_in_filter(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        _, video_filter = _get_best_encoder(_software_h264_plan())
        assert "setparams=colorspace=bt709" in video_filter
        assert "color_primaries=bt709" in video_filter
        assert "color_trc=bt709" in video_filter

    @pytest.mark.parametrize(
        "plan",
        [_hardware_h265_hdr_plan(), _software_h265_pq_plan()],
        ids=["hlg", "pq"],
    )
    def test_hdr_title_fails_when_required_color_conversion_is_unavailable(
        self, plan: EncodingPlan
    ) -> None:
        """HLG/PQ title output must not relabel unconverted SDR pixels."""
        from unittest.mock import patch

        from immich_memories.processing import hdr_utilities
        from immich_memories.titles.encoding import title_color_filter

        with (
            patch(
                "immich_memories.processing.hdr_utilities._check_zscale_available",
                return_value=False,
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            title_color_filter(plan)

        assert type(exc_info.value) is hdr_utilities.RequiredColorConversionUnavailable

    def test_hdr_true_returns_hlg_filter(self):
        from unittest.mock import patch

        from immich_memories.titles.video_encoding import _get_best_encoder

        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            _, video_filter = _get_best_encoder(_hardware_h265_hdr_plan())

        assert isinstance(video_filter, str)
        assert "zscale=" in video_filter
        assert "t=arib-std-b67" in video_filter
        assert "color_trc=arib-std-b67" in video_filter

    def test_hdr_title_fails_instead_of_relabeling_when_zscale_is_missing(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        with (
            patch(
                "immich_memories.processing.hdr_utilities._check_zscale_available",
                return_value=False,
            ),
            pytest.raises(RuntimeError, match="zscale"),
        ):
            _get_best_encoder(_hardware_h265_hdr_plan())

    def test_pq_plan_uses_smpte2084_not_hlg(self):
        from unittest.mock import patch

        from immich_memories.titles.video_encoding import _get_best_encoder

        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            encoder_args, video_filter = _get_best_encoder(_software_h265_pq_plan())

        assert "smpte2084" in video_filter
        assert "arib-std-b67" not in video_filter
        assert encoder_args[encoder_args.index("-color_trc") + 1] == "smpte2084"

    def test_hdr_false_no_color_metadata(self):
        from immich_memories.titles.video_encoding import _get_best_encoder

        encoder_args, _ = _get_best_encoder(_software_h264_plan())
        assert "bt2020" not in encoder_args
        assert "arib-std-b67" not in encoder_args


class TestGlobeVideoPlan:
    """Globe commands preserve the configured codec contract."""

    def test_hdr_true_includes_hlg_metadata(self):
        from unittest.mock import patch

        from immich_memories.titles.globe_video import _build_ffmpeg_command

        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            cmd = _build_ffmpeg_command(
                1920, 1080, 30.0, 5.0, Path("/tmp/g.mp4"), _hardware_h265_hdr_plan()
            )
        cmd_str = " ".join(cmd)
        assert "arib-std-b67" in cmd_str
        assert "bt2020" in cmd_str

    def test_hdr_false_omits_hlg_metadata(self):
        from immich_memories.titles.globe_video import _build_ffmpeg_command

        cmd = _build_ffmpeg_command(
            1920, 1080, 30.0, 5.0, Path("/tmp/g.mov"), _software_prores_plan()
        )
        cmd_str = " ".join(cmd)
        assert "arib-std-b67" not in cmd_str
        assert "bt2020" not in cmd_str
        assert "-c:v prores_ks" in cmd_str


class TestTitleScreenConfigPlan:
    """TitleScreenConfig derives HDR truth from its plan."""

    def test_default_is_explicit_sdr(self):
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig()
        assert not config.hdr
        assert config.encoding_plan.codec is OutputCodec.H264

    def test_hdr_is_derived_from_plan(self):
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig(encoding_plan=_hardware_h265_hdr_plan())
        assert config.hdr


class TestHDRDetection:
    """has_any_hdr_clip detects HDR from source clips."""

    def test_returns_false_for_empty_list(self):
        from immich_memories.processing.hdr_utilities import has_any_hdr_clip

        assert not has_any_hdr_clip([])

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
            assert not has_any_hdr_clip(clips)

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
            assert has_any_hdr_clip(clips)
