"""HDR detection and conversion utilities.

This module provides functions for detecting HDR types (HLG, PQ/HDR10),
converting between HDR formats, and selecting appropriate GPU encoder
arguments for HDR-aware encoding.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from immich_memories.security import validate_video_path

__all__ = [
    "_detect_hdr_type",
    "_detect_color_primaries",
    "_get_dominant_hdr_type",
    "_get_colorspace_filter",
    "_get_hdr_conversion_filter",
    "_get_clip_hdr_types",
    "_get_gpu_encoder_args",
]

logger = logging.getLogger(__name__)


def _detect_hdr_type(video_path: Path) -> str | None:
    """Detect the HDR type of a video file.

    Returns:
        "hlg" for HLG (iPhone Dolby Vision 8.4)
        "pq" for HDR10/HDR10+ (Samsung, Pixel, etc.)
        None if SDR or unknown
    """
    video_path = validate_video_path(video_path, must_exist=True)
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                color_trc = streams[0].get("color_transfer", "")
                if color_trc == "arib-std-b67":
                    return "hlg"  # iPhone HLG / Dolby Vision 8.4
                elif color_trc == "smpte2084":
                    return "pq"  # HDR10 / HDR10+ (Samsung, Pixel)
                elif color_trc in ("bt2020-10", "bt2020-12"):
                    return "pq"  # Assume PQ for BT.2020
    except Exception as e:
        logger.debug(f"HDR detection failed for {video_path}: {e}")
    return None


def _detect_color_primaries(video_path: Path | str) -> str | None:
    """Detect the color primaries of a video file.

    Returns primaries string like "bt709", "smpte432" (Display P3),
    "bt2020", or None if detection fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_primaries",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                return streams[0].get("color_primaries") or None
    except Exception as e:
        logger.debug(f"Color primaries detection failed for {video_path}: {e}")
    return None


def _get_dominant_hdr_type(clips: list) -> str:
    """Detect the dominant HDR type from a list of clips.

    Returns "hlg" or "pq" based on what most clips use.
    Defaults to "hlg" if detection fails (iPhone is most common).
    """
    hdr_types: dict[str, int] = {"hlg": 0, "pq": 0}

    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        if hdr_type:
            hdr_types[hdr_type] += 1

    # Return dominant type, default to HLG if tied or none detected
    if hdr_types["pq"] > hdr_types["hlg"]:
        logger.info(f"Detected HDR10/PQ format (Android/Samsung/Pixel) - {hdr_types['pq']} clips")
        return "pq"
    elif hdr_types["hlg"] > 0:
        logger.info(f"Detected HLG format (iPhone) - {hdr_types['hlg']} clips")
        return "hlg"
    else:
        logger.info("No HDR detected, defaulting to HLG colorspace")
        return "hlg"


def _get_colorspace_filter(hdr_type: str) -> str:
    """Get the setparams filter string for the given HDR type.

    Args:
        hdr_type: "hlg" for HLG, "pq" for HDR10/HDR10+

    Returns:
        FFmpeg setparams filter string
    """
    if hdr_type == "pq":
        # HDR10/HDR10+ (Samsung, Pixel, etc.) - uses PQ/SMPTE2084 transfer
        return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084"
    else:
        # HLG (iPhone Dolby Vision 8.4) - uses ARIB STD-B67 transfer
        return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67"


