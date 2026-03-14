"""FFmpeg progress parsing and process runner.

This module provides the FFmpegProgress and AssemblyContext dataclasses,
along with functions for parsing FFmpeg progress output and running
FFmpeg commands with real-time progress reporting.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import IO

logger = logging.getLogger(__name__)

__all__ = [
    "FFmpegProgress",
    "AssemblyContext",
    "_parse_ffmpeg_time",
    "_parse_ffmpeg_progress",
    "_run_ffmpeg_with_progress",
]


@dataclass
class FFmpegProgress:
    """Progress information from FFmpeg encoding."""

    frame: int = 0
    fps: float = 0.0
    time_seconds: float = 0.0
    speed: float = 0.0
    percent: float = 0.0
    eta_seconds: float | None = None

    def __str__(self) -> str:
        eta_str = f"{self.eta_seconds:.0f}s" if self.eta_seconds else "calculating..."
        return f"{self.percent:.1f}% @ {self.speed:.1f}x speed, ETA: {eta_str}"


@dataclass
class AssemblyContext:
    """Bundle of resolved assembly parameters shared across assembly methods.

    Centralizes resolution, HDR, pixel format, and colorspace decisions
    that are duplicated across all assembly functions.
    """

    target_w: int
    target_h: int
    pix_fmt: str
    hdr_type: str
    clip_hdr_types: list[str | None]
    clip_primaries: list[str | None]
    colorspace_filter: str
    target_fps: int = 60
    fade_duration: float = 0.5


def _parse_ffmpeg_time(time_str: str) -> float:
    """Parse FFmpeg time string (HH:MM:SS.ms) to seconds."""
    try:
        # Handle negative times
        if time_str.startswith("-"):
            return 0.0
        parts = time_str.split(":")
        if len(parts) == 3:
            hours, mins, secs = parts
            return float(hours) * 3600 + float(mins) * 60 + float(secs)
        elif len(parts) == 2:
            mins, secs = parts
            return float(mins) * 60 + float(secs)
        else:
            return float(time_str)
    except (ValueError, IndexError):
        return 0.0


def _parse_ffmpeg_progress(line: str, total_duration: float) -> FFmpegProgress | None:
    """Parse an FFmpeg progress line and return progress info.

    FFmpeg outputs lines like:
    frame=  123 fps= 45 q=28.0 size=    1234kB time=00:00:05.12 bitrate= 123.4kbits/s speed=1.23x
    """
    progress = FFmpegProgress()

    # Extract frame
    frame_match = re.search(r"frame=\s*(\d+)", line)
    if frame_match:
        progress.frame = int(frame_match.group(1))

    # Extract fps
    fps_match = re.search(r"fps=\s*([\d.]+)", line)
    if fps_match:
        progress.fps = float(fps_match.group(1))

    # Extract time (most important for progress)
    time_match = re.search(r"time=\s*([\d:.N/A-]+)", line)
    if time_match:
        time_str = time_match.group(1)
        if time_str != "N/A":
            progress.time_seconds = _parse_ffmpeg_time(time_str)

    # Extract speed
    speed_match = re.search(r"speed=\s*([\d.]+)x", line)
    if speed_match:
        progress.speed = float(speed_match.group(1))

    # Calculate percentage and ETA
    if total_duration > 0 and progress.time_seconds >= 0:
        progress.percent = min(100.0, (progress.time_seconds / total_duration) * 100)

        if progress.speed > 0:
            remaining_time = total_duration - progress.time_seconds
            progress.eta_seconds = remaining_time / progress.speed

    # Only return if we got meaningful data
    if progress.time_seconds > 0 or progress.frame > 0:
        return progress
    return None


def _find_buffer_split_pos(buffer: str) -> int:
    """Find the earliest line break position in buffer."""
    cr_pos = buffer.find("\r")
    lf_pos = buffer.find("\n")
    if cr_pos == -1:
        return lf_pos
    if lf_pos == -1:
        return cr_pos
    return min(cr_pos, lf_pos)


def _build_progress_status(progress: FFmpegProgress) -> str:
    """Build status string from FFmpeg progress."""
    time_str = f"{int(progress.time_seconds // 60)}:{int(progress.time_seconds % 60):02d}"
    status = f"Encoding ({time_str})"
    if progress.speed > 0:
        status += f" @ {progress.speed:.1f}x"
    return status


def _try_report_line(
    line: str,
    total_duration: float,
    progress_callback: Callable[[float, str], None],
    last_progress_time: list[float],
) -> None:
    """Report progress for a single line if throttle interval has elapsed."""
    now = time.time()
    if now - last_progress_time[0] < 0.5:
        return
    progress = _parse_ffmpeg_progress(line, total_duration)
    if not progress:
        return
    last_progress_time[0] = now
    status = _build_progress_status(progress)
    try:
        progress_callback(progress.percent, status)
    except (OSError, RuntimeError):
        logger.debug("Progress callback failed", exc_info=True)


def _process_buffer(
    buffer: str,
    stderr_lines: list[str],
    total_duration: float,
    progress_callback: Callable[[float, str], None] | None,
    last_progress_time: list[float],
) -> str:
    """Extract complete lines from buffer, collect them and report progress."""
    while "\r" in buffer or "\n" in buffer:
        split_pos = _find_buffer_split_pos(buffer)
        line = buffer[:split_pos].strip()
        buffer = buffer[split_pos + 1 :]
        if not line:
            continue
        stderr_lines.append(line)
        if progress_callback:
            _try_report_line(line, total_duration, progress_callback, last_progress_time)
    return buffer


def _drain_stderr_pipe(
    pipe: IO[str],
    stderr_lines: list[str],
    total_duration: float,
    progress_callback: Callable[[float, str], None] | None,
) -> None:
    """Read FFmpeg stderr pipe, appending lines and calling progress callback."""
    last_progress_time = [time.time()]  # Mutable container for nonlocal update
    buffer = ""
    while True:
        chunk = pipe.read(4096)
        if not chunk:
            if buffer.strip():
                stderr_lines.append(buffer.strip())
            break
        buffer += chunk
        buffer = _process_buffer(
            buffer, stderr_lines, total_duration, progress_callback, last_progress_time
        )


def _add_streamlit_ctx(thread: Thread) -> None:
    """Attach Streamlit script context to thread if available."""
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

        ctx = get_script_run_ctx()
        if ctx is not None:
            add_script_run_ctx(thread, ctx)
    except ImportError:
        pass


def _run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    progress_callback: Callable[[float, str], None] | None = None,
) -> subprocess.CompletedProcess:
    """Run FFmpeg command and parse progress output.

    Args:
        cmd: FFmpeg command as list of arguments.
        total_duration: Expected output duration in seconds.
        progress_callback: Callback receiving (percent, status_message).

    Returns:
        CompletedProcess with return code and stderr.
    """
    if "-stats" not in cmd:
        cmd = cmd[:1] + ["-stats"] + cmd[1:]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []

    stderr_thread = Thread(
        target=_drain_stderr_pipe,
        args=(process.stderr, stderr_lines, total_duration, progress_callback),
    )

    _add_streamlit_ctx(stderr_thread)
    stderr_thread.start()

    try:
        process.wait(timeout=3600)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("FFmpeg process timed out after 1 hour")
    finally:
        stderr_thread.join(timeout=10)

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=process.returncode,
        stdout="",
        stderr="\n".join(stderr_lines),
    )
