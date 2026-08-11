"""Resolve requested output settings into one immutable encoding contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

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
    hdr: bool
    tone_map_to_sdr: bool
    pixel_format: str
    container: str


def _validate_request_fields(request: EncodingRequest) -> None:
    if request.container not in {"mp4", "mov"}:
        raise UnsupportedEncodingCombination(f"Unsupported output container: {request.container}")
    if request.preset not in {"fast", "balanced", "quality"}:
        raise UnsupportedEncodingCombination(f"Unsupported encoder preset: {request.preset!r}")
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
    input_has_hdr: bool,
) -> EncodingPlan:
    """Resolve output codec and hardware preference without codec substitution."""
    codec = _normalize_codec(request.codec)
    hdr_mode = _normalize_hdr_mode(request.hdr_mode)
    _validate_request_fields(request)
    _validate_codec_policy(codec, hdr_mode, request.container)

    selected_capabilities = capabilities if request.hardware_enabled else HWAccelCapabilities()
    encoder, encoder_args = get_ffmpeg_encoder(
        selected_capabilities,
        codec=codec.value,
        preset=request.preset,
    )
    _validate_encoder_family(codec, encoder)
    if encoder in {"libx264", "libx265"}:
        encoder_args.extend(["-crf", str(request.crf)])
    hdr = codec is OutputCodec.H265 and (
        hdr_mode is HdrMode.HDR or (hdr_mode is HdrMode.AUTO and input_has_hdr)
    )
    tone_map_to_sdr = input_has_hdr and not hdr
    pixel_format = "yuv420p"
    if hdr:
        pixel_format = "yuv420p10le" if encoder == "libx265" else "p010le"
    if codec is OutputCodec.PRORES:
        pixel_format = "yuv422p10le"

    return EncodingPlan(
        codec=codec,
        encoder=encoder,
        encoder_args=tuple(encoder_args),
        hdr=hdr,
        tone_map_to_sdr=tone_map_to_sdr,
        pixel_format=pixel_format,
        container=request.container,
    )
