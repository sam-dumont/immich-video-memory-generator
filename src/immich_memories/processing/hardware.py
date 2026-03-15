"""Hardware acceleration detection and configuration utilities."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

logger = logging.getLogger(__name__)


class HWAccelBackend(StrEnum):
    """Available hardware acceleration backends."""

    NONE = "none"
    NVIDIA = "nvidia"  # NVENC/NVDEC (CUDA)
    APPLE = "apple"  # VideoToolbox (Metal)
    VAAPI = "vaapi"  # Video Acceleration API (Linux)
    QSV = "qsv"  # Intel Quick Sync Video
    AMF = "amf"  # AMD AMF
    VULKAN = "vulkan"  # Vulkan Video (cross-platform)


@dataclass
class HWAccelCapabilities:
    """Detected hardware acceleration capabilities."""

    backend: HWAccelBackend = HWAccelBackend.NONE
    device_name: str = ""
    supports_h264_encode: bool = False
    supports_h265_encode: bool = False
    supports_h264_decode: bool = False
    supports_h265_decode: bool = False
    supports_scaling: bool = False
    cuda_available: bool = False
    metal_available: bool = False
    opencv_cuda: bool = False
    vram_mb: int = 0
    extra_info: dict = field(default_factory=dict)

    # Apple-specific capabilities
    vision_available: bool = False  # Apple Vision framework for face detection
    neural_engine: bool = False  # Apple Neural Engine (ANE) for ML
    prores_encode: bool = False  # ProRes encoding support
    prores_decode: bool = False  # ProRes decoding support

    @property
    def has_encoding(self) -> bool:
        """Check if hardware encoding is available."""
        return self.supports_h264_encode or self.supports_h265_encode

    @property
    def has_decoding(self) -> bool:
        """Check if hardware decoding is available."""
        return self.supports_h264_decode or self.supports_h265_decode

    def __str__(self) -> str:
        if self.backend == HWAccelBackend.NONE:
            return "No hardware acceleration available"
        features = []
        if self.supports_h264_encode:
            features.append("H.264 encode")
        if self.supports_h265_encode:
            features.append("H.265 encode")
        if self.supports_h264_decode:
            features.append("H.264 decode")
        if self.supports_h265_decode:
            features.append("H.265 decode")
        return f"{self.backend.value}: {self.device_name} ({', '.join(features)})"


# ---------------------------------------------------------------------------
# FFmpeg check helpers
# ---------------------------------------------------------------------------


def _run_ffmpeg_check(args: list[str]) -> tuple[bool, str]:
    """Run an FFmpeg command and return success status and output."""
    try:
        result = subprocess.run(
            ["ffmpeg"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, str(e)


def _check_ffmpeg_encoder(encoder: str) -> bool:
    """Check if a specific FFmpeg encoder is available."""
    success, output = _run_ffmpeg_check(["-hide_banner", "-encoders"])
    return success and encoder in output


def _check_ffmpeg_decoder(decoder: str) -> bool:
    """Check if a specific FFmpeg decoder is available."""
    success, output = _run_ffmpeg_check(["-hide_banner", "-decoders"])
    return success and decoder in output


def _check_ffmpeg_hwaccel(hwaccel: str) -> bool:
    """Check if a specific FFmpeg hwaccel is available."""
    success, output = _run_ffmpeg_check(["-hide_banner", "-hwaccels"])
    return success and hwaccel in output


# ---------------------------------------------------------------------------
# FFmpeg argument builders
# ---------------------------------------------------------------------------


def get_ffmpeg_hwaccel_args(
    capabilities: HWAccelCapabilities,
    operation: Literal["decode", "encode", "both"] = "both",
    codec: str = "h264",
) -> list[str]:
    """Get FFmpeg arguments for hardware acceleration.

    Args:
        capabilities: Detected hardware capabilities.
        operation: Which operation to accelerate.
        codec: Video codec to use.

    Returns:
        List of FFmpeg arguments.
    """
    args: list[str] = []

    if capabilities.backend == HWAccelBackend.NONE:
        return args

    # Hardware decode arguments (input side)
    if operation in ("decode", "both"):
        if capabilities.backend == HWAccelBackend.NVIDIA:
            if (codec == "h264" and capabilities.supports_h264_decode) or (
                codec == "h265" and capabilities.supports_h265_decode
            ):
                args.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])

        elif capabilities.backend == HWAccelBackend.APPLE:
            args.extend(["-hwaccel", "videotoolbox"])

        elif capabilities.backend == HWAccelBackend.VAAPI:
            args.extend(["-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128"])

        elif capabilities.backend == HWAccelBackend.QSV:
            args.extend(["-hwaccel", "qsv"])

    return args


def get_ffmpeg_encoder(
    capabilities: HWAccelCapabilities,
    codec: str = "h264",
    preset: Literal["fast", "balanced", "quality"] = "balanced",
) -> tuple[str, list[str]]:
    """Get FFmpeg encoder and its arguments.

    Args:
        capabilities: Detected hardware capabilities.
        codec: Video codec to use.
        preset: Encoding speed/quality tradeoff.

    Returns:
        Tuple of (encoder_name, encoder_args).
    """
    # Preset mappings for each backend
    _PRESET_VALUES: dict[str, dict[str, str]] = {
        "nvidia": {"fast": "p1", "balanced": "p4", "quality": "p7"},
        "apple": {"fast": "0", "balanced": "50", "quality": "100"},
        "vaapi": {"fast": "1", "balanced": "4", "quality": "7"},
        "qsv": {"fast": "veryfast", "balanced": "medium", "quality": "veryslow"},
        "software": {"fast": "veryfast", "balanced": "medium", "quality": "slow"},
    }

    # Dispatch table: (backend, codec) -> (encoder, preset_key, preset_flag, is_h264)
    _ENCODER_TABLE: dict[
        tuple[HWAccelBackend, str],
        tuple[str, str, str, bool],
    ] = {
        (HWAccelBackend.NVIDIA, "h264"): ("h264_nvenc", "nvidia", "-preset", True),
        (HWAccelBackend.NVIDIA, "h265"): ("hevc_nvenc", "nvidia", "-preset", False),
        (HWAccelBackend.APPLE, "h264"): ("h264_videotoolbox", "apple", "-q:v", True),
        (HWAccelBackend.APPLE, "h265"): ("hevc_videotoolbox", "apple", "-q:v", False),
        (HWAccelBackend.VAAPI, "h264"): ("h264_vaapi", "vaapi", "-compression_level", True),
        (HWAccelBackend.VAAPI, "h265"): ("hevc_vaapi", "vaapi", "-compression_level", False),
        (HWAccelBackend.QSV, "h264"): ("h264_qsv", "qsv", "-preset", True),
        (HWAccelBackend.QSV, "h265"): ("hevc_qsv", "qsv", "-preset", False),
    }

    # Extra args appended per backend
    _EXTRA_ARGS: dict[HWAccelBackend, list[str]] = {
        HWAccelBackend.NVIDIA: ["-rc", "vbr", "-spatial-aq", "1"],
        HWAccelBackend.APPLE: ["-allow_sw", "1"],
    }

    key = (capabilities.backend, codec)
    entry = _ENCODER_TABLE.get(key)

    if entry is not None:
        encoder, preset_key, preset_flag, is_h264 = entry
        supports = (
            capabilities.supports_h264_encode if is_h264 else capabilities.supports_h265_encode
        )
        if supports:
            preset_val = _PRESET_VALUES[preset_key][preset]
            args = [preset_flag, preset_val]
            args.extend(_EXTRA_ARGS.get(capabilities.backend, []))
            return encoder, args

    # Fallback to software encoding
    sw_preset = _PRESET_VALUES["software"][preset]
    sw_encoder = "libx264" if codec == "h264" else "libx265"
    return sw_encoder, ["-preset", sw_preset]


def get_ffmpeg_scale_filter(
    capabilities: HWAccelCapabilities,
    width: int,
    height: int,
) -> str:
    """Get FFmpeg scale filter with hardware acceleration if available.

    Args:
        capabilities: Detected hardware capabilities.
        width: Target width.
        height: Target height.

    Returns:
        FFmpeg filter string.
    """
    if capabilities.backend == HWAccelBackend.NVIDIA and capabilities.supports_scaling:
        return f"scale_cuda={width}:{height}"
    elif capabilities.backend == HWAccelBackend.VAAPI and capabilities.supports_scaling:
        return f"scale_vaapi={width}:{height}"
    elif capabilities.backend == HWAccelBackend.QSV and capabilities.supports_scaling:
        return f"scale_qsv={width}:{height}"
    return f"scale={width}:{height}"


def get_opencv_backend(capabilities: HWAccelCapabilities) -> str:
    """Get the appropriate OpenCV backend string.

    Args:
        capabilities: Detected hardware capabilities.

    Returns:
        OpenCV backend identifier.
    """
    if capabilities.cuda_available and capabilities.opencv_cuda:
        return "cuda"
    return "cpu"


# ---------------------------------------------------------------------------
# Display / info helpers
# ---------------------------------------------------------------------------


def _format_gpu_info(capabilities: HWAccelCapabilities) -> list[str]:
    """Format GPU processing info lines based on backend capabilities."""
    _yn = {True: "Yes", False: "No"}
    lines = [f"  GPU Scaling: {_yn[capabilities.supports_scaling]}"]

    if capabilities.backend == HWAccelBackend.NVIDIA:
        lines.extend(
            (
                f"  OpenCV CUDA: {_yn[capabilities.opencv_cuda]}",
                f"  CUDA Available: {_yn[capabilities.cuda_available]}",
            )
        )

    if capabilities.backend == HWAccelBackend.APPLE:
        lines.extend(
            (
                f"  Metal Available: {_yn[capabilities.metal_available]}",
                f"  Vision Framework: {_yn[capabilities.vision_available]}",
                f"  Neural Engine: {_yn[capabilities.neural_engine]}",
            )
        )

    return lines


def print_hardware_info(capabilities: HWAccelCapabilities) -> None:
    """Print hardware acceleration information."""
    _yn = {True: "Yes", False: "No"}

    print("\n=== Hardware Acceleration Info ===")
    print(f"Backend: {capabilities.backend.value}")
    print(f"Device: {capabilities.device_name or 'Unknown'}")

    if capabilities.vram_mb > 0:
        if capabilities.vram_mb >= 1024:
            print(f"Memory: {capabilities.vram_mb / 1024:.1f} GB")
        else:
            print(f"Memory: {capabilities.vram_mb} MB")

    print("\nVideo Encoding/Decoding:")
    print(f"  H.264 Encode: {_yn[capabilities.supports_h264_encode]}")
    print(f"  H.265 Encode: {_yn[capabilities.supports_h265_encode]}")
    print(f"  H.264 Decode: {_yn[capabilities.supports_h264_decode]}")
    print(f"  H.265 Decode: {_yn[capabilities.supports_h265_decode]}")

    # Apple-specific capabilities
    if capabilities.backend == HWAccelBackend.APPLE:
        print(f"  ProRes Encode: {_yn[capabilities.prores_encode]}")
        print(f"  ProRes Decode: {_yn[capabilities.prores_decode]}")

    print("\nGPU Processing:")
    for line in _format_gpu_info(capabilities):
        print(line)

    if capabilities.extra_info:
        print("\nAdditional Info:")
        for key, value in capabilities.extra_info.items():
            # Format key nicely
            formatted_key = key.replace("_", " ").title()
            print(f"  {formatted_key}: {value}")
    print()


# ---------------------------------------------------------------------------
# Re-export detect_hardware_acceleration from the backends module so that
# existing ``from immich_memories.processing.hardware import ...`` keeps working.
# ---------------------------------------------------------------------------

from immich_memories.processing._hardware_backends import (  # noqa: E402, F401
    detect_hardware_acceleration,
)

__all__ = [
    "HWAccelBackend",
    "HWAccelCapabilities",
    "detect_hardware_acceleration",
    "get_ffmpeg_encoder",
    "get_ffmpeg_hwaccel_args",
    "get_ffmpeg_scale_filter",
    "get_opencv_backend",
    "print_hardware_info",
]
