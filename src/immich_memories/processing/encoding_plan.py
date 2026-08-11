"""Resolve requested output settings into one immutable encoding contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast

from immich_memories.processing.hardware import HWAccelCapabilities, get_ffmpeg_encoder


class OutputCodec(StrEnum):
    """Supported final-output codecs."""

    H264 = "h264"
    H265 = "h265"
    PRORES = "prores"


class HdrMode(StrEnum):
    """Requested relationship between source and output dynamic range."""

    AUTO = "auto"
    SDR = "sdr"
    HDR = "hdr"


class HdrTransfer(StrEnum):
    """Exact transfer function carried by the final-output contract."""

    NONE = "none"
    HLG = "hlg"
    PQ = "pq"


class UnsupportedEncodingCombination(ValueError):
    """Requested codec, dynamic range, and container cannot be combined."""


_ENCODERS_BY_CODEC: dict[OutputCodec, frozenset[str]] = {
    OutputCodec.H264: frozenset(
        {"libx264", "h264_nvenc", "h264_videotoolbox", "h264_vaapi", "h264_qsv"}
    ),
    OutputCodec.H265: frozenset(
        {"libx265", "hevc_nvenc", "hevc_videotoolbox", "hevc_vaapi", "hevc_qsv"}
    ),
    OutputCodec.PRORES: frozenset({"prores_ks"}),
}
_OUTPUT_CONTAINERS = frozenset({"mp4", "mov"})
_ENCODER_PRESETS = frozenset({"fast", "balanced", "quality"})


def _normalize_codec(value: object) -> OutputCodec:
    if not isinstance(value, str):
        raise UnsupportedEncodingCombination(f"Unsupported output codec: {value!r}")
    try:
        return OutputCodec(value)
    except ValueError as exc:
        raise UnsupportedEncodingCombination(f"Unsupported output codec: {value!r}") from exc


def _normalize_hdr_mode(value: object) -> HdrMode:
    if not isinstance(value, str):
        raise UnsupportedEncodingCombination(f"Unsupported HDR mode: {value!r}")
    try:
        return HdrMode(value)
    except ValueError as exc:
        raise UnsupportedEncodingCombination(f"Unsupported HDR mode: {value!r}") from exc


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise UnsupportedEncodingCombination(f"{field_name} must be a bool: {value!r}")
    return value


def _require_choice(value: object, field_name: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise UnsupportedEncodingCombination(f"{field_name} must be a string: {value!r}")
    if value not in choices:
        raise UnsupportedEncodingCombination(f"Unsupported {field_name}: {value!r}")
    return value


@dataclass(frozen=True)
class EncodingRequest:
    """User-controlled settings needed to resolve an encoding plan."""

    codec: OutputCodec
    hdr_mode: HdrMode
    hardware_enabled: bool
    preset: Literal["fast", "balanced", "quality"]
    crf: int
    container: Literal["mp4", "mov"]


@dataclass(frozen=True)
class EncodingPlan:
    """Concrete encoder contract consumed by every final-output path."""

    codec: OutputCodec
    encoder: str
    encoder_args: tuple[str, ...]
    target_transfer: HdrTransfer
    tone_map_to_sdr: bool
    pixel_format: str
    container: str

    @property
    def hdr(self) -> bool:
        """Whether the exact target transfer is HDR."""
        return self.target_transfer is not HdrTransfer.NONE


@dataclass(frozen=True)
class OutputSelection:
    """Codec/container choice with explicit override provenance already applied."""

    codec: OutputCodec
    container: Literal["mp4", "mov"]


def resolve_output_selection(
    *,
    config_codec: str,
    config_container: str,
    format_override: str | None,
) -> OutputSelection:
    """Resolve config defaults without manufacturing an omitted override."""
    overrides: dict[str, OutputSelection] = {
        "mp4": OutputSelection(OutputCodec.H264, "mp4"),
        "h265": OutputSelection(OutputCodec.H265, "mp4"),
        "h264_mov": OutputSelection(OutputCodec.H264, "mov"),
        "h265_mov": OutputSelection(OutputCodec.H265, "mov"),
        "prores": OutputSelection(OutputCodec.PRORES, "mov"),
    }
    if format_override is not None:
        try:
            return overrides[format_override.lower()]
        except KeyError as exc:
            raise UnsupportedEncodingCombination(
                f"Unsupported format override: {format_override!r}"
            ) from exc
    container = _require_choice(config_container, "container", _OUTPUT_CONTAINERS)
    return OutputSelection(
        codec=_normalize_codec(config_codec),
        container=cast(Literal["mp4", "mov"], container),
    )


def _validate_request_fields(request: EncodingRequest) -> None:
    _require_choice(request.container, "container", _OUTPUT_CONTAINERS)
    _require_choice(request.preset, "preset", _ENCODER_PRESETS)
    if (
        isinstance(request.crf, bool)
        or not isinstance(request.crf, int)
        or not 0 <= request.crf <= 51
    ):
        raise UnsupportedEncodingCombination(
            f"CRF must be an integer between 0 and 51: {request.crf!r}"
        )


def _validate_codec_policy(codec: OutputCodec, hdr_mode: HdrMode, container: str) -> None:
    if codec is OutputCodec.H264 and hdr_mode is HdrMode.HDR:
        raise UnsupportedEncodingCombination("H.264 output does not support HDR")
    if codec is OutputCodec.PRORES and hdr_mode is HdrMode.HDR:
        raise UnsupportedEncodingCombination("ProRes output does not support HDR")
    if codec is OutputCodec.PRORES and container != "mov":
        raise UnsupportedEncodingCombination("ProRes output requires a MOV container")


def _validate_encoder_family(codec: OutputCodec, encoder: str) -> None:
    if encoder not in _ENCODERS_BY_CODEC[codec]:
        raise UnsupportedEncodingCombination(
            f"Encoder {encoder!r} is incompatible with requested {codec.value} codec"
        )


def resolve_encoding_plan(
    request: EncodingRequest,
    capabilities: HWAccelCapabilities,
    input_has_hdr: bool | None = None,
    *,
    input_transfer: HdrTransfer | str | None = None,
) -> EncodingPlan:
    """Resolve output codec and hardware preference without codec substitution."""
    codec = _normalize_codec(request.codec)
    hdr_mode = _normalize_hdr_mode(request.hdr_mode)
    hardware_enabled = _require_bool(request.hardware_enabled, "hardware_enabled")
    if input_transfer is None:
        has_hdr_input = _require_bool(input_has_hdr, "input_has_hdr")
        source_transfer = HdrTransfer.HLG if has_hdr_input else HdrTransfer.NONE
    else:
        try:
            source_transfer = HdrTransfer(input_transfer)
        except (TypeError, ValueError) as exc:
            raise UnsupportedEncodingCombination(
                f"Unsupported input transfer: {input_transfer!r}"
            ) from exc
        has_hdr_input = source_transfer is not HdrTransfer.NONE
        if (
            input_has_hdr is not None
            and _require_bool(input_has_hdr, "input_has_hdr") != has_hdr_input
        ):
            raise UnsupportedEncodingCombination(
                "input_has_hdr conflicts with the exact input_transfer"
            )
    _validate_request_fields(request)
    _validate_codec_policy(codec, hdr_mode, request.container)

    selected_capabilities = capabilities if hardware_enabled else HWAccelCapabilities()
    encoder, encoder_args = get_ffmpeg_encoder(
        selected_capabilities,
        codec=codec.value,
        preset=request.preset,
    )
    _validate_encoder_family(codec, encoder)
    if encoder in {"libx264", "libx265"}:
        encoder_args.extend(["-crf", str(request.crf)])
    hdr = codec is OutputCodec.H265 and (
        hdr_mode is HdrMode.HDR or (hdr_mode is HdrMode.AUTO and has_hdr_input)
    )
    target_transfer = HdrTransfer.NONE
    if hdr:
        target_transfer = (
            source_transfer if source_transfer is not HdrTransfer.NONE else HdrTransfer.HLG
        )
    tone_map_to_sdr = has_hdr_input and not hdr
    pixel_format = "yuv420p"
    if hdr:
        pixel_format = "yuv420p10le" if encoder == "libx265" else "p010le"
    if codec is OutputCodec.PRORES:
        pixel_format = "yuv422p10le"

    return EncodingPlan(
        codec=codec,
        encoder=encoder,
        encoder_args=tuple(encoder_args),
        target_transfer=target_transfer,
        tone_map_to_sdr=tone_map_to_sdr,
        pixel_format=pixel_format,
        container=request.container,
    )
