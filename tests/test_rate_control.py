"""One CRF dial has to mean one picture, whichever encoder is doing the work."""

from __future__ import annotations

import logging

import pytest

from immich_memories.processing.rate_control import quality_args


def _videotoolbox_quality(crf: int) -> int:
    return int(quality_args("hevc_videotoolbox", crf)[-1])


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


def test_vaapi_balanced_lands_on_the_measured_quality_match() -> None:
    """Gemini Lake: QP 22 scores 0.98470, the reference CRF 23 scores ~0.9861."""
    from immich_memories.processing.hdr_utilities import quality_to_crf

    args = quality_args("h264_vaapi", quality_to_crf("balanced"))

    assert args[args.index("-qp") + 1] == "22"


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
    assert _videotoolbox_quality(18) < 75


def test_the_default_quality_preset_is_the_one_that_shipped_the_huge_files() -> None:
    """`quality: high` is CRF 12, which the old mapping sent to q 87."""
    assert _videotoolbox_quality(12) < 87


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


# ---------------------------------------------------------------------------
# The ladder, against the anchors the owner approved by eye
# ---------------------------------------------------------------------------

# The settings signed off for each tier, per family. These are measurements plus
# a judgement on gradients, not a formula, so they are asserted literally: if the
# calibration drifts off them, the picture the user approved has changed.
_APPROVED = {
    "high": {
        "libx265": "18",
        "libx264": "18",
        "h264_vaapi": "20",
        "h264_nvenc": "20",
        "hevc_videotoolbox": "65",
    },
    "balanced": {
        "libx265": "24",
        "libx264": "22",
        "h264_vaapi": "22",
        "h264_nvenc": "24",
        "hevc_videotoolbox": "55",
    },
}


@pytest.mark.parametrize("tier", ["high", "balanced"])
def test_every_family_lands_on_its_approved_anchor(tier: str) -> None:
    from immich_memories.processing.hdr_utilities import quality_to_crf

    crf = quality_to_crf(tier)

    for encoder, expected in _APPROVED[tier].items():
        assert quality_args(encoder, crf)[-1] == expected, encoder


def test_the_families_do_not_share_one_offset() -> None:
    """Five families need five slopes; a shared constant gives five pictures."""
    from immich_memories.processing.hdr_utilities import quality_to_crf

    high, balanced = quality_to_crf("high"), quality_to_crf("balanced")
    steps = {
        encoder: int(quality_args(encoder, balanced)[-1]) - int(quality_args(encoder, high)[-1])
        for encoder in _APPROVED["high"]
    }

    assert len(set(steps.values())) > 1, steps


def test_balanced_is_the_default_and_fast_does_not_lower_it() -> None:
    """~0.980 bands on gradients, so `fast` buys speed from the preset instead."""
    from immich_memories.config_models_render import OutputConfig
    from immich_memories.processing.hdr_utilities import quality_encoder_preset, quality_to_crf

    assert OutputConfig().quality == "balanced"
    assert quality_to_crf("fast") == quality_to_crf("balanced")
    assert quality_encoder_preset("fast", "quality") == "fast"
    assert quality_encoder_preset("balanced", "quality") == "quality"


def test_high_no_longer_asks_for_quality_nobody_can_see() -> None:
    """CRF 12 was past SSIM 0.999 and asked VideoToolbox for 76 Mbps."""
    from immich_memories.processing.hdr_utilities import quality_to_crf

    assert quality_to_crf("high") >= 18
    assert _videotoolbox_quality(quality_to_crf("high")) < 87


def test_the_retired_names_still_load() -> None:
    from immich_memories.config_models_render import OutputConfig

    assert OutputConfig(quality="medium").quality == "balanced"
    assert OutputConfig(quality="low").quality == "fast"


def test_high_really_is_better_than_balanced_on_every_family() -> None:
    from immich_memories.processing.hdr_utilities import quality_to_crf

    for encoder in _APPROVED["high"]:
        better = int(quality_args(encoder, quality_to_crf("high"))[-1])
        worse = int(quality_args(encoder, quality_to_crf("balanced"))[-1])
        if encoder.endswith("_videotoolbox"):
            assert better > worse, encoder
        else:
            assert better < worse, encoder
