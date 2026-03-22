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
    """Reads slow-motion blurred frames from an FFmpeg pipe.

    FFmpeg extracts source_seconds of video, slows it down to fill
    the title duration, scales, blurs, and darkens — all in one pipeline.
    Frames are read one at a time via the pipe (constant memory usage).
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
        self.width = width
        self.height = height
        self.hdr = False
        self.bytes_per_pixel = 3
        self.frame_size = width * height * 3
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._last_frame: np.ndarray | None = None

        if not shutil.which("ffmpeg"):
            return

        clip_duration = _probe_duration(clip_path)
        # WHY: clamp source_seconds to available duration
        actual_source = min(source_seconds, clip_duration * 0.8)
        if actual_source < 0.1:
            return

        slowdown = title_duration / actual_source

        # WHY: FFmpeg ONLY handles slow-mo + frame interpolation here.
        # Blur and darken are done per-frame in the Taichi renderer, which:
        #   1. Preserves HDR (no 8-bit format conversion needed)
        #   2. Enables animated deblur (blur ramps heavy→zero)
        #   3. Enables animated darken (dark→normal)
        # minterpolate with blend mode generates smooth intermediate frames.
        # WHY: rgb48le for HDR preserves full 10-bit+ precision (no SDR truncation).
        # The earlier scanline artifacts were from minterpolate (now removed),
        # not from rgb48le byte reading.
        pix_fmt = "rgb48le" if hdr else "rgb24"
        self.bytes_per_pixel = 6 if hdr else 3
        self.frame_size = width * height * self.bytes_per_pixel
        self.hdr = hdr

        # WHY: minterpolate is 8-bit only (silently downgrades HDR).
        # tmix preserves rgb48le natively, is slice-threaded (fast),
        # and creates smooth temporal averaging across duplicated frames.
        # Ghosting from tmix is invisible under heavy Taichi blur.
        # Gaussian-weighted center keeps current frame dominant.
        tmix_n = max(3, int(slowdown))
        weights = " ".join(str(i + 1) for i in range(tmix_n // 2 + 1))
        weights += " " + " ".join(str(tmix_n // 2 - i) for i in range(tmix_n // 2))
        vf = f"setpts={slowdown}*PTS,fps={fps},tmix=frames={tmix_n}:weights='{weights}'"

        cmd = [
            "ffmpeg",
            "-ss", "0",
            "-t", str(actual_source),
            "-i", str(clip_path),
            "-vf", vf,
            "-r", str(fps),
            "-t", str(title_duration),
            "-f", "rawvideo",
            "-pix_fmt", pix_fmt,
            "-an",
            "pipe:1",
        ]  # fmt: skip

        try:
            self._process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            logger.debug(f"Failed to start slowmo pipe for {clip_path}", exc_info=True)

    @property
    def is_active(self) -> bool:
        return self._process is not None and self._process.stdout is not None

    def read_frame(self) -> np.ndarray | None:
        """Read the next frame from the pipe. Returns float32 (H, W, 3) [0, 1].

        If the pipe runs out of frames, returns the last good frame instead
        of None — this prevents fallback to the dark gradient background.
        """
        if not self.is_active:
            return self._last_frame

        assert self._process is not None and self._process.stdout is not None

        data = self._process.stdout.read(self.frame_size)
        if len(data) < self.frame_size:
            return self._last_frame

        if self.hdr:
            raw = np.frombuffer(data, dtype=np.uint16)
            frame = raw.reshape((self.height, self.width, 3)).astype(np.float32) / 65535.0
        else:
            raw = np.frombuffer(data, dtype=np.uint8)
            frame = raw.reshape((self.height, self.width, 3)).astype(np.float32) / 255.0

        self._last_frame = frame
        return frame

    def close(self) -> None:
        if self._process is not None:
            self._process.stdout.close() if self._process.stdout else None
            self._process.stderr.close() if self._process.stderr else None
            self._process.wait()
            self._process = None

    def __del__(self) -> None:
        self.close()
