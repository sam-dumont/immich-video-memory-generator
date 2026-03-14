"""Animated globe video creation.

Renders per-frame globe projections with interpolated camera,
piped to FFmpeg as HLG HDR video. Used for trip map intro screens.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from .encoding import _get_gpu_encoder_args
from .globe_renderer import GlobeCameraKeyframe, interpolate_camera
from .taichi_globe import project_globe_frame

logger = logging.getLogger(__name__)


def create_globe_animation_video(
    texture: np.ndarray,
    keyframes: list[GlobeCameraKeyframe],
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    duration: float = 5.0,
    fps: float = 30.0,
    fov: float = 0.8,
    hold_start: float = 0.5,
    hold_end: float = 1.0,
    hdr: bool = True,
) -> Path:
    """Create an animated globe fly-over video.

    Renders each frame by projecting the equirectangular texture onto
    a 3D sphere with interpolated camera position, then pipes raw
    frames to FFmpeg with HLG HDR metadata.

    Args:
        texture: Equirectangular map texture, float32 (h, w, 3).
        keyframes: Camera path keyframes.
        output_path: Where to write the .mp4.
        width: Output video width.
        height: Output video height.
        duration: Total video duration in seconds.
        fps: Frames per second.
        fov: Field of view multiplier.
        hold_start: Seconds to hold on the starting position.
        hold_end: Seconds to hold on the ending position.

    Returns:
        Path to the generated video file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = int(duration * fps)

    cmd = _build_ffmpeg_command(width, height, fps, duration, output_path, hdr=hdr)
    logger.info(f"Rendering globe animation: {total_frames} frames, {duration}s")

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    frame_buffer = np.zeros((height, width, 3), dtype=np.float32)

    # Time mapping: hold → animate → hold
    animate_start = hold_start / duration
    animate_end = 1.0 - (hold_end / duration)

    try:
        for frame_num in range(total_frames):
            progress = frame_num / max(1, total_frames - 1)

            # Map frame time to camera animation time
            if progress <= animate_start:
                cam_t = 0.0
            elif progress >= animate_end:
                cam_t = 1.0
            else:
                cam_t = (progress - animate_start) / (animate_end - animate_start)

            lat, lon, distance = interpolate_camera(keyframes, cam_t)
            frame_buffer[:] = 0.0
            project_globe_frame(frame_buffer, texture, lat, lon, distance, width, height, fov)

            # Convert float32 [0,1] to uint8 [0,255]
            rgb8 = (np.clip(frame_buffer, 0, 1) * 255).astype(np.uint8)
            process.stdin.write(rgb8.tobytes())

        process.stdin.close()

    except BrokenPipeError:
        pass  # FFmpeg closed pipe early — check returncode below

    process.wait()
    stderr = process.stderr.read() if process.stderr else b""

    if process.returncode != 0:
        raise RuntimeError(f"Globe FFmpeg failed: {stderr.decode()[-500:]}")

    logger.info(f"Globe animation generated: {output_path}")
    return output_path


def _build_ffmpeg_command(
    width: int,
    height: int,
    fps: float,
    duration: float,
    output_path: Path,
    hdr: bool = True,
) -> list[str]:
    """Build FFmpeg command for globe video encoding.

    Uses the shared encoder from encoding.py (single source of truth).
    """
    encoder_args = _get_gpu_encoder_args(hdr=hdr)

    return [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{width}x{height}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        *encoder_args,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(duration),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
