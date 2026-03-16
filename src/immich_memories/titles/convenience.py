"""Convenience functions for quick title screen generation.

These standalone functions provide a simple API for generating title screens,
month dividers, and ending screens without needing to manually create a
TitleScreenConfig or TitleScreenGenerator instance.

Usage:
    ```python
    from immich_memories.titles.convenience import (
        generate_title_screen,
        generate_month_divider,
        generate_ending_screen,
    )

    # Quick title screen
    path = generate_title_screen(
        title="2024",
        subtitle="Family Memories",
        orientation="landscape",
        resolution="1080p",
    )
    ```
"""

from __future__ import annotations

from pathlib import Path

from .encoding import get_resolution_for_orientation
from .styles import TitleStyle, get_random_style
from .text_builder import generate_month_divider_text
from .video_encoding import create_title_video


def generate_title_screen(
    title: str,
    subtitle: str | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 3.5,
    fps: float = 60.0,
    animated_background: bool = True,
) -> Path:
    """Generate a title screen video.

    Convenience function for simple title screen generation.

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        style: Visual style (random if not provided).
        output_path: Output path (auto-generated if not provided).
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).
        animated_background: Enable animated background effects.

    Returns:
        Path to generated video file.
    """
    if style is None:
        style = get_random_style()

    if output_path is None:
        output_path = Path.cwd() / "title_screen.mp4"

    width, height = get_resolution_for_orientation(orientation, resolution)

    return create_title_video(
        title=title,
        subtitle=subtitle,
        style=style,
        output_path=output_path,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        animated_background=animated_background,
    )


def generate_month_divider(
    month: int,
    year: int | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    locale: str = "en",
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 2.0,
    fps: float = 60.0,
    animated_background: bool = True,
) -> Path:
    """Generate a month divider video.

    Args:
        month: Month number (1-12).
        year: Optional year.
        style: Visual style.
        output_path: Output path.
        locale: Language code.
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).
        animated_background: Enable animated background effects.

    Returns:
        Path to generated video file.
    """
    if style is None:
        style = get_random_style()

    month_text = generate_month_divider_text(month, year, locale)

    if output_path is None:
        output_path = Path.cwd() / f"month_divider_{month:02d}.mp4"

    # Use simpler style for dividers
    divider_style = TitleStyle(
        name=f"{style.name}_divider",
        font_family=style.font_family,
        font_weight="light",
        title_size_ratio=0.08,
        text_color=style.text_color,
        background_type=style.background_type,
        background_colors=style.background_colors,
        animation_preset="slow_fade",
        use_line_accent=False,
    )

    width, height = get_resolution_for_orientation(orientation, resolution)

    return create_title_video(
        title=month_text,
        subtitle=None,
        style=divider_style,
        output_path=output_path,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        animated_background=animated_background,
    )


def generate_ending_screen(
    video_clips: list[Path] | None = None,
    _dominant_color: tuple[int, int, int] | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 4.0,
    fps: float = 60.0,
) -> Path:
    """Generate an ending screen with fade to white.

    Args:
        video_clips: Unused, kept for API compatibility.
        _dominant_color: Unused, always fades to white.
        style: Visual style for background.
        output_path: Output path.
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).

    Returns:
        Path to generated video file.
    """
    # Import here to avoid circular imports
    from .generator import TitleScreenConfig, TitleScreenGenerator

    if style is None:
        style = get_random_style()

    if output_path is None:
        output_path = Path.cwd() / "ending_screen.mp4"

    config = TitleScreenConfig(
        ending_duration=duration,
        orientation=orientation,
        resolution=resolution,
        fps=fps,
    )

    return TitleScreenGenerator(config=config, style=style).generate_ending_screen().path
