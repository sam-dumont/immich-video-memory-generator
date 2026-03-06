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
import subprocess
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

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
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


class TitleRenderer:
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
        weight_map = {
            "light": "Light",
            "regular": "Regular",
            "medium": "Medium",
            "semibold": "SemiBold",
        }
        weight = weight_map.get(self.style.font_weight, "Regular")

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
        draw = ImageDraw.Draw(img)

        bbox = draw.textbbox((0, 0), text, font=font)
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
        subtitle_anim = compute_staggered_animation(
            preset,
            frame_number,
            self.settings.fps,
            element_index=0,  # Same as title so they animate together
            start_frame=0,
        ) if subtitle else {}

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

    def _render_text_element(
        self,
        frame: Image.Image,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        animation: dict[str, float],
        is_title: bool = True,
        has_subtitle: bool = False,
    ) -> Image.Image:
        """Render a text element onto the frame.

        Args:
            frame: Base frame to draw on.
            text: Text to render.
            font: Font to use.
            animation: Animation values.
            is_title: True if rendering title, False for subtitle.
            has_subtitle: True if there's a subtitle (affects vertical positioning).

        Returns:
            Frame with text rendered.
        """
        # Get opacity from animation
        opacity = animation.get("opacity", 1.0)

        if opacity <= 0:
            return frame

        # Safe margins: 10% on each side (80% usable width)
        safe_margin_percent = 0.10
        safe_margin_x = int(self.settings.width * safe_margin_percent)
        max_text_width = self.settings.width - (2 * safe_margin_x)

        # Get text metrics
        metrics = self._get_text_metrics(text, font)

        # Scale down font if text exceeds safe area
        current_font = font
        while metrics.width > max_text_width and current_font.size > 20:
            # Reduce font size by 5% until it fits
            new_size = int(current_font.size * 0.95)
            current_font = self._get_font(new_size)
            metrics = self._get_text_metrics(text, current_font)
        font = current_font

        # Calculate position (centered within safe area)
        x = (self.settings.width - metrics.width) // 2
        y = self._calculate_y_position(metrics.height, is_title, has_subtitle)

        # Apply animation offsets
        y += int(animation.get("y_offset", 0))
        x += int(animation.get("x_offset", 0))

        # Apply scale
        scale = animation.get("scale", 1.0)
        if scale != 1.0:
            # Scale requires re-rendering at different size
            scaled_font = self._get_font(int(font.size * scale))
            font = scaled_font
            metrics = self._get_text_metrics(text, font)
            # Re-check safe margins after scaling
            while metrics.width > max_text_width and font.size > 20:
                new_size = int(font.size * 0.95)
                font = self._get_font(new_size)
                metrics = self._get_text_metrics(text, font)
            x = (self.settings.width - metrics.width) // 2
            y = self._calculate_y_position(metrics.height, is_title, has_subtitle)

        # Apply blur
        blur = animation.get("blur", 0)

        # Create text layer for compositing
        text_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer)

        # Auto-detect optimal text color and blend mode based on background
        # This ensures we NEVER use pure black and always have good contrast
        optimal_text_color, optimal_blend_mode = self._get_optimal_text_settings()
        text_color = self._parse_color_with_alpha(optimal_text_color, opacity)

        # Draw text shadow if enabled (soft shadow for depth and contrast)
        # Note: with smart blend modes, shadows are usually not needed
        if self.style.text_shadow and opacity > 0.3:
            shadow_offset = max(2, int(font.size * 0.03))
            # Stronger shadow for better contrast (alpha 80 instead of 30)
            shadow_color = (0, 0, 0, int(80 * opacity))
            draw.text(
                (x + shadow_offset, y + shadow_offset),
                text,
                font=font,
                fill=shadow_color,
            )

        # Draw main text
        draw.text((x, y), text, font=font, fill=text_color)

        # Apply blur if needed
        if blur > 0:
            text_layer = text_layer.filter(ImageFilter.GaussianBlur(radius=blur))

        # Composite onto frame using auto-detected blend mode
        frame = frame.convert("RGBA")
        frame = self._blend_layers(frame, text_layer, optimal_blend_mode)
        frame = frame.convert("RGB")

        return frame

    def _blend_layers(
        self,
        base: Image.Image,
        top: Image.Image,
        mode: str,
    ) -> Image.Image:
        """Blend two layers using specified blend mode.

        Implements Photoshop-style blend modes for better text integration
        with backgrounds.

        Args:
            base: Background image (RGBA).
            top: Text layer with alpha (RGBA).
            mode: Blend mode (normal, multiply, overlay, soft_light).

        Returns:
            Blended image.
        """
        import numpy as np

        if mode == "normal":
            return Image.alpha_composite(base, top)

        # Get the alpha mask from the text layer
        top_alpha = top.split()[3]

        # Convert to numpy for blend calculations
        base_np = np.array(base, dtype=np.float32) / 255.0
        top_np = np.array(top, dtype=np.float32) / 255.0
        alpha_np = np.array(top_alpha, dtype=np.float32) / 255.0
        alpha_np = alpha_np[:, :, np.newaxis]

        if mode == "multiply":
            # Multiply: result = base * top (darkens)
            blended = base_np[:, :, :3] * top_np[:, :, :3]

        elif mode == "screen":
            # Screen: result = 1 - (1 - base) * (1 - top) (lightens)
            blended = 1.0 - (1.0 - base_np[:, :, :3]) * (1.0 - top_np[:, :, :3])

        elif mode == "overlay":
            # Overlay: multiply if base < 0.5, screen if base >= 0.5
            base_rgb = base_np[:, :, :3]
            top_rgb = top_np[:, :, :3]
            blended = np.where(
                base_rgb < 0.5,
                2 * base_rgb * top_rgb,
                1 - 2 * (1 - base_rgb) * (1 - top_rgb),
            )

        elif mode == "soft_light":
            # Soft Light: subtle version of overlay
            base_rgb = base_np[:, :, :3]
            top_rgb = top_np[:, :, :3]
            blended = np.where(
                top_rgb < 0.5,
                base_rgb - (1 - 2 * top_rgb) * base_rgb * (1 - base_rgb),
                base_rgb + (2 * top_rgb - 1) * (np.sqrt(base_rgb) - base_rgb),
            )

        else:
            # Unknown mode, fall back to normal
            return Image.alpha_composite(base, top)

        # Apply alpha blending: result = base * (1 - alpha) + blended * alpha
        result_rgb = base_np[:, :, :3] * (1 - alpha_np) + blended * alpha_np
        result_rgb = np.clip(result_rgb, 0, 1)

        # Reconstruct RGBA image
        result = np.zeros_like(base_np)
        result[:, :, :3] = result_rgb
        result[:, :, 3] = base_np[:, :, 3]  # Keep base alpha

        result = (result * 255).astype(np.uint8)
        return Image.fromarray(result, mode="RGBA")

    def _calculate_y_position(
        self,
        text_height: int,
        is_title: bool,
        has_subtitle: bool,
    ) -> int:
        """Calculate vertical position for text.

        Args:
            text_height: Height of the text.
            is_title: True if calculating for title.
            has_subtitle: True if there's a subtitle.

        Returns:
            Y coordinate for text.
        """
        center_y = self.settings.height // 2

        if not has_subtitle:
            # Single text element - center it
            return center_y - text_height // 2

        # With subtitle, offset title up and subtitle down
        spacing = int(self.settings.height * 0.03)

        if is_title:
            return center_y - text_height - spacing // 2
        else:
            return center_y + spacing // 2

    def _render_decorative_line(
        self,
        frame: Image.Image,
        title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        title: str,
        animation: dict[str, float],
    ) -> Image.Image:
        """Render decorative line accent.

        Args:
            frame: Frame to draw on.
            title_font: Font used for title (for positioning).
            title: Title text (for positioning).
            animation: Animation values.

        Returns:
            Frame with line rendered.
        """
        opacity = animation.get("opacity", 1.0)
        if opacity <= 0:
            return frame

        metrics = self._get_text_metrics(title, title_font)
        center_x = self.settings.width // 2
        center_y = self.settings.height // 2

        # Calculate line position
        line_y = center_y - metrics.height - int(self.settings.height * 0.04)
        if self.style.line_position == "below":
            line_y = center_y + int(self.settings.height * 0.08)

        # Animate line width
        line_width = int(self.style.line_width * opacity)
        half_width = line_width // 2

        # Create line layer
        line_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(line_layer)

        # Parse accent color
        accent_rgb = self._parse_color(self.style.accent_color)
        line_color = (*accent_rgb, int(255 * opacity))

        # Draw line
        draw.rectangle(
            [
                center_x - half_width,
                line_y,
                center_x + half_width,
                line_y + self.style.line_thickness,
            ],
            fill=line_color,
        )

        # Draw second line if position is "both"
        if self.style.line_position == "both":
            line_y2 = center_y + int(self.settings.height * 0.08)
            draw.rectangle(
                [
                    center_x - half_width,
                    line_y2,
                    center_x + half_width,
                    line_y2 + self.style.line_thickness,
                ],
                fill=line_color,
            )

        # Composite
        frame = frame.convert("RGBA")
        frame = Image.alpha_composite(frame, line_layer)
        return frame.convert("RGB")

    def _parse_color(self, hex_color: str) -> tuple[int, int, int]:
        """Parse hex color to RGB tuple.

        Args:
            hex_color: Hex color string.

        Returns:
            RGB tuple.
        """
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore

    def _parse_color_with_alpha(
        self,
        hex_color: str,
        alpha: float,
    ) -> tuple[int, int, int, int]:
        """Parse hex color to RGBA tuple.

        Args:
            hex_color: Hex color string.
            alpha: Alpha value (0.0 to 1.0).

        Returns:
            RGBA tuple.
        """
        rgb = self._parse_color(hex_color)
        return (*rgb, int(255 * alpha))

    def _calculate_luminance(self, hex_color: str) -> float:
        """Calculate perceived luminance of a color.

        Uses the standard formula for relative luminance based on
        human perception (green is perceived as brightest).

        Args:
            hex_color: Hex color string.

        Returns:
            Luminance value from 0.0 (black) to 1.0 (white).
        """
        r, g, b = self._parse_color(hex_color)
        # Standard luminance formula (sRGB to relative luminance)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    def _get_optimal_text_settings(self) -> tuple[str, str]:
        """Determine optimal text color and blend mode based on background.

        Analyzes the background colors to automatically select:
        - Text color: Dark gray for light backgrounds, white for dark backgrounds
          (NEVER pure black #000000)
        - Blend mode: multiply for light backgrounds, screen for dark backgrounds

        Results are cached for performance (background doesn't change during rendering).

        Returns:
            Tuple of (text_color_hex, blend_mode).
        """
        # Check cache first
        if hasattr(self, "_cached_text_settings"):
            return self._cached_text_settings

        # Calculate average luminance of background colors
        bg_colors = self.style.background_colors
        if not bg_colors:
            bg_colors = ["#FFFFFF"]  # Default to white

        total_luminance = sum(self._calculate_luminance(c) for c in bg_colors)
        avg_luminance = total_luminance / len(bg_colors)

        # Determine text color and blend mode based on background brightness
        if avg_luminance > 0.7:
            # Very light background: use dark text with multiply blend
            # multiply darkens further, good for light backgrounds
            text_color = "#2D2D2D"  # Dark gray (not black)
            blend_mode = "multiply"
            brightness_category = "very light"
        elif avg_luminance > 0.5:
            # Light-medium background: use dark text with soft_light
            # soft_light is more subtle than multiply
            text_color = "#3D3D3D"  # Medium-dark gray
            blend_mode = "soft_light"
            brightness_category = "light-medium"
        elif avg_luminance > 0.3:
            # Medium-dark background: use light text with overlay
            # overlay adapts to both light and dark areas
            text_color = "#F5F5F5"  # Off-white
            blend_mode = "overlay"
            brightness_category = "medium-dark"
        else:
            # Dark background: use white text with screen blend
            # screen lightens, good for dark backgrounds
            text_color = "#FFFFFF"  # White
            blend_mode = "screen"
            brightness_category = "dark"

        logger.debug(
            f"Auto-detected text settings: bg_luminance={avg_luminance:.2f} "
            f"({brightness_category}) -> text_color={text_color}, blend_mode={blend_mode}"
        )

        # Cache the result
        self._cached_text_settings = (text_color, blend_mode)
        return self._cached_text_settings

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
                # Map frame to 0.0 → 1.0 progress within fade-out phase
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

        # Prepare frame specifications: (frame_index, preset_to_use, frame_number_for_preset)
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