def _get_hdr_conversion_filter(
    source_type: str | None,
    target_type: str,
    source_primaries: str | None = None,
) -> str:
    """Get filter to convert between HDR formats (HLG <-> PQ) or SDR -> HDR.

    Uses zscale for proper colorspace and transfer function conversion.
    Falls back to colorspace filter if zscale unavailable.

    Args:
        source_type: Source HDR type ("hlg", "pq", "sdr", or None for unknown)
        target_type: Target HDR type ("hlg" or "pq")
        source_primaries: Source color primaries (e.g. "bt709", "smpte432" for
            Display P3). When None, defaults to "bt709" for SDR sources.

    Returns:
        FFmpeg filter string for conversion, or empty string if no conversion needed
    """
    # No conversion needed if source matches target
    if source_type == target_type:
        return ""

    # Check if zscale is available (required for proper conversion)
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
        )
        has_zscale = "zscale" in result.stdout
    except Exception:
        has_zscale = False

    # SDR -> HDR conversion (upscale SDR to HDR colorspace)
    if source_type is None or source_type == "sdr":
        # Use actual source primaries if known (e.g. smpte432 for Display P3)
        src_pri = source_primaries or "bt709"
        src_matrix = "bt709" if src_pri in ("bt709", "smpte432") else src_pri
        if target_type == "hlg":
            if has_zscale:
                logger.debug(f"Converting SDR ({src_pri}) -> HLG")
                return f",zscale=transfer=arib-std-b67:transferin=bt709:primaries=bt2020:primariesin={src_pri}:matrix=bt2020nc:matrixin={src_matrix}"
            else:
                logger.warning("zscale not available - SDR to HDR conversion may look washed out")
                return ""
        elif target_type == "pq":
            if has_zscale:
                logger.debug(f"Converting SDR ({src_pri}) -> PQ/HDR10")
                return f",zscale=transfer=smpte2084:transferin=bt709:primaries=bt2020:primariesin={src_pri}:matrix=bt2020nc:matrixin={src_matrix}"
            else:
                logger.warning("zscale not available - SDR to HDR conversion may look washed out")
                return ""
        return ""

    # HDR -> HDR conversion (HLG <-> PQ)
    if source_type == "hlg" and target_type == "pq":
        # HLG (iPhone) -> PQ (HDR10)
        if has_zscale:
            return ",zscale=transfer=smpte2084:transferin=arib-std-b67:primaries=bt2020:primariesin=bt2020:matrix=bt2020nc:matrixin=bt2020nc"
        else:
            logger.warning("zscale not available - HDR conversion may not be accurate")
            return ""
    elif source_type == "pq" and target_type == "hlg":
        # PQ (HDR10) -> HLG (iPhone)
        if has_zscale:
            return ",zscale=transfer=arib-std-b67:transferin=smpte2084:primaries=bt2020:primariesin=bt2020:matrix=bt2020nc:matrixin=bt2020nc"
        else:
            logger.warning("zscale not available - HDR conversion may not be accurate")
            return ""

    return ""


def _get_clip_hdr_types(clips: list) -> list[str | None]:
    """Get HDR type for each clip in the list.

    Returns:
        List of HDR types ("hlg", "pq", or None) for each clip
    """
    hdr_types = []
    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        hdr_types.append(hdr_type)
    return hdr_types


def _get_gpu_encoder_args(
    crf: int = 23, preserve_hdr: bool = False, hdr_type: str = "hlg"
) -> list[str]:
    """Get GPU-accelerated encoder arguments.

    Uses hardware encoding when available:
    - macOS: hevc_videotoolbox (Apple Silicon GPU)
    - NVIDIA: hevc_nvenc (CUDA)
    - Fallback: libx265/libx264 (CPU)

    Args:
        crf: Quality level (lower = better, 0-51)
        preserve_hdr: If True, use 10-bit HDR settings
        hdr_type: "hlg" for iPhone HLG, "pq" for Android HDR10/HDR10+

    Returns:
        List of FFmpeg encoder arguments.
    """
    # Select correct transfer function based on HDR type
    # HLG (iPhone): arib-std-b67
    # HDR10/HDR10+ (Android/Samsung/Pixel): smpte2084 (PQ)
    color_trc = "smpte2084" if hdr_type == "pq" else "arib-std-b67"

    # macOS: Use VideoToolbox (GPU accelerated)
    if sys.platform == "darwin":
        # Map CRF to VideoToolbox quality (inverse relationship)
        # CRF 18 = high quality, CRF 28 = lower quality
        # VT quality: 0-100, higher = better
        vt_quality = max(10, min(90, 100 - (crf * 3)))

        if preserve_hdr:
            return [
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                str(vt_quality),
                "-pix_fmt",
                "p010le",  # 10-bit
                "-tag:v",
                "hvc1",
                "-colorspace",
                "bt2020nc",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                color_trc,
            ]
        else:
            return [
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                str(vt_quality),
                "-tag:v",
                "hvc1",
            ]

    # Check for NVIDIA NVENC
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
        )
        if "hevc_nvenc" in result.stdout:
            if preserve_hdr:
                return [
                    "-c:v",
                    "hevc_nvenc",
                    "-preset",
                    "p4",
                    "-rc",
                    "constqp",
                    "-qp",
                    str(crf),
                    "-pix_fmt",
                    "p010le",  # 10-bit
                    "-tag:v",
                    "hvc1",
                    "-colorspace",
                    "bt2020nc",
                    "-color_primaries",
                    "bt2020",
                    "-color_trc",
                    color_trc,
                ]
            else:
                return [
                    "-c:v",
                    "hevc_nvenc",
                    "-preset",
                    "p4",
                    "-rc",
                    "constqp",
                    "-qp",
                    str(crf),
                    "-tag:v",
                    "hvc1",
                ]
    except Exception:
        pass

    # Fallback to CPU encoding
    if preserve_hdr:
        # x265 transfer parameter name
        x265_transfer = "smpte2084" if hdr_type == "pq" else "arib-std-b67"
        return [
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p10le",
            "-tag:v",
            "hvc1",
            "-colorspace",
            "bt2020nc",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            color_trc,
            "-x265-params",
            f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer={x265_transfer}:colormatrix=bt2020nc",
        ]
    else:
        return [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
        ]
