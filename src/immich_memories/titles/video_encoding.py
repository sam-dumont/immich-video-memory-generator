"""Video encoding helpers for title screen generation.

Handles FFmpeg encoder selection and video creation from rendered frames.
Split from renderer_pil.py to keep files under 500 lines.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .animations import get_animation_preset, reverse_preset
from .encoding import _get_gpu_encoder_args
from .styles import TitleStyle

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _get_sdr_to_hlg_filter(hdr: bool = True) -> str:
    """Get video filter to convert sRGB frames to HLG/BT.2020.

    PIL renders sRGB (8-bit RGB24) frames. When outputting HDR, we need
    proper transfer function conversion so titles don't appear pinkish.
    When outputting SDR, no conversion needed.

    Args:
        hdr: If True, return SDR→HLG conversion filter. If False, return "".

    Returns:
        FFmpeg video filter string, or "" for SDR output.
    """
    if not hdr:
        return ""

    # zscale handles proper transfer function conversion (sRGB → HLG)
    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
        )
        if "zscale" in result.stdout:
            return (
                "format=yuv420p,"
                "zscale=tin=bt709:t=arib-std-b67"
                ":pin=bt709:p=bt2020"
                ":min=bt709:m=bt2020nc,"
                "format=p010le"
            )

    # Fallback: just do format conversion (colors slightly off but not pink)
    logger.warning("zscale not available — title screen colors may be slightly inaccurate")
    return "format=p010le"


def _get_best_encoder(hdr: bool = True) -> tuple[list[str], str]:
    """Get the best encoder args and optional SDR→HLG video filter.

    Delegates encoder selection to the shared _get_gpu_encoder_args(),
    then adds the PIL-specific SDR→HLG conversion filter when needed.

    Args:
        hdr: If True, output HLG HDR. If False, output SDR.

    Returns:
        Tuple of (encoder args, video filter for SDR→HLG conversion).
    """
    encoder_args = _get_gpu_encoder_args(hdr=hdr)
    video_filter = _get_sdr_to_hlg_filter(hdr=hdr)
    return encoder_args, video_filter


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
    hdr: bool = True,
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
    renderer = TitleRenderer(style, settings)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoder_args, video_filter = _get_best_encoder(hdr=hdr)

    # Build FFmpeg command to read raw video from pipe
    cmd = [
        "ffmpeg",
        "-y",
        # Input: raw RGB frames from stdin
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
        # Add silent audio track (required for crossfades in assembly)
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
    ]

    # SDR→HLG color conversion (fixes pinkish title screens)
    if video_filter:
        cmd.extend(["-vf", video_filter])

    cmd.extend(
        [
            *encoder_args,
            # Audio codec for the silent track
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            # Duration to match video
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    # Use a queue to pass frames between threads
    frame_queue: queue.Queue = queue.Queue(maxsize=10)  # Buffer up to 10 frames
    write_error: list = []

    def write_frames_to_ffmpeg(process: subprocess.Popen) -> None:
        """Thread function to write frames to FFmpeg stdin."""
        try:
            while True:
                frame_bytes = frame_queue.get()
                if frame_bytes is None:  # Sentinel to stop
                    break
                process.stdin.write(frame_bytes)
            process.stdin.close()
        except Exception as e:
            write_error.append(e)

    # Start FFmpeg process
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Start writer thread
    writer_thread = threading.Thread(target=write_frames_to_ffmpeg, args=(process,))
    writer_thread.start()

    # Render frames and add to queue
    total_frames = int(duration * fps)
    preset = get_animation_preset(style.animation_preset)
    reversed_preset = reverse_preset(preset)

    # Text fade-out animation
    fade_out_duration = 1.0
    fade_out_frames = int(fade_out_duration * fps)
    fade_out_start_frame = total_frames - fade_out_frames
    animation_frames = int(preset.duration_ms / 1000 * fps)

    # Fade FROM white at the start (only for intro title, not month dividers)
    fade_in_frames = int(0.8 * fps) if fade_from_white else 0
    white_frame = Image.new("RGB", (width, height), (255, 255, 255)) if fade_from_white else None

    for i in range(total_frames):
        if i >= fade_out_start_frame:
            # Text fade-out (background stays visible for assembly crossfade)
            fade_out_progress = (i - fade_out_start_frame) / fade_out_frames
            fade_out_frame = int(fade_out_progress * animation_frames)
            frame = renderer.render_frame(title, subtitle, fade_out_frame, reversed_preset)
        elif fade_from_white and i < fade_in_frames:
            # Fade-in phase: blend FROM white to frame (intro title only)
            frame = renderer.render_frame(title, subtitle, i, preset)
            fade_in_progress = i / fade_in_frames
            blend_alpha = 1.0 - (1.0 - fade_in_progress) ** 2  # Ease out curve
            frame = Image.blend(white_frame, frame, blend_alpha)
        else:
            # Normal rendering
            frame = renderer.render_frame(title, subtitle, i, preset)

        # Convert PIL Image to raw RGB bytes and add to queue
        frame_bytes = frame.tobytes()
        frame_queue.put(frame_bytes)

    # Signal end of frames
    frame_queue.put(None)
    writer_thread.join()

    # Wait for FFmpeg to finish (stdin already closed by writer thread)
    # Read stderr directly instead of using communicate()
    stderr = process.stderr.read() if process.stderr else b""
    process.wait()

    if write_error:
        raise RuntimeError(f"Write error: {write_error[0]}")

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")

    return output_path
