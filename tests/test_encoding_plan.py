"""Tests for resolving one truthful final-output encoding contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import immich_memories.processing.encoding_plan as encoding_plan_module
from immich_memories.processing.encoding_plan import (
    EncodingRequest,
    HdrMode,
    HdrTransfer,
    OutputCodec,
    UnsupportedEncodingCombination,
    resolve_encoding_plan,
    resolve_output_selection,
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


def test_output_selection_preserves_config_when_override_is_absent() -> None:
    selection = resolve_output_selection(
        config_codec="h265",
        config_container="mp4",
        format_override=None,
    )

    assert selection.codec is OutputCodec.H265
    assert selection.container == "mp4"


@pytest.mark.parametrize(
    ("override", "expected_codec", "expected_container"),
    [
        ("mp4", OutputCodec.H264, "mp4"),
        ("h265", OutputCodec.H265, "mp4"),
        ("prores", OutputCodec.PRORES, "mov"),
    ],
)
def test_output_selection_applies_only_explicit_override(
    override: str,
    expected_codec: OutputCodec,
    expected_container: str,
) -> None:
    selection = resolve_output_selection(
        config_codec="h264",
        config_container="mp4",
        format_override=override,
    )

    assert selection.codec is expected_codec
    assert selection.container == expected_container


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


def test_raw_hdr_string_cannot_bypass_h264_hdr_rejection() -> None:
    request = EncodingRequest(
        codec=OutputCodec.H264,
        hdr_mode="hdr",  # type: ignore[arg-type]
        hardware_enabled=False,
        preset="balanced",
        crf=18,
        container="mp4",
    )

    with pytest.raises(UnsupportedEncodingCombination, match="H.264"):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=True)


def test_unknown_preset_is_rejected_at_resolver_boundary() -> None:
    request = EncodingRequest(
        codec=OutputCodec.H264,
        hdr_mode=HdrMode.AUTO,
        hardware_enabled=False,
        preset="turbo",  # type: ignore[arg-type]
        crf=18,
        container="mp4",
    )

    with pytest.raises(UnsupportedEncodingCombination, match="preset"):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=False)


def test_non_boolean_hardware_enabled_is_rejected() -> None:
    request = EncodingRequest(
        codec=OutputCodec.H264,
        hdr_mode=HdrMode.AUTO,
        hardware_enabled="false",  # type: ignore[arg-type]
        preset="balanced",
        crf=18,
        container="mp4",
    )

    with pytest.raises(UnsupportedEncodingCombination, match="hardware_enabled.*bool"):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=False)


def test_non_boolean_input_has_hdr_is_rejected() -> None:
    with pytest.raises(UnsupportedEncodingCombination, match="input_has_hdr.*bool"):
        resolve_encoding_plan(
            _request(OutputCodec.H265, hardware_enabled=False),
            _apple_capabilities(),
            input_has_hdr="false",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("hardware_enabled", "input_has_hdr", "expected_encoder", "expected_hdr"),
    [
        pytest.param(True, True, "hevc_videotoolbox", True, id="true-values"),
        pytest.param(False, False, "libx265", False, id="false-values"),
    ],
)
def test_boolean_boundary_values_keep_their_meaning(
    hardware_enabled: bool,
    input_has_hdr: bool,
    expected_encoder: str,
    expected_hdr: bool,
) -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=hardware_enabled),
        _apple_capabilities(),
        input_has_hdr=input_has_hdr,
    )

    assert plan.encoder == expected_encoder
    assert plan.hdr is expected_hdr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("preset", [], id="preset-list"),
        pytest.param("container", [], id="container-list"),
    ],
)
def test_non_string_collection_fields_raise_typed_error(field: str, value: object) -> None:
    values: dict[str, object] = {
        "codec": OutputCodec.H264,
        "hdr_mode": HdrMode.AUTO,
        "hardware_enabled": False,
        "preset": "balanced",
        "crf": 18,
        "container": "mp4",
    }
    values[field] = value
    request = EncodingRequest(**values)  # type: ignore[arg-type]

    with pytest.raises(UnsupportedEncodingCombination, match=field):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=False)


@pytest.mark.parametrize(
    "crf",
    [
        pytest.param(-1, id="below-minimum"),
        pytest.param(52, id="above-maximum"),
        pytest.param(18.5, id="not-integer"),
        pytest.param(True, id="boolean"),
    ],
)
def test_invalid_crf_is_rejected_at_resolver_boundary(crf: object) -> None:
    request = EncodingRequest(
        codec=OutputCodec.H264,
        hdr_mode=HdrMode.AUTO,
        hardware_enabled=False,
        preset="balanced",
        crf=crf,  # type: ignore[arg-type]
        container="mp4",
    )

    with pytest.raises(UnsupportedEncodingCombination, match="CRF"):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=False)


def test_valid_raw_enum_strings_are_normalized_in_plan() -> None:
    request = EncodingRequest(
        codec="h265",  # type: ignore[arg-type]
        hdr_mode="auto",  # type: ignore[arg-type]
        hardware_enabled=False,
        preset="balanced",
        crf=18,
        container="mp4",
    )

    plan = resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=True)

    assert plan.codec is OutputCodec.H265
    assert plan.hdr is True


@pytest.mark.parametrize(
    ("codec", "hdr_mode", "message"),
    [
        pytest.param("vp9", HdrMode.AUTO, "codec", id="codec"),
        pytest.param(OutputCodec.H264, "dolby", "HDR mode", id="hdr-mode"),
        pytest.param(264, HdrMode.AUTO, "codec", id="codec-non-string"),
        pytest.param(OutputCodec.H264, None, "HDR mode", id="hdr-mode-non-string"),
    ],
)
def test_unknown_raw_enum_values_raise_typed_error(
    codec: object,
    hdr_mode: object,
    message: str,
) -> None:
    request = EncodingRequest(
        codec=codec,  # type: ignore[arg-type]
        hdr_mode=hdr_mode,  # type: ignore[arg-type]
        hardware_enabled=False,
        preset="balanced",
        crf=18,
        container="mp4",
    )

    with pytest.raises(UnsupportedEncodingCombination, match=message):
        resolve_encoding_plan(request, _apple_capabilities(), input_has_hdr=False)


def test_h265_auto_preserves_hdr_input() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True),
        _apple_capabilities(),
        input_has_hdr=True,
    )

    assert plan.hdr is True
    assert plan.tone_map_to_sdr is False
    assert plan.pixel_format == "p010le"


def test_h265_auto_preserves_exact_pq_transfer() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H265, hardware_enabled=True),
        _apple_capabilities(),
        input_transfer=HdrTransfer.PQ,
    )

    assert plan.target_transfer is HdrTransfer.PQ


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
    with pytest.raises(UnsupportedEncodingCombination, match="container"):
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


@pytest.mark.parametrize("crf", [0, 51])
def test_crf_boundary_values_are_preserved(crf: int) -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H264, hardware_enabled=False, crf=crf),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    assert plan.encoder_args[-2:] == ("-crf", str(crf))


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


def test_mismatched_encoder_family_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        encoding_plan_module,
        "get_ffmpeg_encoder",
        lambda *_args, **_kwargs: ("libx265", ["-preset", "medium"]),
    )

    with pytest.raises(UnsupportedEncodingCombination, match="libx265.*h264"):
        resolve_encoding_plan(
            _request(OutputCodec.H264, hardware_enabled=False),
            _apple_capabilities(),
            input_has_hdr=False,
        )


def test_resolved_plan_is_immutable() -> None:
    plan = resolve_encoding_plan(
        _request(OutputCodec.H264, hardware_enabled=False),
        _apple_capabilities(),
        input_has_hdr=False,
    )

    with pytest.raises(FrozenInstanceError):
        plan.encoder = "hevc_videotoolbox"  # type: ignore[misc]
