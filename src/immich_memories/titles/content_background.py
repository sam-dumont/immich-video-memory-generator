"""Content-backed background generation for title screens.

Extracts video content and applies cinematic treatment for title backgrounds.
Supports both static frames and slow-motion video backgrounds.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageFilter

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _probe_duration(clip_path: Path) -> float:
    """Get video duration via ffprobe."""
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
        return float(probe.stdout.strip() or "3")
    except Exception:
        return 3.0


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

    duration = _probe_duration(clip_path)
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
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
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


class SlowmoBackgroundReader:
    """Generates smooth slow-motion frames via linear interpolation.

    Pre-reads all source frames (typically 15 at 0.5s/30fps), then
    generates interpolated intermediate frames on demand. Linear blending
    creates motion-blur-like ghosting that merges with the Taichi
    renderer's heavy Gaussian blur — no optical flow needed.
    """

    def __init__(
        self,
        clip_path: Path,
        width: int,
        height: int,
        fps: float,
        title_duration: float = 3.5,
        source_seconds: float = 0.5,
        hdr: bool = False,
    ):
        self._source_frames: list[np.ndarray] = []
        self._output_index = 0
        self._total_output_frames = int(title_duration * fps)

        if not shutil.which("ffmpeg"):
            return

        clip_duration = _probe_duration(clip_path)
        actual_source = min(source_seconds, clip_duration * 0.8)
        if actual_source < 0.1:
            return

        pix_fmt = "rgb48le" if hdr else "rgb24"
        bpp = 6 if hdr else 3
        frame_size = width * height * bpp

        # WHY: extract source frames at native fps — no slowdown in FFmpeg.
        # Interpolation happens in Python for smooth blending.
        cmd = [
            "ffmpeg",
            "-ss", "0",
            "-t", str(actual_source),
            "-i", str(clip_path),
            "-f", "rawvideo",
            "-pix_fmt", pix_fmt,
            "-an",
            "pipe:1",
        ]  # fmt: skip

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            logger.debug(f"Failed to extract frames from {clip_path}", exc_info=True)
            return

        if result.returncode != 0:
            return

        # Read all source frames into memory (~150MB for 15 frames at 4K 16-bit)
        data = result.stdout
        offset = 0
        while offset + frame_size <= len(data):
            chunk = data[offset : offset + frame_size]
            if hdr:
                raw = np.frombuffer(chunk, dtype=np.uint16)
                frame = raw.reshape((height, width, 3)).astype(np.float32) / 65535.0
            else:
                raw = np.frombuffer(chunk, dtype=np.uint8)
                frame = raw.reshape((height, width, 3)).astype(np.float32) / 255.0
            self._source_frames.append(frame)
            offset += frame_size

        logger.info(
            f"Loaded {len(self._source_frames)} source frames for "
            f"{self._total_output_frames} output frames ({title_duration}s)"
        )

    @property
    def is_active(self) -> bool:
        return len(self._source_frames) >= 2

    def read_frame(self) -> np.ndarray | None:
        """Generate the next interpolated frame.

        Maps output frame index to a fractional position in the source
        frames, then linearly blends the two adjacent source frames.
        """
        if not self.is_active:
            return None
        if self._output_index >= self._total_output_frames:
            return None

        # WHY: ease-in cubic maps output time to source time with acceleration.
        # Starts very slow (dreamy slow-mo), ends near real-time speed so the
        # hard cut to the clip doesn't "jump" from slow to fast.
        n_src = len(self._source_frames)
        progress = self._output_index / max(1, self._total_output_frames - 1)
        eased = progress * progress * progress  # cubic ease-in
        src_pos = eased * (n_src - 1)

        idx = min(int(src_pos), n_src - 2)
        t = src_pos - idx  # fractional position [0, 1)

        # WHY: Catmull-Rom cubic interpolation uses 4 frames instead of 2.
        # This eliminates the "pop" at frame pair boundaries that linear
        # interpolation creates (C1 continuity vs C0).
        p0 = self._source_frames[max(0, idx - 1)]
        p1 = self._source_frames[idx]
        p2 = self._source_frames[min(idx + 1, n_src - 1)]
        p3 = self._source_frames[min(idx + 2, n_src - 1)]

        frame = 0.5 * (
            2.0 * p1
            + (-p0 + p2) * t
            + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (t * t)
            + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (t * t * t)
        )
        np.clip(frame, 0.0, 1.0, out=frame)

        self._output_index += 1
        return frame

    def close(self) -> None:
        self._source_frames.clear()

    def __del__(self) -> None:
        self.close()
