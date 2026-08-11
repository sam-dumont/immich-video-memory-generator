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


def resolve_encoding_plan(
    request: EncodingRequest,
    capabilities: HWAccelCapabilities,
    input_has_hdr: bool,
) -> EncodingPlan:
    """Resolve output codec and hardware preference without codec substitution."""
    if request.container not in {"mp4", "mov"}:
        raise UnsupportedEncodingCombination(f"Unsupported output container: {request.container}")
    if request.codec is OutputCodec.H264 and request.hdr_mode is HdrMode.HDR:
        raise UnsupportedEncodingCombination("H.264 output does not support HDR")
    if request.codec is OutputCodec.PRORES and request.hdr_mode is HdrMode.HDR:
        raise UnsupportedEncodingCombination("ProRes output does not support HDR")
    if request.codec is OutputCodec.PRORES and request.container != "mov":
        raise UnsupportedEncodingCombination("ProRes output requires a MOV container")

    selected_capabilities = capabilities if request.hardware_enabled else HWAccelCapabilities()
    encoder, encoder_args = get_ffmpeg_encoder(
        selected_capabilities,
        codec=request.codec.value,
        preset=request.preset,
    )
    if encoder in {"libx264", "libx265"}:
        encoder_args.extend(["-crf", str(request.crf)])
    hdr = request.codec is OutputCodec.H265 and (
        request.hdr_mode is HdrMode.HDR or (request.hdr_mode is HdrMode.AUTO and input_has_hdr)
    )
    tone_map_to_sdr = input_has_hdr and not hdr
    pixel_format = "yuv420p"
    if hdr:
        pixel_format = "yuv420p10le" if encoder == "libx265" else "p010le"
    if request.codec is OutputCodec.PRORES:
        pixel_format = "yuv422p10le"

    return EncodingPlan(
        codec=request.codec,
        encoder=encoder,
        encoder_args=tuple(encoder_args),
        hdr=hdr,
        tone_map_to_sdr=tone_map_to_sdr,
        pixel_format=pixel_format,
        container=request.container,
    )
