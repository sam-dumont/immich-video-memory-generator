"""Video creation using Taichi GPU-rendered title frames.

Pipes rendered frames into FFmpeg to produce the final title video file.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path

import numpy as np

from .encoding import _get_gpu_encoder_args
from .renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

logger = logging.getLogger(__name__)


def create_title_video_taichi(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: TaichiTitleConfig | None = None,
    fade_from_white: bool = False,
    hdr: bool = True,
) -> Path:
    """Create title video using Taichi GPU rendering."""
    cfg = config or TaichiTitleConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    renderer = TaichiTitleRenderer(cfg)

    # Encoder args from single source of truth (encoding.py)
    encoder_args = _get_gpu_encoder_args(hdr=hdr)

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
        *encoder_args,
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
    # WHY: reusable blend buffer avoids 50MB of temporaries per fade frame at 4K
    blend_buffer = np.zeros((cfg.height, cfg.width, 3), dtype=np.uint8) if fade_from_white else None

    with contextlib.suppress(BrokenPipeError):  # FFmpeg closed pipe early — check returncode below
        for frame_num in range(renderer.total_frames):
            frame = renderer.render_frame(frame_num, title, subtitle)

            if fade_from_white and frame_num < fade_in_frames:
                fade_in_progress = frame_num / fade_in_frames
                alpha = 1.0 - (1.0 - fade_in_progress) ** 2
                # In-place blend: blend_buffer = white*(1-alpha) + frame*alpha
                assert blend_buffer is not None
                np.multiply(255 * (1 - alpha), 1.0, out=blend_buffer, casting="unsafe")
                np.add(blend_buffer, frame * alpha, out=blend_buffer, casting="unsafe")
                # WHY: write buffer directly — .tobytes() would copy 25MB per frame
                process.stdin.write(memoryview(blend_buffer))
            else:
                process.stdin.write(memoryview(frame))

        process.stdin.close()

    process.wait()
    stderr = process.stderr.read() if process.stderr else b""

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

    logger.info(f"Title generated: {output_path}")
    return output_path
