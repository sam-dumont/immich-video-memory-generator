"""FFmpeg-native title screen renderer.

This module generates title screens using FFmpeg's built-in filters,
which is 50-100x faster than rendering frames in Python.

FFmpeg handles:
- Gradient backgrounds (gradients filter)
- Text rendering (drawtext filter)
- Animations (expression-based)
- All in optimized C with SIMD acceleration
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .encoding import _get_gpu_encoder_args

logger = logging.getLogger(__name__)


def _get_encoder_args(hdr: bool = True) -> list[str]:
    """Get encoder arguments — delegates to shared encoding._get_gpu_encoder_args."""
    return _get_gpu_encoder_args(hdr=hdr)


@dataclass
class FFmpegTitleConfig:
    """Configuration for FFmpeg title generation."""

    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration: float = 3.5

    # Colors (hex without #)
    bg_color1: str = "FFF5E6"
    bg_color2: str = "FFE4CC"
    text_color: str = "2D2D2D"

    # Animation timing
    fade_in_duration: float = 0.6
    fade_out_duration: float = 1.0

    # Text styling
    font: str = "Arial"  # System font
    title_size_ratio: float = 0.10  # Relative to height


def _get_system_font() -> str:
    """Get a reliable system font path."""
    # Common system fonts
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/System/Library/Fonts/SFNSDisplay.ttf",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
        "/usr/share/fonts/TTF/DejaVuSans.ttf",  # Linux alt
        "C:\\Windows\\Fonts\\arial.ttf",  # Windows
    ]

    for path in font_paths:
        if Path(path).exists():
            return path

    # Fallback - let FFmpeg find it
    return "Arial"


def _escape_ffmpeg_text(text: str) -> str:
    """Escape special characters for FFmpeg drawtext filter."""
    # Strip control characters (except space) to prevent filter injection
    text = "".join(c for c in text if c == " " or (ord(c) >= 32 and ord(c) != 127))
    # FFmpeg drawtext special characters (backslash must be first)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("%", "\\%")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace(";", "\\;")
    return text


def create_title_ffmpeg(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: FFmpegTitleConfig | None = None,
) -> Path:
    """Create a title screen using FFmpeg native filters.

    This is 50-100x faster than PIL-based rendering because FFmpeg
    generates everything natively without Python frame loops.

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        output_path: Output video path.
        config: Configuration options.

    Returns:
        Path to generated video.
    """
    cfg = config or FFmpegTitleConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_path = _get_system_font()
    title_escaped = _escape_ffmpeg_text(title)
    subtitle_escaped = _escape_ffmpeg_text(subtitle) if subtitle else None

    # Calculate font sizes
    title_size = int(cfg.height * cfg.title_size_ratio)
    subtitle_size = int(title_size * 0.6)

    # Build animation expressions
    # Fade in from 0 to fade_in_duration, hold, fade out in last fade_out_duration
    fade_out_start = cfg.duration - cfg.fade_out_duration
    alpha_expr = (
        f"if(lt(t,{cfg.fade_in_duration}),t/{cfg.fade_in_duration},"
        f"if(gt(t,{fade_out_start}),({cfg.duration}-t)/{cfg.fade_out_duration},1))"
    )

    # Y position animation (slide up during fade in)
    # Start 30px below, animate to center
    slide_distance = 30
    y_offset_expr = (
        f"if(lt(t,{cfg.fade_in_duration}),{slide_distance}*(1-t/{cfg.fade_in_duration}),0)"
    )

    # Scale animation (subtle zoom from 0.95 to 1.0)
    # We can't directly scale text, but we can animate font size
    # For simplicity, we'll skip dynamic font size and use position animation

    # Calculate vertical positions
    if subtitle:
        # With subtitle: title above center, subtitle below
        spacing = int(cfg.height * 0.03)
        title_y = f"(h/2)-{title_size}-{spacing}+{y_offset_expr}"
        subtitle_y = f"(h/2)+{spacing}+{y_offset_expr}"
    else:
        # Without subtitle: centered
        title_y = f"(h-text_h)/2+{y_offset_expr}"

    # Build drawtext filters
    filters = []

    # Title text
    title_filter = (
        f"drawtext="
        f"text='{title_escaped}':"
        f"fontfile='{font_path}':"
        f"fontsize={title_size}:"
        f"fontcolor={cfg.text_color}:"
        f"x=(w-text_w)/2:"
        f"y={title_y}:"
        f"alpha='{alpha_expr}'"
    )
    filters.append(title_filter)

    # Subtitle text (if present)
    if subtitle_escaped:
        subtitle_filter = (
            f"drawtext="
            f"text='{subtitle_escaped}':"
            f"fontfile='{font_path}':"
            f"fontsize={subtitle_size}:"
            f"fontcolor={cfg.text_color}:"
            f"x=(w-text_w)/2:"
            f"y={subtitle_y}:"
            f"alpha='{alpha_expr}'"
        )
        filters.append(subtitle_filter)

    # Combine filters
    filter_chain = ",".join(filters)

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-y",
        # Animated gradient background
        "-f",
        "lavfi",
        "-i",
        f"gradients=s={cfg.width}x{cfg.height}:c0={cfg.bg_color1}:c1={cfg.bg_color2}:duration={cfg.duration}:speed=1:r={cfg.fps}",
        # Silent audio
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        # Apply text filters
        "-vf",
        filter_chain,
        # Encoding - GPU accelerated with 10-bit for smooth gradients
        *_get_encoder_args(),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(cfg.duration),
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info(f"Generating title with FFmpeg: {title}")
    logger.debug(f"FFmpeg command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error(f"FFmpeg failed: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    logger.info(f"Title generated: {output_path}")
    return output_path


def create_title_with_effects(
    title: str,
    subtitle: str | None,
    output_path: Path,
    config: FFmpegTitleConfig | None = None,
) -> Path:
    """Create a title screen with animated effects using FFmpeg.

    Uses FFmpeg's native filters for fast generation (~5 seconds for 4K).

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        output_path: Output video path.
        config: Configuration options.

    Returns:
        Path to generated video.
    """
    cfg = config or FFmpegTitleConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_path = _get_system_font()
    title_escaped = _escape_ffmpeg_text(title)
    subtitle_escaped = _escape_ffmpeg_text(subtitle) if subtitle else None

    title_size = int(cfg.height * cfg.title_size_ratio)
    subtitle_size = int(title_size * 0.6)

    # Animation timing
    fade_in = cfg.fade_in_duration
    fade_out_start = cfg.duration - cfg.fade_out_duration
    dur = cfg.duration

    # Build filter_complex with proper escaping
    # Use filter_complex instead of -vf for complex graphs
    filters = []

    # Start with gradient input [0:v]
    # Add animated vignette
    filters.append(f"[0:v]vignette=PI/4+PI/4*sin(2*PI*t/{dur})\\:mode=backward[bg]")

    # Title position (centered, with slight upward offset when subtitle present)
    if subtitle:
        spacing = int(cfg.height * 0.03)
        title_y_base = f"(h/2)-{title_size}-{spacing}"
        subtitle_y_base = f"(h/2)+{spacing}"
    else:
        title_y_base = "(h-text_h)/2"

    # Slide animation: start lower, move up
    slide = 40
    # y = base + slide * (1 - min(t/fade_in, 1))
    title_y = f"{title_y_base}+{slide}*(1-min(t/{fade_in}\\,1))"

    # Alpha: fade in, hold, fade out
    # alpha = min(t/fade_in, 1) * (t < fade_out_start ? 1 : (dur-t)/fade_out_dur)
    alpha = (
        f"min(t/{fade_in}\\,1)*if(lt(t\\,{fade_out_start})\\,1\\,({dur}-t)/{cfg.fade_out_duration})"
    )

    # Shadow for title
    shadow = max(2, title_size // 40)
    filters.append(
        f"[bg]drawtext=text='{title_escaped}':"
        f"fontfile='{font_path}':"
        f"fontsize={title_size}:"
        f"fontcolor=black@0.2:"
        f"x=(w-text_w)/2+{shadow}:"
        f"y={title_y}+{shadow}:"
        f"alpha={alpha}[t1]"
    )

    # Main title
    filters.append(
        f"[t1]drawtext=text='{title_escaped}':"
        f"fontfile='{font_path}':"
        f"fontsize={title_size}:"
        f"fontcolor={cfg.text_color}:"
        f"x=(w-text_w)/2:"
        f"y={title_y}:"
        f"alpha={alpha}[t2]"
    )

    last_label = "t2"

    if subtitle_escaped:
        subtitle_y = f"{subtitle_y_base}+{slide}*(1-min(t/{fade_in}\\,1))"

        # Subtitle shadow
        filters.append(
            f"[{last_label}]drawtext=text='{subtitle_escaped}':"
            f"fontfile='{font_path}':"
            f"fontsize={subtitle_size}:"
            f"fontcolor=black@0.15:"
            f"x=(w-text_w)/2+{shadow // 2}:"
            f"y={subtitle_y}+{shadow // 2}:"
            f"alpha={alpha}[s1]"
        )
        last_label = "s1"

        # Main subtitle
        filters.append(
            f"[{last_label}]drawtext=text='{subtitle_escaped}':"
            f"fontfile='{font_path}':"
            f"fontsize={subtitle_size}:"
            f"fontcolor={cfg.text_color}:"
            f"x=(w-text_w)/2:"
            f"y={subtitle_y}:"
            f"alpha={alpha}[out]"
        )
        last_label = "out"
    else:
        # Rename last output to [out]
        filters[-1] = filters[-1].replace("[t2]", "[out]")
        last_label = "out"

    filter_complex = ";".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"gradients=s={cfg.width}x{cfg.height}:c0={cfg.bg_color1}:c1={cfg.bg_color2}:duration={cfg.duration}:speed=1:r={cfg.fps}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{last_label}]",
        "-map",
        "1:a",
        # Encoding - GPU accelerated with 10-bit for smooth gradients
        *_get_encoder_args(),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(cfg.duration),
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info(f"Generating title: {title}")
    logger.debug(f"Filter: {filter_complex}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error(f"FFmpeg stderr: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")

    logger.info(f"Title generated: {output_path}")
    return output_path
