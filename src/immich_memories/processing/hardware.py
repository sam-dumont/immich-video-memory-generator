"""Hardware acceleration detection and configuration utilities."""

from __future__ import annotations

import contextlib
import logging
import platform
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Literal

logger = logging.getLogger(__name__)


class HWAccelBackend(str, Enum):
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


def _detect_nvidia() -> HWAccelCapabilities | None:
    """Detect NVIDIA GPU capabilities."""
    if not _check_ffmpeg_hwaccel("cuda"):
        return None

    caps = HWAccelCapabilities(
        backend=HWAccelBackend.NVIDIA,
        cuda_available=True,
    )

    # Check for NVENC encoders
    caps.supports_h264_encode = _check_ffmpeg_encoder("h264_nvenc")
    caps.supports_h265_encode = _check_ffmpeg_encoder("hevc_nvenc")

    # Check for NVDEC decoders
    caps.supports_h264_decode = _check_ffmpeg_decoder("h264_cuvid")
    caps.supports_h265_decode = _check_ffmpeg_decoder("hevc_cuvid")

    # NVIDIA supports GPU scaling
    caps.supports_scaling = True

    # Try to get GPU info via nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 1:
                caps.device_name = parts[0]
            if len(parts) >= 2:
                with contextlib.suppress(ValueError):
                    caps.vram_mb = int(float(parts[1]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        caps.device_name = "NVIDIA GPU"

    # Check for OpenCV CUDA support
    try:
        import cv2

        caps.opencv_cuda = cv2.cuda.getCudaEnabledDeviceCount() > 0
    except (ImportError, AttributeError, cv2.error):
        caps.opencv_cuda = False

    return caps


def _detect_apple() -> HWAccelCapabilities | None:
    """Detect Apple VideoToolbox/Metal capabilities."""
    if platform.system() != "Darwin":
        return None

    if not _check_ffmpeg_hwaccel("videotoolbox"):
        return None

    caps = HWAccelCapabilities(
        backend=HWAccelBackend.APPLE,
        metal_available=True,
    )

    # Check for VideoToolbox encoders
    caps.supports_h264_encode = _check_ffmpeg_encoder("h264_videotoolbox")
    caps.supports_h265_encode = _check_ffmpeg_encoder("hevc_videotoolbox")

    # Check for ProRes support (common on Macs)
    caps.prores_encode = _check_ffmpeg_encoder("prores_videotoolbox")
    caps.prores_decode = _check_ffmpeg_decoder("prores")

    # VideoToolbox supports hardware decode for most formats
    caps.supports_h264_decode = True
    caps.supports_h265_decode = True
    caps.supports_scaling = True

    # Check for Apple Vision framework (GPU-accelerated face detection)
    try:
        from immich_memories.analysis.apple_vision import is_vision_available

        caps.vision_available = is_vision_available()
    except ImportError:
        caps.vision_available = False

    # Get Mac model/chip info
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            caps.device_name = result.stdout.strip()

        # Check for Apple Silicon
        result = subprocess.run(
            ["sysctl", "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            caps.extra_info["apple_silicon"] = True
            caps.neural_engine = True  # All Apple Silicon has Neural Engine

            # Get chip generation for more specific info
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                chip_name = result.stdout.strip() if result.returncode == 0 else ""

                # Detect chip generation
                if "M1" in chip_name:
                    caps.extra_info["chip_generation"] = "M1"
                elif "M2" in chip_name:
                    caps.extra_info["chip_generation"] = "M2"
                elif "M3" in chip_name:
                    caps.extra_info["chip_generation"] = "M3"
                elif "M4" in chip_name:
                    caps.extra_info["chip_generation"] = "M4"

                # Check for Pro/Max/Ultra variants
                if "Pro" in chip_name:
                    caps.extra_info["chip_variant"] = "Pro"
                elif "Max" in chip_name:
                    caps.extra_info["chip_variant"] = "Max"
                elif "Ultra" in chip_name:
                    caps.extra_info["chip_variant"] = "Ultra"

            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            # Apple Silicon has unified memory - get total system memory
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                try:
                    # Convert bytes to MB
                    caps.vram_mb = int(result.stdout.strip()) // (1024 * 1024)
                except ValueError:
                    pass
        else:
            # Intel Mac - try to get discrete GPU info if available
            try:
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType", "-json"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    import json

                    data = json.loads(result.stdout)
                    displays = data.get("SPDisplaysDataType", [])
                    for display in displays:
                        gpu_name = display.get("sppci_model", "")
                        if gpu_name:
                            caps.device_name = gpu_name
                            break
                        # Check for VRAM
                        vram_str = display.get("spdisplays_vram", "")
                        if vram_str and "MB" in vram_str:
                            with contextlib.suppress(ValueError):
                                caps.vram_mb = int(vram_str.replace("MB", "").strip())
                        elif vram_str and "GB" in vram_str:
                            with contextlib.suppress(ValueError):
                                caps.vram_mb = int(float(vram_str.replace("GB", "").strip()) * 1024)
            except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
                pass

    except (FileNotFoundError, subprocess.TimeoutExpired):
        caps.device_name = "Apple GPU"

    return caps


def _detect_vaapi() -> HWAccelCapabilities | None:
    """Detect VAAPI capabilities (Linux)."""
    if platform.system() != "Linux":
        return None

    if not _check_ffmpeg_hwaccel("vaapi"):
        return None

    caps = HWAccelCapabilities(backend=HWAccelBackend.VAAPI)

    # Check for VAAPI encoders
    caps.supports_h264_encode = _check_ffmpeg_encoder("h264_vaapi")
    caps.supports_h265_encode = _check_ffmpeg_encoder("hevc_vaapi")

    # Check for VAAPI decoders
    caps.supports_h264_decode = _check_ffmpeg_decoder("h264_vaapi") or _check_ffmpeg_hwaccel(
        "vaapi"
    )
    caps.supports_h265_decode = _check_ffmpeg_decoder("hevc_vaapi") or _check_ffmpeg_hwaccel(
        "vaapi"
    )
    caps.supports_scaling = True

    # Try to get device info
    try:
        result = subprocess.run(
            ["vainfo"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "Driver version" in line:
                    caps.device_name = line.split(":")[-1].strip()
                    break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        caps.device_name = "VAAPI Device"

    return caps


def _detect_qsv() -> HWAccelCapabilities | None:
    """Detect Intel Quick Sync Video capabilities."""
    if not _check_ffmpeg_hwaccel("qsv"):
        return None

    caps = HWAccelCapabilities(backend=HWAccelBackend.QSV)

    # Check for QSV encoders
    caps.supports_h264_encode = _check_ffmpeg_encoder("h264_qsv")
    caps.supports_h265_encode = _check_ffmpeg_encoder("hevc_qsv")

    # Check for QSV decoders
    caps.supports_h264_decode = _check_ffmpeg_decoder("h264_qsv")
    caps.supports_h265_decode = _check_ffmpeg_decoder("hevc_qsv")
    caps.supports_scaling = True

    caps.device_name = "Intel Quick Sync"

    return caps


@lru_cache(maxsize=1)
def detect_hardware_acceleration() -> HWAccelCapabilities:
    """Detect available hardware acceleration.

    Returns the best available hardware acceleration backend.
    Detection order: NVIDIA > Apple > QSV > VAAPI > None

    Returns:
        HWAccelCapabilities with detected hardware info.
    """
    logger.info("Detecting hardware acceleration capabilities...")

    # Try each backend in order of preference
    detectors = [
        ("NVIDIA", _detect_nvidia),
        ("Apple", _detect_apple),
        ("Intel QSV", _detect_qsv),
        ("VAAPI", _detect_vaapi),
    ]

    for name, detector in detectors:
        try:
            caps = detector()
            if caps and caps.has_encoding:
                logger.info(f"Detected {name} hardware acceleration: {caps}")
                return caps
        except Exception as e:
            logger.debug(f"Error detecting {name}: {e}")

    logger.info("No hardware acceleration detected, using software encoding")
    return HWAccelCapabilities(backend=HWAccelBackend.NONE)


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
    nvidia_presets = {"fast": "p1", "balanced": "p4", "quality": "p7"}
    apple_presets = {"fast": "0", "balanced": "50", "quality": "100"}
    vaapi_presets = {"fast": "1", "balanced": "4", "quality": "7"}
    qsv_presets = {"fast": "veryfast", "balanced": "medium", "quality": "veryslow"}
    software_presets = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}

    if capabilities.backend == HWAccelBackend.NVIDIA:
        if codec == "h264" and capabilities.supports_h264_encode:
            return "h264_nvenc", [
                "-preset",
                nvidia_presets[preset],
                "-rc",
                "vbr",
                "-spatial-aq",
                "1",
            ]
        elif codec == "h265" and capabilities.supports_h265_encode:
            return "hevc_nvenc", [
                "-preset",
                nvidia_presets[preset],
                "-rc",
                "vbr",
                "-spatial-aq",
                "1",
            ]

    elif capabilities.backend == HWAccelBackend.APPLE:
        if codec == "h264" and capabilities.supports_h264_encode:
            return "h264_videotoolbox", [
                "-q:v",
                apple_presets[preset],
                "-allow_sw",
                "1",
            ]
        elif codec == "h265" and capabilities.supports_h265_encode:
            return "hevc_videotoolbox", [
                "-q:v",
                apple_presets[preset],
                "-allow_sw",
                "1",
            ]

    elif capabilities.backend == HWAccelBackend.VAAPI:
        if codec == "h264" and capabilities.supports_h264_encode:
            return "h264_vaapi", [
                "-compression_level",
                vaapi_presets[preset],
            ]
        elif codec == "h265" and capabilities.supports_h265_encode:
            return "hevc_vaapi", [
                "-compression_level",
                vaapi_presets[preset],
            ]

    elif capabilities.backend == HWAccelBackend.QSV:
        if codec == "h264" and capabilities.supports_h264_encode:
            return "h264_qsv", [
                "-preset",
                qsv_presets[preset],
            ]
        elif codec == "h265" and capabilities.supports_h265_encode:
            return "hevc_qsv", [
                "-preset",
                qsv_presets[preset],
            ]

    # Fallback to software encoding
    if codec == "h264":
        return "libx264", ["-preset", software_presets[preset]]
    else:
        return "libx265", ["-preset", software_presets[preset]]


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
    else:
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


def print_hardware_info(capabilities: HWAccelCapabilities) -> None:
    """Print hardware acceleration information."""
    print("\n=== Hardware Acceleration Info ===")
    print(f"Backend: {capabilities.backend.value}")
    print(f"Device: {capabilities.device_name or 'Unknown'}")

    if capabilities.vram_mb > 0:
        if capabilities.vram_mb >= 1024:
            print(f"Memory: {capabilities.vram_mb / 1024:.1f} GB")
        else:
            print(f"Memory: {capabilities.vram_mb} MB")

    print("\nVideo Encoding/Decoding:")
    print(f"  H.264 Encode: {'Yes' if capabilities.supports_h264_encode else 'No'}")
    print(f"  H.265 Encode: {'Yes' if capabilities.supports_h265_encode else 'No'}")
    print(f"  H.264 Decode: {'Yes' if capabilities.supports_h264_decode else 'No'}")
    print(f"  H.265 Decode: {'Yes' if capabilities.supports_h265_decode else 'No'}")

    # Apple-specific capabilities
    if capabilities.backend == HWAccelBackend.APPLE:
        print(f"  ProRes Encode: {'Yes' if capabilities.prores_encode else 'No'}")
        print(f"  ProRes Decode: {'Yes' if capabilities.prores_decode else 'No'}")

    print("\nGPU Processing:")
    print(f"  GPU Scaling: {'Yes' if capabilities.supports_scaling else 'No'}")

    if capabilities.backend == HWAccelBackend.NVIDIA:
        print(f"  OpenCV CUDA: {'Yes' if capabilities.opencv_cuda else 'No'}")
        print(f"  CUDA Available: {'Yes' if capabilities.cuda_available else 'No'}")

    if capabilities.backend == HWAccelBackend.APPLE:
        print(f"  Metal Available: {'Yes' if capabilities.metal_available else 'No'}")
        print(f"  Vision Framework: {'Yes' if capabilities.vision_available else 'No'}")
        print(f"  Neural Engine: {'Yes' if capabilities.neural_engine else 'No'}")

    if capabilities.extra_info:
        print("\nAdditional Info:")
        for key, value in capabilities.extra_info.items():
            # Format key nicely
            formatted_key = key.replace("_", " ").title()
            print(f"  {formatted_key}: {value}")
    print()
