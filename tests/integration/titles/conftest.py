"""Shared fixtures for title integration tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from tests.integration.conftest import requires_ffmpeg  # noqa: F401

# Small dimensions for fast pixel assertions.
# Videos render at their native resolution (e.g. 720p);
# frames are rescaled to these dimensions on extraction.
TITLE_W, TITLE_H, TITLE_FPS = 320, 180, 10


def extract_frame_rgb(video: Path, frame_num: int, width: int, height: int) -> np.ndarray:
    """Extract a specific frame as RGB24 numpy array via FFmpeg.

    Rescales to width x height so callers can use small dimensions
    for fast numpy ops regardless of the video's native resolution.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{frame_num}),scale={width}:{height}",
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"Frame extraction failed: {stderr[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


def extract_frames_rgb(video: Path, count: int, width: int, height: int) -> list[np.ndarray]:
    """Extract evenly-spaced frames across the video.

    Uses ffprobe to get total frame count, then extracts `count` frames
    at evenly-spaced indices.
    """
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-print_format",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(probe.stdout)
    total = int(data["streams"][0]["nb_read_frames"])

    indices = [int(i * (total - 1) / (count - 1)) for i in range(count)]
    return [extract_frame_rgb(video, idx, width, height) for idx in indices]


def ffprobe_stream(path: Path) -> dict:
    """Get first video stream metadata."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "-select_streams",
            "v:0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    data = json.loads(result.stdout)
    return data["streams"][0]


def has_audio_stream(path: Path) -> bool:
    """Check if video has an audio stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-print_format",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    data = json.loads(result.stdout)
    return len(data.get("streams", [])) > 0


def region_mean(
    frame: np.ndarray, y_start: float, y_end: float, x_start: float = 0.0, x_end: float = 1.0
) -> float:
    """Mean pixel value of a region (coordinates as fractions 0-1)."""
    h, w = frame.shape[:2]
    region = frame[
        int(h * y_start) : int(h * y_end),
        int(w * x_start) : int(w * x_end),
    ]
    return float(region.mean())


def region_std(
    frame: np.ndarray, y_start: float, y_end: float, x_start: float = 0.0, x_end: float = 1.0
) -> float:
    """Std dev of pixel values in a region (coordinates as fractions 0-1)."""
    h, w = frame.shape[:2]
    region = frame[
        int(h * y_start) : int(h * y_end),
        int(w * x_start) : int(w * x_end),
    ]
    return float(region.std())
