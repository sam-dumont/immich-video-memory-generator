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


def _get_cpu_brand() -> str | None:
    """Get CPU brand string."""
    try:
        system = platform.system().lower()

        if system == "darwin":
            # macOS: use sysctl
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()

        elif system == "linux":
            # Linux: parse /proc/cpuinfo
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":")[1].strip()

        elif system == "windows":
            # Windows: use wmic
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()

    except Exception as e:
        logger.debug(f"Failed to get CPU brand: {e}")

    return None


def _get_ram_gb() -> float:
    """Get total RAM in GB."""
    try:
        system = platform.system().lower()

        if system == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                bytes_ram = int(result.stdout.strip())
                return bytes_ram / (1024**3)

        elif system == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        return kb / (1024**2)

        elif system == "windows":
            result = subprocess.run(
                ["wmic", "computersystem", "get", "totalphysicalmemory"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    bytes_ram = int(lines[1].strip())
                    return bytes_ram / (1024**3)

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


def _get_gpu_name() -> str | None:
    """Get GPU name if available."""
    try:
        system = platform.system().lower()

        if system == "darwin":
            # macOS: Use system_profiler
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
                if displays:
                    return displays[0].get("sppci_model", None)

        elif system == "linux":
            # Linux: Try nvidia-smi first
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0]

            # Fall back to lspci
            result = subprocess.run(
                ["lspci"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "VGA" in line or "3D" in line:
                        # Extract GPU name after the colon
                        parts = line.split(":")
                        if len(parts) >= 3:
                            return parts[-1].strip()

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
        import taichi as ti

        # Try to initialize - this validates GPU support
        ti.init(arch=ti.gpu, offline_cache=True, log_level=ti.WARN)
        return True
    except Exception:
        return False
