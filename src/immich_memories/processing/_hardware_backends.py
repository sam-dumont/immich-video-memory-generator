"""Hardware backend detection functions for each acceleration type."""

from __future__ import annotations

import contextlib
import logging
import platform
import subprocess
from functools import lru_cache

from immich_memories.processing.hardware import (
    HWAccelBackend,
    HWAccelCapabilities,
    _check_ffmpeg_decoder,
    _check_ffmpeg_encoder,
    _check_ffmpeg_hwaccel,
)

logger = logging.getLogger(__name__)


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
            if parts:
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


def _detect_apple_chip_info(caps: HWAccelCapabilities) -> None:
    """Detect Apple Silicon chip generation and variant, updating caps in place."""
    _CHIP_GENERATIONS = ("M1", "M2", "M3", "M4")
    _CHIP_VARIANTS = ("Pro", "Max", "Ultra")

    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        chip_name = result.stdout.strip() if result.returncode == 0 else ""

        for gen in _CHIP_GENERATIONS:
            if gen in chip_name:
                caps.extra_info["chip_generation"] = gen
                break

        for variant in _CHIP_VARIANTS:
            if variant in chip_name:
                caps.extra_info["chip_variant"] = variant
                break

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _detect_apple_vram(caps: HWAccelCapabilities) -> None:
    """Detect VRAM for Apple Silicon (unified memory) or Intel Mac (discrete GPU)."""
    # Apple Silicon has unified memory - get total system memory
    result = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        with contextlib.suppress(ValueError):
            caps.vram_mb = int(result.stdout.strip()) // (1024 * 1024)


def _detect_apple_intel_gpu(caps: HWAccelCapabilities) -> None:
    """Detect discrete GPU info on Intel Macs."""
    import json

    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return

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
            _detect_apple_chip_info(caps)
            _detect_apple_vram(caps)
        else:
            # Intel Mac - try to get discrete GPU info if available
            _detect_apple_intel_gpu(caps)

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
