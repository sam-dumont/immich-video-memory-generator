"""Video encoding helpers for title screen generation.

Handles FFmpeg encoder selection and video creation from rendered frames.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.encoding_plan import EncodingPlan

from .animations import get_animation_preset, reverse_preset
from .encoding import standalone_title_encoding_plan, title_color_filter, title_encoder_args
from .styles import TitleStyle

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _get_best_encoder(
    encoding_plan: EncodingPlan | None = None,
) -> tuple[list[str], str]:
    """Return plan-derived args and the PIL-specific color conversion."""
    plan = encoding_plan or standalone_title_encoding_plan()
    encoder_args = title_encoder_args(plan)
    video_filter = title_color_filter(plan)
    return encoder_args, video_filter


def _build_ffmpeg_cmd(
    width: int,
    height: int,
    fps: float,
    duration: float,
    encoder_args: list[str],
    video_filter: str,
    output_path: Path,
) -> list[str]:
    """Build the FFmpeg command for piped raw-video input."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
    ]
    if video_filter:
        cmd.extend(["-vf", video_filter])
    cmd.extend(
        [
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
    )
    return cmd


def _render_frame_with_animation(
    renderer,
    title: str,
    subtitle: str | None,
    frame_idx: int,
    *,
    preset,
    reversed_preset,
    fade_out_start_frame: int,
    fade_out_frames: int,
    animation_frames: int,
    fade_from_white: bool,
    fade_in_frames: int,
    white_frame,
):
    """Render a single frame applying fade-in/fade-out animation logic."""
    if frame_idx >= fade_out_start_frame:
        fade_out_progress = (frame_idx - fade_out_start_frame) / fade_out_frames
        fade_out_frame = int(fade_out_progress * animation_frames)
        return renderer.render_frame(title, subtitle, fade_out_frame, reversed_preset)

    if fade_from_white and frame_idx < fade_in_frames:
        frame = renderer.render_frame(title, subtitle, frame_idx, preset)
        fade_in_progress = frame_idx / fade_in_frames
        blend_alpha = 1.0 - (1.0 - fade_in_progress) ** 2
        return Image.blend(white_frame, frame, blend_alpha)

    return renderer.render_frame(title, subtitle, frame_idx, preset)


def create_title_video(
    title: str,
    subtitle: str | None,
    style: TitleStyle,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    duration: float = 3.5,
    fps: float = 60.0,  # 60fps for smooth animations (downsample later if needed)
    animated_background: bool = True,
    fade_from_white: bool = False,
    background_image: np.ndarray | None = None,
    encoding_plan: EncodingPlan | None = None,
) -> Path:
    """Create a complete title video with full animation support.

    Renders frames and pipes them directly to FFmpeg (no disk I/O for frames).
    This is significantly faster than saving PNG files to disk.

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        style: Visual style.
        output_path: Output video file path.
        width: Video width.
        height: Video height.
        duration: Video duration in seconds.
        fps: Frames per second.
        animated_background: Enable animated background effects.
        fade_from_white: If True, fade from white at the start (for intro title only).

    Returns:
        Path to created video file.
    """
    from .renderer_pil import RenderSettings, TitleRenderer

    settings = RenderSettings(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        animated_background=animated_background,
    )
    renderer = TitleRenderer(style, settings, background_image=background_image)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoder_args, video_filter = _get_best_encoder(encoding_plan)
    cmd = _build_ffmpeg_cmd(width, height, fps, duration, encoder_args, video_filter, output_path)

    # Use a queue to pass frames between threads
    frame_queue: queue.Queue = queue.Queue(maxsize=10)
    write_error: list = []

    def write_frames_to_ffmpeg(process: subprocess.Popen) -> None:
        """Thread function to write frames to FFmpeg stdin."""
        try:
            while True:
                frame_bytes = frame_queue.get()
                if frame_bytes is None:
                    break
                process.stdin.write(frame_bytes)
            process.stdin.close()
        except OSError as e:
            write_error.append(e)

    process = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    writer_thread = threading.Thread(target=write_frames_to_ffmpeg, args=(process,))
    writer_thread.start()

    # Animation parameters
    total_frames = int(duration * fps)
    preset = get_animation_preset(style.animation_preset)
    reversed_preset = reverse_preset(preset)
    fade_out_frames = int(1.0 * fps)
    fade_out_start_frame = total_frames - fade_out_frames
    animation_frames = int(preset.duration_ms / 1000 * fps)
    fade_in_frames = int(0.8 * fps) if fade_from_white else 0
    white_frame = Image.new("RGB", (width, height), (255, 255, 255)) if fade_from_white else None

    for i in range(total_frames):
        frame = _render_frame_with_animation(
            renderer,
            title,
            subtitle,
            i,
            preset=preset,
            reversed_preset=reversed_preset,
            fade_out_start_frame=fade_out_start_frame,
            fade_out_frames=fade_out_frames,
            animation_frames=animation_frames,
            fade_from_white=fade_from_white,
            fade_in_frames=fade_in_frames,
            white_frame=white_frame,
        )
        frame_queue.put(frame.tobytes())

    frame_queue.put(None)
    writer_thread.join()

    stderr = process.stderr.read() if process.stderr else b""
    process.wait()

    if write_error:
        raise RuntimeError(f"Write error: {write_error[0]}")
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")

    return output_path
