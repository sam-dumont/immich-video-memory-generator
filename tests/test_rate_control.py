"""One CRF dial has to mean one picture, whichever encoder is doing the work."""

from __future__ import annotations

import logging

import pytest

from immich_memories.processing.rate_control import (
    quality_args,
    videotoolbox_quality_from_crf,
)

# Every encoder the pipeline can select for a final output.
_HARDWARE_ENCODERS = [
    "h264_vaapi",
    "hevc_vaapi",
    "h264_qsv",
    "hevc_qsv",
    "h264_nvenc",
    "hevc_nvenc",
    "h264_videotoolbox",
    "hevc_videotoolbox",
]


@pytest.mark.parametrize("encoder", _HARDWARE_ENCODERS)
def test_no_hardware_encoder_is_left_without_rate_control(encoder: str) -> None:
    """The defect: VAAPI, QSV and NVENC got `[]` and the driver chose the quality."""
    assert quality_args(encoder, 18) != []


@pytest.mark.parametrize("encoder", _HARDWARE_ENCODERS + ["libx264", "libx265"])
def test_every_encoder_asks_for_a_constant_quality_not_a_bitrate(encoder: str) -> None:
    """A bitrate target spends the same bits on a still frame and a fast pan."""
    args = quality_args(encoder, 18)

    assert "-b:v" not in args
    assert "-maxrate" not in args


@pytest.mark.parametrize("encoder", _HARDWARE_ENCODERS + ["libx264", "libx265"])
def test_a_lower_crf_always_asks_for_more_quality(encoder: str) -> None:
    """Whatever the flag is called, the dial has to point the same way."""
    better = quality_args(encoder, 16)
    worse = quality_args(encoder, 28)

    # Every family's value is the last token; VideoToolbox counts up, the
    # quantiser scales count down.
    if encoder.endswith("_videotoolbox"):
        assert int(better[-1]) > int(worse[-1])
    else:
        assert int(better[-1]) < int(worse[-1])


def test_vaapi_selects_cqp_because_qp_alone_is_ignored() -> None:
    args = quality_args("h264_vaapi", 18)

    assert args[args.index("-rc_mode") + 1] == "CQP"
    # Measured on the owner's Gemini Lake: libx264 -crf 18 is SSIM 0.99011,
    # h264_vaapi -qp 20 is 0.98946, and no other point on the sweep is closer.
    assert args[args.index("-qp") + 1] == "20"


def test_nvenc_sets_constqp_rather_than_a_bitrate_mode() -> None:
    args = quality_args("h264_nvenc", 18)

    assert args[args.index("-rc") + 1] == "constqp"
    assert "-qp" in args


def test_qsv_uses_global_quality_which_is_where_its_dial_lives() -> None:
    args = quality_args("h264_qsv", 18)

    assert args[0] == "-global_quality"


def test_the_flags_do_not_leak_between_families() -> None:
    """`-qp`, `-cq` and `-global_quality` are not interchangeable."""
    assert "-global_quality" not in quality_args("h264_vaapi", 18)
    assert "-rc_mode" not in quality_args("h264_nvenc", 18)
    assert "-qp" not in quality_args("h264_qsv", 18)
    assert "-crf" not in quality_args("hevc_videotoolbox", 18)


def test_videotoolbox_no_longer_asks_for_eight_times_the_software_bitrate() -> None:
    """The old `111 - 2*crf` put CRF 18 at q 75 = 37.3 Mbps against libx265's 4.6."""
    assert videotoolbox_quality_from_crf(18) < 75


def test_the_default_quality_preset_is_the_one_that_shipped_the_huge_files() -> None:
    """`quality: high` is CRF 12, which the old mapping sent to q 87."""
    assert videotoolbox_quality_from_crf(12) < 87


@pytest.mark.parametrize("crf", [0, 51])
def test_quantisers_stay_inside_the_scale_at_the_extremes(crf: int) -> None:
    for encoder in _HARDWARE_ENCODERS:
        args = quality_args(encoder, crf)
        value = int(args[-1])
        ceiling = 100 if encoder.endswith("_videotoolbox") else 51
        assert 0 <= value <= ceiling


def test_prores_has_no_quality_dial_and_says_nothing() -> None:
    assert quality_args("prores_ks", 18) == []


def test_an_unknown_encoder_is_reported_rather_than_silently_defaulted(caplog) -> None:
    """Silence is what caused this defect; a new backend must not repeat it."""
    with caplog.at_level(logging.WARNING, logger="immich_memories.processing.rate_control"):
        assert quality_args("av1_someday", 18) == []

    assert "av1_someday" in caplog.text
