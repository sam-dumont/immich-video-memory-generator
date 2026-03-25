"""System information capture utilities."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from typing import TYPE_CHECKING

from immich_memories.tracking.models import SystemInfo

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def capture_system_info() -> SystemInfo:
    """Capture current system specifications.

    Returns:
        SystemInfo with current hardware and software details.
    """
    return SystemInfo(
        platform=platform.system().lower(),
        platform_version=platform.platform(),
        python_version=platform.python_version(),
        machine_arch=platform.machine(),
        cpu_brand=_get_cpu_brand(),
        cpu_cores=os.cpu_count() or 0,
        ram_gb=_get_ram_gb(),
        hw_accel_backend=_get_hw_accel_backend(),
        gpu_name=_get_gpu_name(),
        vram_mb=_get_vram_mb(),
        ffmpeg_version=_get_ffmpeg_version(),
        opencv_version=_get_opencv_version(),
        taichi_available=_check_taichi(),
    )


def _get_cpu_brand_macos() -> str | None:
    """Get CPU brand on macOS via sysctl."""
    result = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _get_cpu_brand_linux() -> str | None:
    """Get CPU brand on Linux via /proc/cpuinfo."""
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                return line.split(":")[1].strip()
    return None


def _get_cpu_brand_windows() -> str | None:
    """Get CPU brand on Windows via wmic."""
    result = subprocess.run(
        ["wmic", "cpu", "get", "name"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().split("\n")
    return lines[1].strip() if len(lines) > 1 else None


def _get_cpu_brand() -> str | None:
    """Get CPU brand string."""
    _cpu_brand_funcs = {
        "darwin": _get_cpu_brand_macos,
        "linux": _get_cpu_brand_linux,
        "windows": _get_cpu_brand_windows,
    }
    try:
        system = platform.system().lower()
        fn = _cpu_brand_funcs.get(system)
        return fn() if fn else None
    except Exception as e:
        logger.debug(f"Failed to get CPU brand: {e}")
    return None


def _get_ram_gb_macos() -> float:
    """Get RAM in GB on macOS via sysctl."""
    result = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        return int(result.stdout.strip()) / (1024**3)
    return 0.0


def _get_ram_gb_linux() -> float:
    """Get RAM in GB on Linux via /proc/meminfo."""
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal"):
                kb = int(line.split()[1])
                return kb / (1024**2)
    return 0.0


def _get_ram_gb_windows() -> float:
    """Get RAM in GB on Windows via wmic."""
    result = subprocess.run(
        ["wmic", "computersystem", "get", "totalphysicalmemory"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return 0.0
    lines = result.stdout.strip().split("\n")
    if len(lines) > 1:
        return int(lines[1].strip()) / (1024**3)
    return 0.0


def _get_ram_gb() -> float:
    """Get total RAM in GB."""
    _ram_funcs = {
        "darwin": _get_ram_gb_macos,
        "linux": _get_ram_gb_linux,
        "windows": _get_ram_gb_windows,
    }
    try:
        system = platform.system().lower()
        fn = _ram_funcs.get(system)
        return fn() if fn else 0.0
    except Exception as e:
        logger.debug(f"Failed to get RAM: {e}")
    return 0.0


def _get_hw_accel_backend() -> str | None:
    """Get detected hardware acceleration backend."""
    try:
        from immich_memories.processing.hardware import detect_hardware_acceleration

        hw_caps = detect_hardware_acceleration()
        if hw_caps and hw_caps.backend:
            return hw_caps.backend.value
    except Exception as e:
        logger.debug(f"Failed to detect HW acceleration: {e}")

    return None


def _get_gpu_name_macos() -> str | None:
    """Get GPU name on macOS via system_profiler."""
    import json

    result = subprocess.run(
        ["system_profiler", "SPDisplaysDataType", "-json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    displays = json.loads(result.stdout).get("SPDisplaysDataType", [])
    return displays[0].get("sppci_model", None) if displays else None


def _get_gpu_name_linux_nvidia() -> str | None:
    """Get NVIDIA GPU name on Linux via nvidia-smi."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        return result.stdout.strip().split("\n")[0]
    return None


def _get_gpu_name_linux_lspci() -> str | None:
    """Get GPU name on Linux via lspci fallback."""
    result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return None
    for line in result.stdout.split("\n"):
        if "VGA" in line or "3D" in line:
            parts = line.split(":")
            if len(parts) >= 3:
                return parts[-1].strip()
    return None


def _get_gpu_name_linux() -> str | None:
    """Get GPU name on Linux (nvidia-smi then lspci fallback)."""
    return _get_gpu_name_linux_nvidia() or _get_gpu_name_linux_lspci()


def _get_gpu_name() -> str | None:
    """Get GPU name if available."""
    _gpu_name_funcs = {
        "darwin": _get_gpu_name_macos,
        "linux": _get_gpu_name_linux,
    }
    try:
        system = platform.system().lower()
        fn = _gpu_name_funcs.get(system)
        return fn() if fn else None
    except Exception as e:
        logger.debug(f"Failed to get GPU name: {e}")
    return None


def _get_vram_mb() -> int:
    """Get GPU VRAM in MB if available."""
    try:
        system = platform.system().lower()

        if system == "linux":
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return int(result.stdout.strip().split("\n")[0])

    except Exception as e:
        logger.debug(f"Failed to get VRAM: {e}")

    return 0


def _get_ffmpeg_version() -> str | None:
    """Get FFmpeg version string."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # First line is like "ffmpeg version 7.0.1 Copyright..."
            first_line = result.stdout.split("\n")[0]
            parts = first_line.split()
            if len(parts) >= 3:
                return parts[2]
    except Exception as e:
        logger.debug(f"Failed to get FFmpeg version: {e}")

    return None


def _get_opencv_version() -> str | None:
    """Get OpenCV version if installed."""
    try:
        import cv2

        return cv2.__version__
    except ImportError:
        return None


def _check_taichi() -> bool:
    """Check if Taichi is available and working."""
    try:
        import os

        os.environ.setdefault("TI_LOG_LEVEL", "error")
        import taichi as ti

        ti.init(arch=ti.gpu, offline_cache=True, log_level=ti.ERROR)
        return True
    except Exception:
        return False
