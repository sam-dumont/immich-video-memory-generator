"""SDF GPU text rendering with Taichi kernels.

Provides GPU-accelerated text rendering using Signed Distance Field atlases
and Taichi compute kernels for high-performance text compositing.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging

import numpy as np

from .sdf_font import SDFFontAtlas

logger = logging.getLogger(__name__)

# Optional dependency
try:
    import taichi as ti

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    ti = None  # type: ignore


# =============================================================================
# GPU Text Rendering with Taichi
# =============================================================================

# Module-level kernel references (set after ti.init)
_render_sdf_text = None
_kernels_compiled = False


def _compile_sdf_kernels():
    """Compile Taichi kernels for SDF text rendering."""
    global _render_sdf_text, _kernels_compiled

    if not TAICHI_AVAILABLE:
        return

    if _kernels_compiled:
        return

    @ti.kernel
    def render_sdf_text(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),  # RGB output
        atlas: ti.types.ndarray(dtype=ti.f32, ndim=2),  # SDF atlas
        glyph_data: ti.types.ndarray(dtype=ti.f32, ndim=2),  # [n_glyphs, 10] metrics
        num_glyphs: ti.i32,
        color_r: ti.f32,
        color_g: ti.f32,
        color_b: ti.f32,
        opacity: ti.f32,
        scale: ti.f32,
        offset_x: ti.f32,
        offset_y: ti.f32,
        smoothing: ti.f32,
    ):
        """Render SDF text onto output buffer.

        glyph_data format per glyph [10 floats]:
            0: screen_x (destination x)
            1: screen_y (destination y)
            2: atlas_x
            3: atlas_y
            4: atlas_w
            5: atlas_h
            6: bearing_x
            7: bearing_y
            8-9: reserved
        """
        height = output.shape[0]
        width = output.shape[1]
        atlas_h = atlas.shape[0]
        atlas_w = atlas.shape[1]

        for y, x in ti.ndrange(height, width):
            # Check each glyph
            for g in range(num_glyphs):
                # Get glyph data
                screen_x = glyph_data[g, 0] + offset_x
                screen_y = glyph_data[g, 1] + offset_y
                a_x = glyph_data[g, 2]
                a_y = glyph_data[g, 3]
                a_w = glyph_data[g, 4]
                a_h = glyph_data[g, 5]
                bearing_x = glyph_data[g, 6]
                bearing_y = glyph_data[g, 7]

                # Calculate position within glyph
                glyph_x = (x - screen_x - bearing_x * scale) / scale
                glyph_y = (y - screen_y + bearing_y * scale) / scale

                # Check bounds
                if 0 <= glyph_x < a_w and 0 <= glyph_y < a_h:
                    # Sample from atlas
                    ax = int(a_x + glyph_x)
                    ay = int(a_y + glyph_y)

                    if 0 <= ax < atlas_w and 0 <= ay < atlas_h:
                        # Get SDF value (0-1, 0.5 = edge)
                        sdf = atlas[ay, ax]

                        # Apply smoothstep for anti-aliasing
                        # SDF is 0-1 with ~0.5 at edge (128/255)
                        edge = 0.5
                        alpha = (
                            ti.max(0.0, ti.min(1.0, (sdf - edge + smoothing) / (2.0 * smoothing)))
                            * opacity
                        )

                        # Composite with premultiplied alpha
                        if alpha > 0.001:
                            output[y, x, 0] = output[y, x, 0] * (1.0 - alpha) + color_r * alpha
                            output[y, x, 1] = output[y, x, 1] * (1.0 - alpha) + color_g * alpha
                            output[y, x, 2] = output[y, x, 2] * (1.0 - alpha) + color_b * alpha

    _render_sdf_text = render_sdf_text
    _kernels_compiled = True
    logger.info("SDF text kernels compiled")


def init_sdf_kernels():
    """Initialize SDF text rendering kernels (call after ti.init)."""
    _compile_sdf_kernels()


# =============================================================================
# Text Layout
# =============================================================================


def layout_text(
    text: str,
    atlas: SDFFontAtlas,
    x: float = 0,
    y: float = 0,
    scale: float = 1.0,
) -> tuple[np.ndarray, float, float]:
    """Calculate glyph positions for text layout.

    Args:
        text: Text to layout.
        atlas: SDF font atlas.
        x, y: Starting position.
        scale: Scale factor.

    Returns:
        Tuple of (glyph_data array, text_width, text_height).
    """
    glyph_data = []
    cursor_x = x
    cursor_y = y

    for char in text:
        if char == "\n":
            cursor_x = x
            cursor_y += atlas.line_height * scale
            continue

        metrics = atlas.glyphs.get(char)
        if metrics is None:
            # Unknown character, use space width
            space_metrics = atlas.glyphs.get(" ")
            if space_metrics:
                cursor_x += space_metrics.advance_x * scale
            continue

        # Store glyph data [screen_x, screen_y, atlas coords, metrics]
        glyph_data.append(
            [
                cursor_x,
                cursor_y,
                float(metrics.atlas_x),
                float(metrics.atlas_y),
                float(metrics.atlas_width),
                float(metrics.atlas_height),
                float(metrics.bearing_x),
                float(metrics.bearing_y),
                0.0,
                0.0,  # Reserved
            ]
        )

        cursor_x += metrics.advance_x * scale

    if not glyph_data:
        return np.zeros((1, 10), dtype=np.float32), 0.0, 0.0

    text_width = cursor_x - x
    text_height = atlas.line_height * scale

    return np.array(glyph_data, dtype=np.float32), text_width, text_height


def render_text_sdf(
    output: np.ndarray,
    text: str,
    atlas: SDFFontAtlas,
    x: float,
    y: float,
    scale: float = 1.0,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    opacity: float = 1.0,
    smoothing: float = 0.1,
):
    """Render text using SDF atlas and Taichi GPU.

    Args:
        output: RGB output buffer (H, W, 3), float32 0-1.
        text: Text to render.
        atlas: SDF font atlas.
        x, y: Position (top-left of text bounding box).
        scale: Scale factor (1.0 = atlas font_size).
        color: RGB color (0-1).
        opacity: Text opacity.
        smoothing: Edge smoothing amount.
    """
    if _render_sdf_text is None:
        raise RuntimeError("SDF kernels not initialized. Call init_sdf_kernels() after ti.init()")

    # Layout text
    glyph_data, _, _ = layout_text(text, atlas, x, y, scale)

    # Normalize atlas to 0-1
    atlas_float = atlas.texture.astype(np.float32) / 255.0

    # Render
    _render_sdf_text(
        output,
        atlas_float,
        glyph_data,
        len(glyph_data),
        color[0],
        color[1],
        color[2],
        opacity,
        scale,
        0.0,
        0.0,
        smoothing,
    )


def measure_text(text: str, atlas: SDFFontAtlas, scale: float = 1.0) -> tuple[float, float]:
    """Measure text dimensions.

    Args:
        text: Text to measure.
        atlas: SDF font atlas.
        scale: Scale factor.

    Returns:
        Tuple of (width, height) in pixels.
    """
    _, width, height = layout_text(text, atlas, 0, 0, scale)
    return width, height
