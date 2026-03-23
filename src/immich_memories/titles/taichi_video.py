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


def _apply_fade_from_white(
    frame: np.ndarray,
    frame_num: int,
    fade_in_frames: int,
    white_val: int,
    blend_buffer: np.ndarray | None,
) -> np.ndarray:
    """Apply fade-from-white effect to a frame."""
    if blend_buffer is not None and frame_num < fade_in_frames:
        alpha = 1.0 - (1.0 - frame_num / fade_in_frames) ** 2
        np.multiply(white_val * (1 - alpha), 1.0, out=blend_buffer, casting="unsafe")
        np.add(blend_buffer, frame * alpha, out=blend_buffer, casting="unsafe")
        return blend_buffer
    return frame


def _apply_fade_to_white(
    frame: np.ndarray,
    frame_num: int,
    fade_out_start: int,
    fade_out_frames: int,
    white_val: int,
    blend_buffer: np.ndarray | None,
) -> np.ndarray:
    """Apply fade-to-white effect at the end of a video."""
    if blend_buffer is not None and fade_out_frames > 0 and frame_num >= fade_out_start:
        t = (frame_num - fade_out_start) / max(1, fade_out_frames)
        alpha = t * t  # quadratic ease-in
        np.multiply(white_val * alpha, 1.0, out=blend_buffer, casting="unsafe")
        np.add(blend_buffer, frame * (1 - alpha), out=blend_buffer, casting="unsafe")
        return blend_buffer
    return frame


def create_title_video_taichi(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: TaichiTitleConfig | None = None,
    fade_from_white: bool = False,
    fade_to_white: bool = False,
    hdr: bool = True,
) -> Path:
    """Create title video using Taichi GPU rendering."""
    cfg = config or TaichiTitleConfig()
    cfg.hdr = hdr
    output_path.parent.mkdir(parents=True, exist_ok=True)

    renderer = TaichiTitleRenderer(cfg)

    encoder_args = _get_gpu_encoder_args(hdr=hdr)

    # WHY: rgb48le (16-bit) for HDR preserves full 10-bit+ precision.
    # rgb24 (8-bit) for SDR. No zscale conversion needed — data is
    # already in the correct color space from the source clip.
    pix_fmt = "rgb48le" if hdr else "rgb24"

    # WHY: rawvideo input needs explicit color metadata for HDR —
    # without it, the encoder strips bt2020 tags from the output.
    input_color_args: list[str] = []
    if hdr:
        input_color_args = [
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
        ]

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
        pix_fmt,
        *input_color_args,
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

    fade_in_frames = int(0.8 * cfg.fps) if fade_from_white else 0
    # Fade TO white in last 1.5 seconds (for ending screens)
    fade_out_frames = int(1.5 * cfg.fps) if fade_to_white else 0
    fade_out_start = renderer.total_frames - fade_out_frames

    white_val = 65535 if hdr else 255
    blend_dtype = np.uint16 if hdr else np.uint8
    blend_buffer = (
        np.zeros((cfg.height, cfg.width, 3), dtype=blend_dtype)
        if (fade_from_white or fade_to_white)
        else None
    )

    with contextlib.suppress(BrokenPipeError):
        for frame_num in range(renderer.total_frames):
            frame = renderer.render_frame(frame_num, title, subtitle)
            out = _apply_fade_from_white(frame, frame_num, fade_in_frames, white_val, blend_buffer)
            out = _apply_fade_to_white(
                out, frame_num, fade_out_start, fade_out_frames, white_val, blend_buffer
            )
            process.stdin.write(out.data)  # type: ignore[union-attr]

        process.stdin.close()

    process.wait()
    stderr = process.stderr.read() if process.stderr else b""

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

    logger.info(f"Title generated: {output_path}")
    return output_path
