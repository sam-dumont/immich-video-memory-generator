"""Text rendering mixin for PIL-based title renderer.

Handles text compositing, blend modes, color analysis, and decorative elements.
Split from renderer_pil.py to keep files under 500 lines.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

try:
    from PIL import Image, ImageDraw, ImageFilter

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger(__name__)


class TextRenderingMixin:
    """Mixin providing text rendering, blend modes, and color helpers.

    Must be mixed into a class that has:
    - self.style: TitleStyle
    - self.settings: RenderSettings
    - self._get_font(size) -> font
    - self._get_text_metrics(text, font) -> TextMetrics
    """

    def _render_text_element(
        self,
        frame: Image.Image,
        text: str,
        font,
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
        title_font,
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
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore

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
            text_color = "#2D2D2D"  # Dark gray (not black)
            blend_mode = "multiply"
            brightness_category = "very light"
        elif avg_luminance > 0.5:
            # Light-medium background: use dark text with soft_light
            text_color = "#3D3D3D"  # Medium-dark gray
            blend_mode = "soft_light"
            brightness_category = "light-medium"
        elif avg_luminance > 0.3:
            # Medium-dark background: use light text with overlay
            text_color = "#F5F5F5"  # Off-white
            blend_mode = "overlay"
            brightness_category = "medium-dark"
        else:
            # Dark background: use white text with screen blend
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
