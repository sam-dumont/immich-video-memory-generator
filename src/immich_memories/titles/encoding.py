"""Video encoding utilities and resolution helpers for title screens.

This module provides:
- GPU-accelerated encoder argument selection (VideoToolbox, NVENC, libx265 fallback)
- Resolution lookups for different orientations (landscape, portrait, square)
- HLG/HDR colorspace metadata for concat compatibility
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _get_gpu_encoder_args() -> list[str]:
    """Get GPU-accelerated encoder arguments with 10-bit HDR (HLG) support.

    Title screens must match the colorspace of video clips (bt2020/HLG)
    to avoid decoder artifacts when concatenated with stream copy.
    """
    import subprocess
    import sys

    # HLG colorspace metadata — must match _encode_single_clip in assembly.py
    color_args = [
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "arib-std-b67",
        "-colorspace",
        "bt2020nc",
    ]

    # macOS: VideoToolbox (GPU accelerated)
    if sys.platform == "darwin":
        return [
            "-c:v",
            "hevc_videotoolbox",
            "-q:v",
            "50",
            "-pix_fmt",
            "p010le",  # 10-bit
            "-tag:v",
            "hvc1",
            *color_args,
        ]

    # Check for NVIDIA NVENC
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
        )
        if "hevc_nvenc" in result.stdout:
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
                "p010le",  # 10-bit
                "-tag:v",
                "hvc1",
                *color_args,
            ]
    except Exception:
        pass

    # Fallback to CPU libx265 (slower)
    return [
        "-c:v",
        "libx265",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p10le",
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
