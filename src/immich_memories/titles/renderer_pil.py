"""PIL-based frame renderer for title screens.

This module renders individual frames using PIL/Pillow for maximum control
over typography and visual effects. Frames are then encoded to video using FFmpeg.

Performance optimizations:
- NumPy vectorization for gradient backgrounds (10-20x speedup)
- Multiprocessing for parallel frame rendering (4-8x speedup)
- High-quality intermediate encoding for final assembly
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .animations import (
    AnimationPreset,
    compute_staggered_animation,
    get_animation_preset,
    reverse_preset,
)
from .backgrounds import create_animated_background, create_background_for_style
from .fonts import get_font_path as get_cached_font_path
from .styles import TitleStyle
from .text_rendering import TextRenderingMixin
from .video_encoding import create_title_video  # noqa: F401 — re-export for backwards compat

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class TextMetrics:
    """Metrics for rendered text."""

    width: int
    height: int
    ascent: int
    descent: int


@dataclass
class RenderSettings:
    """Settings for frame rendering."""

    width: int = 1920
    height: int = 1080
    fps: float = 60.0  # 60fps for smooth animations
    duration: float = 3.5
    animation_duration: float = 0.5
    animated_background: bool = True  # Enable animated backgrounds by default


class TitleRenderer(TextRenderingMixin):
    """Renders title screens using PIL."""

    def __init__(
        self,
        style: TitleStyle,
        settings: RenderSettings | None = None,
        fonts_dir: Path | None = None,
    ):
        """Initialize the renderer.

        Args:
            style: Visual style for rendering.
            settings: Render settings (resolution, fps, etc.).
            fonts_dir: Directory containing font files.
        """
        if not HAS_PIL:
            raise ImportError("PIL/Pillow is required for title rendering")

        self.style = style
        self.settings = settings or RenderSettings()
        self.fonts_dir = fonts_dir or Path(__file__).parent.parent / "fonts"

        # Load fonts
        self._title_font: ImageFont.FreeTypeFont | None = None
        self._subtitle_font: ImageFont.FreeTypeFont | None = None

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Get font at specified size.

        Args:
            size: Font size in pixels.

        Returns:
            PIL font object.
        """
        # First try the persistent font cache
        weight = {
            "light": "Light",
            "regular": "Regular",
            "medium": "Medium",
            "semibold": "SemiBold",
        }.get(self.style.font_weight, "Regular")

        cached_font = get_cached_font_path(self.style.font_family, weight)
        if cached_font and cached_font.exists():
            return ImageFont.truetype(str(cached_font), size)

        # Try the style's font path method (for custom fonts)
        font_path = self.style.get_font_path(self.fonts_dir)
        if font_path and font_path.exists():
            return ImageFont.truetype(str(font_path), size)

        # Try system fonts as fallback
        system_fonts = [
            "/System/Library/Fonts/SFNSDisplay.ttf",  # macOS
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "C:\\Windows\\Fonts\\arial.ttf",  # Windows
        ]

        for sys_font in system_fonts:
            if Path(sys_font).exists():
                return ImageFont.truetype(sys_font, size)

        # Fallback to default
        return ImageFont.load_default()

    def _get_text_metrics(
        self,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> TextMetrics:
        """Get metrics for text rendering.

        Args:
            text: Text to measure.
            font: Font to use.

        Returns:
            TextMetrics with dimensions.
        """
        # Create temporary image for measuring
        img = Image.new("RGB", (1, 1))
        bbox = ImageDraw.Draw(img).textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        return TextMetrics(
            width=width,
            height=height,
            ascent=abs(bbox[1]),
            descent=abs(bbox[3] - height),
        )

    def _apply_text_transform(self, text: str) -> str:
        """Apply text transformation based on style.

        Args:
            text: Original text.

        Returns:
            Transformed text.
        """
        if self.style.text_transform == "uppercase":
            return text.upper()
        elif self.style.text_transform == "capitalize":
            return text.title()
        return text

    def render_frame(
        self,
        title: str,
        subtitle: str | None = None,
        frame_number: int = 0,
        animation_preset: AnimationPreset | None = None,
    ) -> Image.Image:
        """Render a single frame of the title screen.

        Args:
            title: Main title text.
            subtitle: Optional subtitle text.
            frame_number: Current frame number (for animation).
            animation_preset: Animation to apply.

        Returns:
            PIL Image of the rendered frame.
        """
        # Create background (animated or static)
        if self.settings.animated_background:
            # Calculate animation progress for background
            total_frames = int(self.settings.duration * self.settings.fps)
            bg_progress = frame_number / max(1, total_frames - 1)
            frame = create_animated_background(
                self.settings.width,
                self.settings.height,
                self.style.background_type,
                self.style.background_colors,
                self.style.background_angle,
                bg_progress,
            )
        else:
            frame = create_background_for_style(
                self.settings.width,
                self.settings.height,
                self.style.background_type,
                self.style.background_colors,
                self.style.background_angle,
            )

        # Apply text transform
        title = self._apply_text_transform(title)
        if subtitle:
            subtitle = self._apply_text_transform(subtitle)

        # Calculate font sizes
        title_size = int(self.settings.height * self.style.title_size_ratio)
        subtitle_size = int(title_size * self.style.subtitle_size_ratio)

        # Load fonts
        title_font = self._get_font(title_size)
        subtitle_font = self._get_font(subtitle_size) if subtitle else None

        # Get animation values
        preset = animation_preset or get_animation_preset(self.style.animation_preset)

        # Calculate animation for title
        title_anim = compute_staggered_animation(
            preset,
            frame_number,
            self.settings.fps,
            element_index=0,
            start_frame=0,
        )

        # Calculate animation for subtitle (same timing as title - no stagger)
        subtitle_anim = (
            compute_staggered_animation(
                preset,
                frame_number,
                self.settings.fps,
                element_index=0,  # Same as title so they animate together
                start_frame=0,
            )
            if subtitle
            else {}
        )

        # Render title
        frame = self._render_text_element(
            frame,
            title,
            title_font,
            title_anim,
            is_title=True,
            has_subtitle=subtitle is not None,
        )

        # Render subtitle
        if subtitle:
            frame = self._render_text_element(
                frame,
                subtitle,
                subtitle_font,
                subtitle_anim,
                is_title=False,
                has_subtitle=True,
            )

        # Render decorative line if enabled
        if self.style.use_line_accent and title_anim.get("opacity", 1) > 0:
            frame = self._render_decorative_line(frame, title_font, title, title_anim)

        return frame

    def render_all_frames(
        self,
        title: str,
        subtitle: str | None = None,
        fade_out_duration: float = 1.0,
    ) -> list[Image.Image]:
        """Render all frames for a title screen with fade-out.

        The title screen has three phases:
        1. Fade-in: Text animates in using the preset animation
        2. Hold: Text remains fully visible
        3. Fade-out: Text animates out using reversed preset animation

        Args:
            title: Main title text.
            subtitle: Optional subtitle.
            fade_out_duration: Duration of fade-out in seconds (default 1.0).

        Returns:
            List of PIL Images for all frames.
        """
        total_frames = int(self.settings.duration * self.settings.fps)
        preset = get_animation_preset(self.style.animation_preset)
        reversed_preset = reverse_preset(preset)

        # Calculate fade-out timing
        fade_out_frames = int(fade_out_duration * self.settings.fps)
        fade_out_start_frame = total_frames - fade_out_frames

        # Calculate animation duration in frames
        animation_frames = int(preset.duration_ms / 1000 * self.settings.fps)

        frames = []
        for i in range(total_frames):
            if i >= fade_out_start_frame:
                # Fade-out phase: use reversed preset
                # Map frame to 0.0 -> 1.0 progress within fade-out phase
                fade_out_progress = (i - fade_out_start_frame) / fade_out_frames
                fade_out_frame = int(fade_out_progress * animation_frames)
                frame = self.render_frame(title, subtitle, fade_out_frame, reversed_preset)
            else:
                # Normal animation (fade-in and hold)
                frame = self.render_frame(title, subtitle, i, preset)
            frames.append(frame)

        return frames

    def render_all_frames_parallel(
        self,
        title: str,
        subtitle: str | None = None,
        fade_out_duration: float = 1.0,
        max_workers: int | None = None,
    ) -> list[Image.Image]:
        """Render all frames in parallel using threading.

        Uses ThreadPoolExecutor to render frames concurrently. NumPy and PIL
        release the GIL during computation, so threads provide real parallelism
        for the vectorized gradient operations.

        Args:
            title: Main title text.
            subtitle: Optional subtitle.
            fade_out_duration: Duration of fade-out in seconds.
            max_workers: Max number of worker threads. Defaults to CPU count.

        Returns:
            List of PIL Images for all frames.
        """
        total_frames = int(self.settings.duration * self.settings.fps)
        preset = get_animation_preset(self.style.animation_preset)
        reversed_preset = reverse_preset(preset)

        # Calculate fade-out timing
        fade_out_frames = int(fade_out_duration * self.settings.fps)
        fade_out_start_frame = total_frames - fade_out_frames
        animation_frames = int(preset.duration_ms / 1000 * self.settings.fps)

        # Prepare frame specifications
        frame_specs = []
        for i in range(total_frames):
            if i >= fade_out_start_frame:
                fade_out_progress = (i - fade_out_start_frame) / fade_out_frames
                fade_out_frame = int(fade_out_progress * animation_frames)
                frame_specs.append((i, reversed_preset, fade_out_frame))
            else:
                frame_specs.append((i, preset, i))

        # Determine worker count
        if max_workers is None:
            max_workers = min(os.cpu_count() or 4, 8)

        # For small frame counts, don't bother with threading
        if total_frames < max_workers * 2:
            return self.render_all_frames(title, subtitle, fade_out_duration)

        # Create worker function with fixed parameters
        def render_single(spec: tuple) -> tuple[int, Image.Image]:
            idx, used_preset, frame_num = spec
            frame = self.render_frame(title, subtitle, frame_num, used_preset)
            return (idx, frame)

        # Render frames in parallel using threads
        frames: list[Image.Image | None] = [None] * total_frames

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(render_single, frame_specs)
            for idx, frame in results:
                frames[idx] = frame

        return frames  # type: ignore


def render_title_frame(
    title: str,
    subtitle: str | None,
    style: TitleStyle,
    width: int,
    height: int,
    animation_progress: float,
) -> np.ndarray:
    """Render a single frame as numpy array.

    Convenience function for quick frame rendering.

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        style: Visual style.
        width: Frame width.
        height: Frame height.
        animation_progress: Animation progress (0.0 to 1.0).

    Returns:
        Numpy array of shape (height, width, 3).
    """
    settings = RenderSettings(width=width, height=height)
    renderer = TitleRenderer(style, settings)

    # Convert progress to frame number
    animation_frames = int(0.5 * settings.fps)
    frame_number = int(animation_progress * animation_frames)

    frame = renderer.render_frame(title, subtitle, frame_number)
    return np.array(frame)
