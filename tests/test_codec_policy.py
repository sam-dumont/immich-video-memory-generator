"""A codec the machine cannot encode means the CPU does the whole film."""

from __future__ import annotations

import logging

from immich_memories.processing.encoding_plan import (
    EncodingRequest,
    HdrMode,
    OutputCodec,
    resolve_encoding_plan,
)
from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities


def _gemini_lake() -> HWAccelCapabilities:
    """The owner's J4125: an H.264 encode entrypoint and no HEVC one at all."""
    return HWAccelCapabilities(
        backend=HWAccelBackend.VAAPI,
        supports_h264_encode=True,
        supports_h265_encode=False,
    )


def _both_codecs() -> HWAccelCapabilities:
    return HWAccelCapabilities(
        backend=HWAccelBackend.NVIDIA,
        supports_h264_encode=True,
        supports_h265_encode=True,
    )


def _request(
    codec: OutputCodec = OutputCodec.H265,
    *,
    hdr_mode: HdrMode = HdrMode.SDR,
    policy: str = "prefer_hardware",
    container: str = "mp4",
) -> EncodingRequest:
    return EncodingRequest(
        codec=codec,
        hdr_mode=hdr_mode,
        hardware_enabled=True,
        preset="balanced",
        crf=23,
        container=container,
        codec_policy=policy,
    )


def test_h265_becomes_h264_when_only_h264_has_an_encoder() -> None:
    plan = resolve_encoding_plan(_request(), _gemini_lake(), input_has_hdr=False)

    assert plan.codec is OutputCodec.H264
    assert plan.encoder == "h264_vaapi"


def test_the_substitution_is_recorded_rather_than_silent() -> None:
    plan = resolve_encoding_plan(_request(), _gemini_lake(), input_has_hdr=False)

    assert plan.codec_substituted_from is OutputCodec.H265


def test_it_says_so_in_the_log(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="immich_memories.processing.encoding_plan"):
        resolve_encoding_plan(_request(), _gemini_lake(), input_has_hdr=False)

    assert "h265" in caplog.text and "h264" in caplog.text


def test_strict_keeps_the_requested_codec_on_the_cpu() -> None:
    plan = resolve_encoding_plan(_request(policy="strict"), _gemini_lake(), input_has_hdr=False)

    assert plan.codec is OutputCodec.H265
    assert plan.encoder == "libx265"
    assert plan.codec_substituted_from is None


def test_a_machine_that_encodes_h265_is_untouched() -> None:
    """The policy must not change behaviour where the request can be honoured."""
    plan = resolve_encoding_plan(_request(), _both_codecs(), input_has_hdr=False)

    assert plan.codec is OutputCodec.H265
    assert plan.encoder == "hevc_nvenc"
    assert plan.codec_substituted_from is None


def test_hdr_is_never_traded_for_speed() -> None:
    """H.264 carries no HDR, so substituting would throw away the range."""
    plan = resolve_encoding_plan(_request(hdr_mode=HdrMode.HDR), _gemini_lake(), input_has_hdr=True)

    assert plan.codec is OutputCodec.H265
    assert plan.hdr is True
    assert plan.codec_substituted_from is None


def test_an_hdr_source_on_auto_also_keeps_h265() -> None:
    plan = resolve_encoding_plan(
        _request(hdr_mode=HdrMode.AUTO), _gemini_lake(), input_has_hdr=True
    )

    assert plan.codec is OutputCodec.H265
    assert plan.codec_substituted_from is None


def test_prores_is_an_explicit_choice_and_is_never_substituted() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.PRORES, container="mov"), _gemini_lake(), input_has_hdr=False
    )

    assert plan.codec is OutputCodec.PRORES
    assert plan.codec_substituted_from is None


def test_software_only_machines_are_untouched() -> None:
    """With no hardware at all there is nothing to prefer."""
    plan = resolve_encoding_plan(_request(), HWAccelCapabilities(), input_has_hdr=False)

    assert plan.codec is OutputCodec.H265
    assert plan.codec_substituted_from is None


def test_the_substituted_plan_keeps_its_container_and_drops_the_hevc_tag() -> None:
    """`-tag:v hvc1` on an H.264 stream would mislabel the file."""
    from immich_memories.processing.clip_encoder import encoder_args_for_plan

    plan = resolve_encoding_plan(_request(), _gemini_lake(), input_has_hdr=False)

    assert plan.container == "mp4"
    assert "hvc1" not in encoder_args_for_plan(plan)
