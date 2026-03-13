"""Text rendering mixin for Taichi title renderer.

Provides SDF-based GPU text rendering and PIL-based fallback text rendering
for the TaichiTitleRenderer class.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import taichi_kernels
from .taichi_kernels import (
    SDF_AVAILABLE,
    _get_system_font,
    find_font,
    get_cached_atlas,
    layout_text,
)


def _split_text_for_rendering(draw, text: str, font, max_width: float) -> list[str]:
    """Split text into lines using pixel widths, preferring comma boundaries."""

    def _measure(t: str) -> int:
        bbox = draw.textbbox((0, 0), t, font=font)
        return bbox[2] - bbox[0]

    if _measure(text) <= max_width:
        return [text]

    # Try comma split first
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        parts[0] += ","
        if all(_measure(p) <= max_width for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if _measure(test) > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


def split_title_lines(text: str, max_chars: int) -> list[str]:
    """Split title text into lines, preferring comma boundaries.

    Args:
        text: Title text to split.
        max_chars: Approximate max characters per line.

    Returns:
        List of lines.
    """
    if len(text) <= max_chars:
        return [text]

    # Prefer splitting at comma
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        # Keep comma on first part for visual continuity
        parts[0] += ","
        if all(len(p) <= max_chars for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


logger = logging.getLogger(__name__)


class TaichiTextMixin:
    """Mixin providing text rendering methods for TaichiTitleRenderer."""

    def _init_sdf_atlas(self):
        """Initialize SDF font atlas for GPU text rendering."""
        if not SDF_AVAILABLE or not find_font:
            logger.warning("SDF font support not available")
            self._use_sdf = False
            return

        # Find font file
        font_path = find_font(self.config.font_family)
        if not font_path:
            logger.warning(f"Font '{self.config.font_family}' not found, using fallback")
            font_path = find_font("Helvetica")

        if not font_path:
            logger.warning("No fonts found, falling back to PIL")
            self._use_sdf = False
            return

        # Generate/get cached atlas at a reasonable size for quality
        # Use 128px for atlas to get good quality when scaling
        atlas_size = 128
        self._sdf_atlas = get_cached_atlas(font_path, atlas_size)
        self._sdf_atlas_float = self._sdf_atlas.texture.astype(np.float32) / 255.0
        logger.info(f"SDF atlas loaded: {self._sdf_atlas.texture.shape}")

    def _render_sdf_text_direct(
        self,
        text: str,
        font_size: int,
        color: tuple[float, float, float],
        opacity: float,
        y_offset: float = 0.0,
        x_offset: float = 0.0,
        is_shadow: bool = False,
    ):
        """Render text directly onto frame buffer using SDF GPU kernel.

        Args:
            text: Text to render
            font_size: Target font size in pixels
            color: RGB color (0-1 range)
            opacity: Text opacity
            y_offset: Vertical offset for animation
            x_offset: Horizontal offset for animation
            is_shadow: If True, render as shadow (offset and darker)
        """
        if not self._use_sdf or self._sdf_atlas is None or taichi_kernels._render_sdf_text is None:
            return

        # Calculate scale from atlas size to target size
        scale = font_size / self._sdf_atlas.font_size

        # Layout text to measure width
        glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        # Apply safe margin - text should fit within 80% of screen width (10% margin each side)
        safe_width = self.config.width * 0.8
        if text_width > safe_width:
            # Scale down to fit within safe area
            width_scale = safe_width / text_width
            scale = scale * width_scale
            # Re-layout with new scale
            glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        # Center text on screen
        center_x = (self.config.width - text_width) / 2
        center_y = (self.config.height - text_height) / 2 + self._sdf_atlas.ascender * scale / 2

        # Apply shadow offset if needed
        shadow_offset = 0.0
        if is_shadow:
            shadow_offset = max(2, int(self.config.height * self.config.shadow_offset_ratio))

        # Smoothing based on scale (smaller text needs more smoothing)
        smoothing = max(0.05, min(0.2, 0.15 / scale))

        # Render using GPU kernel
        taichi_kernels._render_sdf_text(
            self.frame_buffer,
            self._sdf_atlas_float,
            glyph_data,
            len(glyph_data),
            color[0],
            color[1],
            color[2],
            opacity,
            scale,
            center_x + x_offset + shadow_offset,
            center_y + y_offset + shadow_offset,
            smoothing,
        )

    def _render_text_layer(
        self,
        text: str,
        font_size: int,
        color: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Render text to RGBA numpy array using PIL.

        For portrait (height > width), uses multiline word-wrapping
        to keep the font large and readable.
        """
        w, h = self.config.width, self.config.height
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_path = _get_system_font(self.config.font_family)

        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        safe_width = w * 0.88
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width > safe_width:
            # Word-wrap into multiple lines (both portrait and landscape)
            self._draw_multiline_centered(draw, text, font, font_size, safe_width, w, h, color)
        else:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (w - tw) // 2
            y = (h - th) // 2
            draw.text((x, y), text, font=font, fill=color)

        return np.array(img, dtype=np.float32) / 255.0

    @staticmethod
    def _draw_multiline_centered(
        draw,
        text: str,
        font,
        font_size: int,
        max_width: float,
        width: int,
        height: int,
        color: tuple[int, int, int, int],
    ) -> None:
        """Word-wrap text with comma-aware splitting and draw centered."""
        lines = _split_text_for_rendering(draw, text, font, max_width)

        line_height = int(font_size * 1.2)
        total_h = line_height * len(lines)
        start_y = (height - total_h) // 2

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            x = (width - lw) // 2
            y = start_y + i * line_height
            draw.text((x, y), line, font=font, fill=color)

    def _render_text_layers(self, title: str, subtitle: str | None):
        """Pre-render text layers (cached)."""
        if self._cached_text == (title, subtitle):
            return

        title_size = int(self.config.height * self.config.title_size_ratio)
        subtitle_size = int(self.config.height * self.config.subtitle_size_ratio)

        tr, tg, tb = self.text_rgb
        text_rgba = (int(tr * 255), int(tg * 255), int(tb * 255), 255)
        shadow_rgba = (0, 0, 0, int(self.config.shadow_opacity * 255))

        self._title_layer = self._render_text_layer(title, title_size, text_rgba)

        if self.config.enable_shadow:
            self._shadow_layer = self._render_text_layer(title, title_size, shadow_rgba)

        if subtitle:
            self._subtitle_layer = self._render_text_layer(subtitle, subtitle_size, text_rgba)
        else:
            self._subtitle_layer = None

        self._cached_text = (title, subtitle)