def _get_best_encoder() -> list[str]:
    """Get the best available video encoder for intermediate files.

    Returns encoder flags optimized for:
    - VERY HIGH quality (near-lossless for intermediate files that will be re-encoded)
    - Fast encoding (GPU when available)
    - 10-bit color depth (eliminates gradient banding)
    - Compatible with .mp4 container

    Returns:
        List of FFmpeg encoder arguments.
    """
    import sys

    # HLG colorspace metadata — must match video clips for clean concat
    color_args = [
        "-color_primaries", "bt2020",
        "-color_trc", "arib-std-b67",
        "-colorspace", "bt2020nc",
    ]

    # macOS: Use VideoToolbox hardware encoder (GPU accelerated, 10-bit support)
    if sys.platform == "darwin":
        return [
            "-c:v", "hevc_videotoolbox",
            "-q:v", "50",  # High quality (lower = better, 0-100)
            "-pix_fmt", "p010le",  # 10-bit for smooth gradients
            "-tag:v", "hvc1",  # Better compatibility
            *color_args,
        ]

    # Other platforms: Check for available encoders
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
        )
        encoders = result.stdout

        # Try NVIDIA NVENC (GPU accelerated)
        if "hevc_nvenc" in encoders:
            return [
                "-c:v", "hevc_nvenc",
                "-preset", "p4",  # Quality preset
                "-rc", "constqp", "-qp", "18",
                "-pix_fmt", "p010le",  # 10-bit
                "-tag:v", "hvc1",
                *color_args,
            ]

        # Fallback to libx265 (CPU, slower but high quality)
        if "libx265" in encoders:
            return [
                "-c:v", "libx265",
                "-crf", "18",
                "-preset", "fast",
                "-pix_fmt", "yuv420p10le",
                "-tag:v", "hvc1",
                *color_args,
            ]

        # Last fallback to libx264 (8-bit)
        return [
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
        ]

    except Exception:
        # Default to libx264 if detection fails
        return [
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
        ]


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
    import queue
    import threading

    settings = RenderSettings(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        animated_background=animated_background,
    )
    renderer = TitleRenderer(style, settings)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoder_args = _get_best_encoder()

    # Build FFmpeg command to read raw video from pipe
    cmd = [
        "ffmpeg",
        "-y",
        # Input: raw RGB frames from stdin
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "pipe:0",
        # Add silent audio track (required for crossfades in assembly)
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        *encoder_args,
        # Audio codec for the silent track
        "-c:a", "aac", "-b:a", "128k",
        # Duration to match video
        "-t", str(duration),
        "-movflags", "+faststart",
        str(output_path),
    ]

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
