"""FFmpeg-based video transform operations.

Helper module for transforms.py — contains the fit, fill, and crop
FFmpeg pipelines with hardware-acceleration support.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from immich_memories.config_models import HardwareAccelConfig
from immich_memories.processing.hardware import (
    HWAccelBackend,
    HWAccelCapabilities,
    detect_hardware_acceleration,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
)
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardware capability cache
# ---------------------------------------------------------------------------

_hw_caps: HWAccelCapabilities | None = None


def _get_hw_caps() -> HWAccelCapabilities:
    global _hw_caps
    if _hw_caps is None:
        _hw_caps = detect_hardware_acceleration()
    return _hw_caps


def _build_encode_args(
    hw_caps: HWAccelCapabilities | None,
    hardware_config: HardwareAccelConfig,
    output_crf: int,
    codec: str = "h264",
) -> list[str]:
    """Build FFmpeg encoding arguments, using hardware acceleration when available."""
    args: list[str] = []

    if hw_caps and hw_caps.has_encoding and hardware_config.enabled:
        encoder, encoder_args = get_ffmpeg_encoder(
            hw_caps,
            codec=codec,
            preset=hardware_config.encoder_preset,
        )
        args.extend(["-c:v", encoder])
        args.extend(encoder_args)

        # Quality parameter varies by encoder
        if "nvenc" in encoder:
            args.extend(["-cq", str(output_crf)])
        elif "videotoolbox" not in encoder:
            if "vaapi" in encoder or "qsv" in encoder:
                args.extend(["-global_quality", str(output_crf)])
            else:
                args.extend(["-crf", str(output_crf)])

        logger.debug(f"Using hardware encoder: {encoder}")
    else:
        args.extend(["-c:v", "libx264"])
        args.extend(["-preset", "medium"])
        args.extend(["-crf", str(output_crf)])

    args.extend(["-c:a", "aac", "-b:a", "128k"])
    args.extend(["-movflags", "+faststart"])

    return args


# ---------------------------------------------------------------------------
# CropRegion dataclass
# ---------------------------------------------------------------------------


@dataclass
class CropRegion:
    """A crop region within a frame."""

    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_ffmpeg_filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


# ---------------------------------------------------------------------------
# Video probing
# ---------------------------------------------------------------------------


def get_video_dimensions(video_path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return (0, 0)

    try:
        parts = result.stdout.strip().split(",")
        return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return (0, 0)


# ---------------------------------------------------------------------------
# Fit transform (letterbox / pillarbox with blur background)
# ---------------------------------------------------------------------------


def transform_fit(
    input_path: Path,
    output_path: Path,
    target_resolution: tuple[int, int],
    hardware_config: HardwareAccelConfig,
    output_crf: int,
) -> Path:
    """Transform using letterbox/pillarbox with blurred background.

    Uses hardware acceleration for encoding when available.
    """
    target_w, target_h = target_resolution
    hw_caps = _get_hw_caps() if hardware_config.enabled else None

    cmd = ["ffmpeg", "-y"]

    if hw_caps and hw_caps.has_decoding and hardware_config.gpu_decode:
        hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
        cmd.extend(hwaccel_args)

    cmd.extend(["-i", str(input_path)])

    filter_complex = (
        f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[bg];"
        f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )

    cmd.extend(["-filter_complex", filter_complex])

    encode_args = _build_encode_args(hw_caps, hardware_config, output_crf)
    cmd.extend(encode_args)
    cmd.append(str(output_path))

    logger.debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        if hw_caps and hw_caps.has_encoding:
            logger.warning("Hardware encoding failed, falling back to software")
            return _transform_fit_software(input_path, output_path, target_resolution, output_crf)
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"Failed to transform video: {result.stderr}")

    return output_path


def _transform_fit_software(
    input_path: Path,
    output_path: Path,
    target_resolution: tuple[int, int],
    output_crf: int,
) -> Path:
    """Software-only fallback for letterbox/pillarbox."""
    target_w, target_h = target_resolution

    filter_complex = (
        f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[bg];"
        f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(output_crf),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to transform video: {result.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# Fill transform (center crop)
# ---------------------------------------------------------------------------


def transform_fill(
    input_path: Path,
    output_path: Path,
    target_resolution: tuple[int, int],
    hardware_config: HardwareAccelConfig,
    output_crf: int,
) -> Path:
    """Transform using center crop to fill frame.

    Uses hardware acceleration when available.
    """
    target_w, target_h = target_resolution
    hw_caps = _get_hw_caps() if hardware_config.enabled else None

    cmd = ["ffmpeg", "-y"]

    if hw_caps and hw_caps.has_decoding and hardware_config.gpu_decode:
        hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
        cmd.extend(hwaccel_args)

    cmd.extend(["-i", str(input_path)])

    if hw_caps and hw_caps.backend == HWAccelBackend.NVIDIA and hw_caps.supports_scaling:
        filter_str = (
            f"hwupload_cuda,"
            f"scale_cuda={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"hwdownload,format=nv12,"
            f"crop={target_w}:{target_h}"
        )
    else:
        filter_str = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )

    cmd.extend(["-vf", filter_str])

    encode_args = _build_encode_args(hw_caps, hardware_config, output_crf)
    cmd.extend(encode_args)
    cmd.append(str(output_path))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        if hw_caps and (hw_caps.has_encoding or hw_caps.supports_scaling):
            logger.warning("Hardware processing failed, falling back to software")
            return _transform_fill_software(input_path, output_path, target_resolution, output_crf)
        raise RuntimeError(f"Failed to transform video: {result.stderr}")

    return output_path


def _transform_fill_software(
    input_path: Path,
    output_path: Path,
    target_resolution: tuple[int, int],
    output_crf: int,
) -> Path:
    """Software-only fallback for fill mode."""
    target_w, target_h = target_resolution

    filter_str = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(output_crf),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to transform video: {result.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# Crop transform (smart crop applies crop region then scales)
# ---------------------------------------------------------------------------


def apply_crop_transform(
    input_path: Path,
    output_path: Path,
    crop: CropRegion,
    target_resolution: tuple[int, int],
    hardware_config: HardwareAccelConfig,
    output_crf: int,
) -> Path:
    """Apply crop and scale to target resolution.

    Uses hardware acceleration when available.
    """
    target_w, target_h = target_resolution
    hw_caps = _get_hw_caps() if hardware_config.enabled else None

    cmd = ["ffmpeg", "-y"]

    if hw_caps and hw_caps.has_decoding and hardware_config.gpu_decode:
        hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
        cmd.extend(hwaccel_args)

    cmd.extend(["-i", str(input_path)])

    filter_str = f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y},scale={target_w}:{target_h}"

    cmd.extend(["-vf", filter_str])

    encode_args = _build_encode_args(hw_caps, hardware_config, output_crf)
    cmd.extend(encode_args)
    cmd.append(str(output_path))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        if hw_caps and hw_caps.has_encoding:
            logger.warning("Hardware encoding failed, falling back to software")
            return _apply_crop_transform_software(
                input_path, output_path, crop, target_resolution, output_crf
            )
        raise RuntimeError(f"Failed to transform video: {result.stderr}")

    return output_path


def _apply_crop_transform_software(
    input_path: Path,
    output_path: Path,
    crop: CropRegion,
    target_resolution: tuple[int, int],
    output_crf: int,
) -> Path:
    """Software-only fallback for crop transform."""
    target_w, target_h = target_resolution

    filter_str = f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y},scale={target_w}:{target_h}"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(output_crf),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to transform video: {result.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# Date overlay
# ---------------------------------------------------------------------------


def add_date_overlay(
    input_path: Path,
    output_path: Path,
    date_text: str,
    output_crf: int,
    position: str = "bottom-right",
    font_size: int = 24,
    opacity: float = 0.7,
) -> Path:
    """Add a date text overlay to a video with shadow for readability."""
    input_path = validate_video_path(input_path, must_exist=True)

    positions = {
        "bottom-left": "x=20:y=h-th-20",
        "bottom-right": "x=w-tw-20:y=h-th-20",
        "top-left": "x=20:y=20",
        "top-right": "x=w-tw-20:y=20",
    }
    pos_str = positions.get(position, positions["bottom-right"])

    filter_str = (
        f"drawtext=text='{date_text}':"
        f"fontsize={font_size}:"
        f"fontcolor=white@{opacity}:"
        f"shadowcolor=black@0.5:"
        f"shadowx=2:shadowy=2:"
        f"{pos_str}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(output_crf),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to add date overlay: {result.stderr}")

    return output_path
