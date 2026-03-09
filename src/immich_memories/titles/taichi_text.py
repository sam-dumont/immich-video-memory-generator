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
        """Render text to RGBA numpy array using PIL."""
        img = Image.new("RGBA", (self.config.width, self.config.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        font_path = _get_system_font()

        # Apply safe margin - text should fit within 80% of screen width
        safe_width = self.config.width * 0.8
        current_font_size = font_size

        while current_font_size > 12:  # Don't go below 12px
            try:
                font = ImageFont.truetype(font_path, current_font_size)
            except Exception:
                font = ImageFont.load_default()
                break

            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]

            if text_width <= safe_width:
                break

            # Reduce font size proportionally
            current_font_size = int(current_font_size * safe_width / text_width * 0.95)

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (self.config.width - text_width) // 2
        y = (self.config.height - text_height) // 2

        draw.text((x, y), text, font=font, fill=color)
        return np.array(img, dtype=np.float32) / 255.0

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
