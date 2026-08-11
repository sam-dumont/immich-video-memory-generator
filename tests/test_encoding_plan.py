"""Tests for resolving one truthful final-output encoding contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from immich_memories.processing.encoding_plan import (
    EncodingRequest,
    HdrMode,
    OutputCodec,
    resolve_encoding_plan,
)
from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities


def _request(
    codec: OutputCodec,
    hardware_enabled: bool,
    *,
    hdr_mode: HdrMode = HdrMode.AUTO,
    container: str = "mp4",
    crf: int = 18,
) -> EncodingRequest:
    return EncodingRequest(
        codec=codec,
        hdr_mode=hdr_mode,
        hardware_enabled=hardware_enabled,
        preset="balanced",
        crf=crf,
        container=container,
    )


def _apple_capabilities() -> HWAccelCapabilities:
    return HWAccelCapabilities(
        backend=HWAccelBackend.APPLE,
        supports_h264_encode=True,
        supports_h265_encode=True,
        prores_encode=True,
    )


@pytest.mark.parametrize(
    ("codec", "hardware_enabled", "container", "expected_encoder"),
    [
        pytest.param(OutputCodec.H264, True, "mp4", "h264_videotoolbox", id="h264-hw"),
        pytest.param(OutputCodec.H264, False, "mp4", "libx264", id="h264-sw"),
        pytest.param(OutputCodec.H265, True, "mp4", "hevc_videotoolbox", id="h265-hw"),
        pytest.param(OutputCodec.H265, False, "mp4", "libx265", id="h265-sw"),
        pytest.param(OutputCodec.PRORES, True, "mov", "prores_ks", id="prores-hw-request"),
        pytest.param(OutputCodec.PRORES, False, "mov", "prores_ks", id="prores-sw"),
    ],
)
def test_encoder_never_changes_requested_codec(
    codec: OutputCodec,
    hardware_enabled: bool,
    container: str,
    expected_encoder: str,
) -> None:
    plan = resolve_encoding_plan(
        _request(codec, hardware_enabled, container=container),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    assert plan.codec is codec
    assert plan.encoder == expected_encoder


def test_h264_auto_tone_maps_hdr_input_to_sdr() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H264, hardware_enabled=True),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.hdr is False
    assert plan.tone_map_to_sdr is True
    assert plan.pixel_format == "yuv420p"


def test_h264_explicit_hdr_is_rejected_before_rendering() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_encoding_plan(
            _request(OutputCodec.H264, hardware_enabled=True, hdr_mode=HdrMode.HDR),
            _apple_capabilities(),
            input_has_hdr=True,
        )

    assert type(exc_info.value).__name__ == "UnsupportedEncodingCombination"
    assert "H.264" in str(exc_info.value)


def test_h265_auto_preserves_hdr_input() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.hdr is True
    assert plan.tone_map_to_sdr is False
    assert plan.pixel_format == "p010le"


def test_h265_explicit_sdr_tone_maps_hdr_input() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True, hdr_mode=HdrMode.SDR),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.hdr is False
    assert plan.tone_map_to_sdr is True
    assert plan.pixel_format == "yuv420p"


def test_h265_explicit_hdr_produces_hdr_for_sdr_input() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True, hdr_mode=HdrMode.HDR),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    assert plan.hdr is True
    assert plan.tone_map_to_sdr is False
    assert plan.pixel_format == "p010le"


def test_prores_auto_tone_maps_hdr_input_to_10_bit_sdr() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.PRORES, hardware_enabled=True, container="mov"),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.hdr is False
    assert plan.tone_map_to_sdr is True
    assert plan.pixel_format == "yuv422p10le"


def test_prores_explicit_hdr_is_rejected_before_rendering() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_encoding_plan(
            _request(
                OutputCodec.PRORES,
                hardware_enabled=False,
                hdr_mode=HdrMode.HDR,
                container="mov",
            ),
            _apple_capabilities(),
            input_has_hdr=True,
        )

    assert type(exc_info.value).__name__ == "UnsupportedEncodingCombination"
    assert "ProRes" in str(exc_info.value)


def test_prores_requires_mov_container() -> None:
    with pytest.raises(ValueError, match="MOV"):
        resolve_encoding_plan(
            _request(OutputCodec.PRORES, hardware_enabled=False, container="mp4"),
            _apple_capabilities(),
            input_has_hdr=False,
        )


def test_unknown_container_is_rejected() -> None:
    with pytest.raises(ValueError, match="container"):
        resolve_encoding_plan(
            _request(OutputCodec.H264, hardware_enabled=False, container="mkv"),
            _apple_capabilities(),
            input_has_hdr=False,
        )


@pytest.mark.parametrize("codec", [OutputCodec.H264, OutputCodec.H265])
def test_h26x_codecs_accept_mov_container(codec: OutputCodec) -> None:
    plan = resolve_encoding_plan(
        _request(codec, hardware_enabled=False, container="mov"),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    assert plan.container == "mov"


def test_software_h265_hdr_uses_planar_10_bit_pixel_format() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=False, hdr_mode=HdrMode.HDR),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.encoder == "libx265"
    assert plan.pixel_format == "yuv420p10le"


def test_software_encoder_args_include_requested_crf() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H264, hardware_enabled=False, crf=21),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    assert plan.encoder_args == ("-preset", "medium", "-crf", "21")


def test_unsupported_hardware_encoder_falls_back_within_requested_codec() -> None:
    capabilities = HWAccelCapabilities(
        backend=HWAccelBackend.APPLE,
        supports_h264_encode=True,
        supports_h265_encode=False,
    )

    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True),
        capabilities,
        input_has_hdr=False,
    )

    assert plan.codec is OutputCodec.H265
    assert plan.encoder == "libx265"


def test_resolved_plan_is_immutable() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H264, hardware_enabled=False),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    with pytest.raises(FrozenInstanceError):
        plan.encoder = "hevc_videotoolbox"  # type: ignore[misc]
