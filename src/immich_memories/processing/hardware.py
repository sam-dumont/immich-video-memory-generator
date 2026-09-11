"""Hardware acceleration detection and configuration utilities."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from immich_memories.processing.hardware_encode import device_args, upload_filter
from immich_memories.processing.rate_control import quality_args

logger = logging.getLogger(__name__)


class HWAccelBackend(StrEnum):
    """Available hardware acceleration backends."""

    NONE = "none"
    NVIDIA = "nvidia"  # NVENC/NVDEC (CUDA)
    APPLE = "apple"  # VideoToolbox (Metal)
    VAAPI = "vaapi"  # Video Acceleration API (Linux)
    QSV = "qsv"  # Intel Quick Sync Video


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
        return self.supports_h264_encode or self.supports_h265_encode

    @property
    def has_decoding(self) -> bool:
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
    success, output = _run_ffmpeg_check(["-hide_banner", "-encoders"])
    return success and encoder in output


# The probe has to set up exactly what a render sets up, or it proves nothing
# about the render — which is how VAAPI and QSV came to pass detection on any
# box with /dev/dri while every real encode failed.
_PROBE_UPLOAD_ARGS: dict[str, list[str]] = {
    backend: [*device_args(backend), "-vf", upload_filter(backend, "yuv420p")]
    for backend in ("vaapi", "qsv")
}


# WHY: a 64x64 RGB `testsrc` frame is smaller than what some NVENC generations
# accept, so the probe failed on a working RTX 5060 Ti and every render fell
# back to the CPU (#772). `testsrc2` is a YUV source, at a size every encoder takes.
_PROBE_SOURCE = "testsrc2=size=256x256:rate=1"


def _probe_ffmpeg_encode(encoder_args: list[str], *, upload: str | None = None) -> bool:
    """Encode one synthetic frame; the only proof a hardware encoder actually has a device.

    `ffmpeg -encoders` lists NVENC/QSV/VAAPI on any build compiled with them (Debian's is),
    so a listing check alone selects NVENC on every GPU-less Docker host (#343).
    """
    args = [
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", _PROBE_SOURCE,
        *_PROBE_UPLOAD_ARGS.get(upload or "", []),
        "-frames:v", "1", *encoder_args, "-f", "null", "-",
    ]  # fmt: skip
    success, output = _run_ffmpeg_check(args)
    if not success:
        logger.info("Hardware encoder probe failed for %s: %s", encoder_args, output.strip()[-200:])
    return success


def _check_ffmpeg_decoder(decoder: str) -> bool:
    success, output = _run_ffmpeg_check(["-hide_banner", "-decoders"])
    return success and decoder in output


def _check_ffmpeg_hwaccel(hwaccel: str) -> bool:
    success, output = _run_ffmpeg_check(["-hide_banner", "-hwaccels"])
    return success and hwaccel in output


# ---------------------------------------------------------------------------
# FFmpeg argument builders
# ---------------------------------------------------------------------------


def get_ffmpeg_hwaccel_args(
    capabilities: HWAccelCapabilities,
    operation: Literal["decode", "encode", "both"] = "both",
    codec: str = "h264",
    *,
    for_software_filters: bool = False,
) -> list[str]:
    """Get FFmpeg arguments for hardware acceleration.

    Args:
        capabilities: Detected hardware capabilities.
        operation: Which operation to accelerate.
        codec: Video codec to use.
        for_software_filters: Keep decoded frames in system memory. CUDA
            otherwise hands them back in GPU memory, where a CPU filter such
            as `scale` cannot reach them and ffmpeg fails. Only NVIDIA is
            affected; the other backends already decode to system memory.

    Returns:
        List of FFmpeg arguments.
    """
    if capabilities.backend == HWAccelBackend.NONE or operation not in ("decode", "both"):
        return []
    return _decode_hwaccel_args(capabilities, codec, for_software_filters=for_software_filters)


def _decode_hwaccel_args(
    capabilities: HWAccelCapabilities,
    codec: str,
    *,
    for_software_filters: bool,
) -> list[str]:
    if capabilities.backend == HWAccelBackend.NVIDIA:
        if (codec == "h264" and capabilities.supports_h264_decode) or (
            codec == "h265" and capabilities.supports_h265_decode
        ):
            args = ["-hwaccel", "cuda"]
            if not for_software_filters:
                args.extend(["-hwaccel_output_format", "cuda"])
            return args
        return []

    if capabilities.backend == HWAccelBackend.APPLE:
        return ["-hwaccel", "videotoolbox"]

    if capabilities.backend == HWAccelBackend.VAAPI:
        return ["-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128"]

    if capabilities.backend == HWAccelBackend.QSV:
        return ["-hwaccel", "qsv"]

    return []


def get_ffmpeg_encoder(
    capabilities: HWAccelCapabilities,
    codec: str = "h264",
    preset: Literal["fast", "balanced", "quality"] = "balanced",
) -> tuple[str, list[str]]:
    """Get FFmpeg encoder and its arguments.

    Args:
        capabilities: Detected hardware capabilities.
        codec: Video codec to use.
        preset: Encoding speed/effort policy. Image quality is configured by
            the encoding plan rather than hidden inside this backend mapping.

    Returns:
        Tuple of (encoder_name, encoder_args).
    """
    if codec not in {"h264", "h265", "prores"}:
        raise ValueError(f"Unsupported codec: {codec}")

    # ProRes hardware support varies by profile and platform.  Keep the
    # release contract deterministic by using FFmpeg's portable encoder.
    if codec == "prores":
        return "prores_ks", []

    # Preset mappings for each backend
    _PRESET_VALUES: dict[str, dict[str, str]] = {
        "nvidia": {"fast": "p1", "balanced": "p4", "quality": "p7"},
        # VideoToolbox only exposes a boolean speed-priority control. Balanced
        # and quality therefore both keep speed priority disabled; output
        # quality is supplied separately from the requested CRF.
        "apple": {"fast": "1", "balanced": "0", "quality": "0"},
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
        (HWAccelBackend.APPLE, "h264"): (
            "h264_videotoolbox",
            "apple",
            "-prio_speed",
            True,
        ),
        (HWAccelBackend.APPLE, "h265"): (
            "hevc_videotoolbox",
            "apple",
            "-prio_speed",
            False,
        ),
        (HWAccelBackend.VAAPI, "h264"): ("h264_vaapi", "vaapi", "-compression_level", True),
        (HWAccelBackend.VAAPI, "h265"): ("hevc_vaapi", "vaapi", "-compression_level", False),
        (HWAccelBackend.QSV, "h264"): ("h264_qsv", "qsv", "-preset", True),
        (HWAccelBackend.QSV, "h265"): ("hevc_qsv", "qsv", "-preset", False),
    }

    # Extra args appended per backend. Rate control is deliberately absent: it
    # belongs to rate_control.quality_args, which the plan appends after these.
    # `-rc vbr` used to live here and would now be overridden by `-rc constqp`
    # two flags later — one of them has to be wrong, so only one sets the mode.
    # `-spatial-aq` is an adaptive-quantisation knob, not a mode, and still applies.
    _EXTRA_ARGS: dict[HWAccelBackend, list[str]] = {
        HWAccelBackend.NVIDIA: ["-spatial-aq", "1"],
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
        # A backend can encode one codec and not the other — Gemini Lake VAAPI
        # advertises H.264 encode and no HEVC, and h265 is the default output.
        logger.info(
            "%s cannot encode %s on this device; encoding it in software",
            capabilities.backend.value,
            codec,
        )

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


# Temp files for analysis and previews are never shown to anyone, so they run at
# the CRF the "low" quality preset means. The rate control comes from the same
# mapping the final output uses, so "CRF 28" costs the same picture on every
# backend instead of each one inventing its own default.
_FAST_CRF = 28
_FAST_SPEED_ARGS: dict[HWAccelBackend, list[str]] = {
    HWAccelBackend.NVIDIA: ["-preset", "p1"],
    HWAccelBackend.QSV: ["-preset", "veryfast"],
}
_FAST_ENCODERS: dict[HWAccelBackend, str] = {
    HWAccelBackend.NVIDIA: "h264_nvenc",
    HWAccelBackend.VAAPI: "h264_vaapi",
    HWAccelBackend.QSV: "h264_qsv",
}
_SOFTWARE_FAST_ARGS = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(_FAST_CRF)]


def _fast_args_for(encoder: str, backend: HWAccelBackend) -> list[str]:
    return ["-c:v", encoder, *_FAST_SPEED_ARGS.get(backend, []), *quality_args(encoder, _FAST_CRF)]


def fast_encoder_args(*, hardware_enabled: bool = True) -> list[str]:
    """Speed-first encoder args for analysis/preview temp files.

    Uses the probed backend from `detect_hardware_acceleration()` — never a bare
    `ffmpeg -encoders` listing, which advertises NVENC/VAAPI/QSV on GPU-less boxes (#343).
    """
    if not hardware_enabled:
        return _SOFTWARE_FAST_ARGS.copy()
    if sys.platform == "darwin":
        return _fast_args_for("h264_videotoolbox", HWAccelBackend.APPLE)
    backend = detect_hardware_acceleration().backend
    encoder = _FAST_ENCODERS.get(backend)
    if encoder is None:
        return _SOFTWARE_FAST_ARGS.copy()
    return _fast_args_for(encoder, backend)


# ---------------------------------------------------------------------------
# Re-export detect_hardware_acceleration from the backends module so that
# existing ``from immich_memories.processing.hardware import ...`` keeps working.
# ---------------------------------------------------------------------------

from immich_memories.processing.hardware_detection import (  # noqa: E402, F401
    detect_hardware_acceleration,
)

__all__ = [
    "HWAccelBackend",
    "HWAccelCapabilities",
    "detect_hardware_acceleration",
    "fast_encoder_args",
    "get_ffmpeg_encoder",
    "get_ffmpeg_hwaccel_args",
    "get_ffmpeg_scale_filter",
    "get_opencv_backend",
    "print_hardware_info",
]
