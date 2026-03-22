"""Content-backed background generation for title screens.

Extracts a representative frame from source video clips and applies
cinematic treatment (blur + darken) for use as title screen background.
Follows the same pattern as the map title renderer in rendering_service.py.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageFilter

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def extract_representative_frame(
    clip_path: Path,
    width: int,
    height: int,
) -> np.ndarray | None:
    """Extract a single frame from a video, scaled to target dimensions.

    Seeks to 1/3 of the duration to avoid black intro frames.
    Pipes raw RGB directly from FFmpeg stdout — no temp files needed.

    Returns:
        Float32 numpy array of shape (height, width, 3), values [0, 1].
        None if extraction fails.
    """
    if not shutil.which("ffmpeg"):
        return None

    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration = float(probe.stdout.strip() or "3")
    except Exception:
        duration = 3.0

    timestamp = duration / 3.0

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-ss",
                str(timestamp),
                "-i",
                str(clip_path),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=disable",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-y",
                "pipe:1",
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None

        expected_size = width * height * 3
        if len(result.stdout) < expected_size:
            return None

        raw = np.frombuffer(result.stdout[:expected_size], dtype=np.uint8)
        return raw.reshape((height, width, 3)).astype(np.float32) / 255.0

    except Exception:
        logger.debug(f"Failed to extract frame from {clip_path}", exc_info=True)
        return None


def prepare_content_background(
    frame: np.ndarray,
    darken: float = 0.45,
    blur_radius: int = 40,
) -> np.ndarray:
    """Apply cinematic treatment: blur + darken.

    Args:
        frame: Float32 array [0, 1], shape (H, W, 3).
        darken: Multiply factor (0.45 = quite dark, similar to map's 0.55).
        blur_radius: Gaussian blur radius in pixels.

    Returns:
        Treated float32 array, same shape.
    """
    if blur_radius > 0 and HAS_PIL:
        img = Image.fromarray((frame * 255).astype(np.uint8), mode="RGB")
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        frame = np.array(img).astype(np.float32) / 255.0

    result = frame * darken
    return np.clip(result, 0.0, 1.0)


def extract_content_background(
    clip_path: Path,
    width: int,
    height: int,
    darken: float = 0.45,
    blur_radius: int = 40,
) -> np.ndarray | None:
    """One-shot: extract frame + apply treatment.

    Returns float32 array ready for TaichiTitleConfig.background_image,
    or None if extraction fails.
    """
    frame = extract_representative_frame(clip_path, width, height)
    if frame is None:
        return None
    return prepare_content_background(frame, darken, blur_radius)
