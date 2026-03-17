"""Video encoding utilities and resolution helpers for title screens.

This module provides:
- GPU-accelerated encoder argument selection (VideoToolbox, NVENC, libx265 fallback)
- Resolution lookups for different orientations (landscape, portrait, square)
- HLG/HDR colorspace metadata for concat compatibility
"""

from __future__ import annotations

import contextlib
import logging

logger = logging.getLogger(__name__)


def _get_gpu_encoder_args(hdr: bool = True) -> list[str]:
    """Get GPU-accelerated encoder arguments for title screen video.

    When hdr=True, outputs 10-bit HEVC with HLG (bt2020/arib-std-b67)
    colorspace metadata for clean concatenation with HDR source clips.
    When hdr=False, outputs 8-bit video without HDR metadata for SDR sources.

    This is the SINGLE source of truth for title screen encoding.
    All title video creation paths must call this function.

    Args:
        hdr: If True, use 10-bit HLG HDR encoding. If False, use 8-bit SDR.
    """
    import subprocess
    import sys

    color_args: list[str] = []
    if hdr:
        color_args = [
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
        ]

    hdr_pix_fmt_hw = "p010le"  # 10-bit for hardware encoders
    hdr_pix_fmt_sw = "yuv420p10le"  # 10-bit for software encoder
    sdr_pix_fmt = "yuv420p"  # 8-bit for SDR

    # macOS: VideoToolbox (GPU accelerated)
    if sys.platform == "darwin":
        return [
            "-c:v",
            "hevc_videotoolbox",
            "-q:v",
            "50",
            "-pix_fmt",
            hdr_pix_fmt_hw if hdr else sdr_pix_fmt,
            "-tag:v",
            "hvc1",
            *color_args,
        ]

    # Check for NVIDIA NVENC (probe with a 1-frame encode to verify GPU is actually available)
    with contextlib.suppress(Exception):
        probe = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:d=0.01",
                "-c:v",
                "hevc_nvenc",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if probe.returncode == 0:
            return [
                "-c:v",
                "hevc_nvenc",
                "-preset",
                "p4",
                "-rc",
                "constqp",
                "-qp",
                "18",
                "-pix_fmt",
                hdr_pix_fmt_hw if hdr else sdr_pix_fmt,
                "-tag:v",
                "hvc1",
                *color_args,
            ]

    # Fallback to CPU libx265 (slower)
    return [
        "-c:v",
        "libx265",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-pix_fmt",
        hdr_pix_fmt_sw if hdr else sdr_pix_fmt,
        "-tag:v",
        "hvc1",
        *color_args,
    ]


# Standard resolutions for each orientation
ORIENTATION_RESOLUTIONS: dict[str, dict[str, tuple[int, int]]] = {
    "landscape": {
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
    },
    "portrait": {
        "720p": (720, 1280),
        "1080p": (1080, 1920),
        "4k": (2160, 3840),
    },
    "square": {
        "720p": (720, 720),
        "1080p": (1080, 1080),
        "4k": (2160, 2160),
    },
}


def get_resolution_for_orientation(
    orientation: str,
    resolution: str = "1080p",
) -> tuple[int, int]:
    """Get the appropriate resolution for an orientation.

    Args:
        orientation: One of "landscape", "portrait", "square".
        resolution: One of "720p", "1080p", "4k".

    Returns:
        Tuple of (width, height) for the given orientation and resolution.
    """
    if orientation not in ORIENTATION_RESOLUTIONS:
        orientation = "landscape"
    if resolution not in ORIENTATION_RESOLUTIONS[orientation]:
        resolution = "1080p"
    return ORIENTATION_RESOLUTIONS[orientation][resolution]
