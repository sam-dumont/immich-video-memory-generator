"""Video creation using Taichi GPU-rendered title frames.

Pipes rendered frames into FFmpeg to produce the final title video file.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import numpy as np

from .renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

logger = logging.getLogger(__name__)


def create_title_video_taichi(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: TaichiTitleConfig | None = None,
    fade_from_white: bool = False,
) -> Path:
    """Create title video using Taichi GPU rendering."""
    cfg = config or TaichiTitleConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    renderer = TaichiTitleRenderer(cfg)

    # HLG colorspace metadata -- must match video clips for clean concat
    color_args = [
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "arib-std-b67",
        "-colorspace",
        "bt2020nc",
    ]

    # Use 10-bit encoding for smooth gradients (no banding)
    if sys.platform == "darwin":
        video_codec = [
            "-c:v",
            "hevc_videotoolbox",
            "-q:v",
            "50",
            "-tag:v",
            "hvc1",
            *color_args,
        ]
        pix_fmt = "p010le"
    else:
        video_codec = [
            "-c:v",
            "libx265",
            "-crf",
            "18",
            "-preset",
            "fast",
            "-tag:v",
            "hvc1",
            *color_args,
        ]
        pix_fmt = "yuv420p10le"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{cfg.width}x{cfg.height}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(cfg.fps),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        *video_codec,
        "-pix_fmt",
        pix_fmt,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(cfg.duration),
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info(f"Generating title with Taichi: {title}")

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    # Fade FROM white at the start (only for intro title, not month dividers)
    fade_in_frames = int(0.8 * cfg.fps) if fade_from_white else 0
    white_frame = (
        np.full((cfg.height, cfg.width, 3), 255, dtype=np.uint8) if fade_from_white else None
    )

    try:
        for frame_num in range(renderer.total_frames):
            frame = renderer.render_frame(frame_num, title, subtitle)

            if fade_from_white and frame_num < fade_in_frames:
                fade_in_progress = frame_num / fade_in_frames
                blend_alpha = 1.0 - (1.0 - fade_in_progress) ** 2
                assert white_frame is not None
                frame = (white_frame * (1 - blend_alpha) + frame * blend_alpha).astype(np.uint8)

            process.stdin.write(frame.tobytes())

        process.stdin.close()

    except BrokenPipeError:
        pass  # FFmpeg closed pipe early — check returncode below

    process.wait()
    stderr = process.stderr.read() if process.stderr else b""

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

    logger.info(f"Title generated: {output_path}")
    return output_path
