"""SDF (Signed Distance Field) font atlas generation and GPU text rendering.

This module provides GPU-accelerated text rendering using Signed Distance Fields.
SDFs allow crisp text at any scale with minimal memory, perfect for video titles.

Architecture:
1. Generate SDF glyphs using FreeType's native SDF renderer
2. Pack glyphs into a texture atlas
3. Use Taichi GPU kernels to sample and render text

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Optional dependencies
try:
    import freetype

    FREETYPE_AVAILABLE = True
except ImportError:
    FREETYPE_AVAILABLE = False
    freetype = None  # type: ignore

try:
    import taichi as ti

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    ti = None  # type: ignore


# =============================================================================
# Glyph and Atlas Data Structures
# =============================================================================


@dataclass
class GlyphMetrics:
    """Metrics for a single glyph in the atlas."""

    char: str
    # Position in atlas (pixels)
    atlas_x: int
    atlas_y: int
    atlas_width: int
    atlas_height: int
    # Glyph metrics (in pixels at render size)
    bearing_x: int  # Left side bearing
    bearing_y: int  # Top bearing (from baseline)
    advance_x: int  # Horizontal advance to next glyph


@dataclass
class SDFFontAtlas:
    """SDF font atlas with glyph metrics."""

    # Atlas texture (grayscale, 0-255)
    texture: np.ndarray
    # Glyph lookup
    glyphs: dict[str, GlyphMetrics]
    # Font metrics
    font_size: int  # Size used to generate atlas
    line_height: int  # Recommended line spacing
    ascender: int  # Distance from baseline to top
    descender: int  # Distance from baseline to bottom (negative)
    # SDF parameters
    spread: int = 8  # Distance field spread in pixels


# =============================================================================
# Font Discovery
# =============================================================================

# Common font paths by platform
FONT_SEARCH_PATHS = [
    # macOS
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    # Linux
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    # Windows
    Path("C:/Windows/Fonts"),
]

# Font family name mappings to actual font files
FONT_MAPPINGS = {
    # Sans-serif
    "Outfit": ["Outfit-Regular.ttf", "Outfit-Medium.ttf", "Outfit-SemiBold.ttf"],
    "Quicksand": ["Quicksand-Regular.ttf", "Quicksand-Medium.ttf"],
    "Poppins": ["Poppins-Regular.ttf", "Poppins-Medium.ttf"],
    "Montserrat": ["Montserrat-Regular.ttf", "Montserrat-Medium.ttf"],
    "Inter": ["Inter-Regular.ttf", "Inter-Medium.ttf"],
    # Serif
    "Playfair": ["PlayfairDisplay-Regular.ttf"],
    "Cormorant": ["Cormorant-Regular.ttf"],
    "Lora": ["Lora-Regular.ttf"],
    "JosefinSans": ["JosefinSans-Regular.ttf", "JosefinSans-Light.ttf"],
    # System fallbacks
    "Helvetica": ["Helvetica.ttc", "HelveticaNeue.ttc"],
    "Arial": ["Arial.ttf", "arial.ttf"],
    "SF Pro": ["SF-Pro-Display-Regular.otf", "SF-Pro.ttf"],
}


def find_font(family: str, weight: str = "regular") -> Path | None:
    """Find a font file for the given family and weight.

    Args:
        family: Font family name (e.g., "Outfit", "Helvetica")
        weight: Font weight ("light", "regular", "medium", "semibold", "bold")

    Returns:
        Path to font file, or None if not found.
    """
    # Get candidate filenames for this family
    candidates = FONT_MAPPINGS.get(family, [f"{family}.ttf", f"{family}.otf", f"{family}.ttc"])

    # Add weight-specific variants
    weight_suffixes = {
        "light": ["Light", "Thin"],
        "regular": ["Regular", ""],
        "medium": ["Medium"],
        "semibold": ["SemiBold", "DemiBold"],
        "bold": ["Bold"],
    }

    for suffix in weight_suffixes.get(weight, ["Regular"]):
        for ext in [".ttf", ".otf", ".ttc"]:
            candidates.append(f"{family}-{suffix}{ext}")
            candidates.append(f"{family}{suffix}{ext}")

    # Search for font files
    for search_path in FONT_SEARCH_PATHS:
        if not search_path.exists():
            continue

        for candidate in candidates:
            # Direct match
            font_path = search_path / candidate
            if font_path.exists():
                return font_path

            # Recursive search (one level deep)
            for subdir in search_path.iterdir():
                if subdir.is_dir():
                    font_path = subdir / candidate
                    if font_path.exists():
                        return font_path

    # System fallback
    for fallback in ["Helvetica", "Arial", "SF Pro"]:
        if fallback != family:
            result = find_font(fallback, weight)
            if result:
                logger.warning(f"Font '{family}' not found, using fallback: {result}")
                return result

    return None


# =============================================================================
# SDF Atlas Generation
# =============================================================================

# Characters to include in the atlas
DEFAULT_CHARSET = (
    # ASCII printable
    " !\"#$%&'()*+,-./0123456789:;<=>?@"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
    # Extended Latin (accents)
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
    # Common symbols
    "©®™€£¥¢°±²³¹º¼½¾×÷"
    # Quotes and punctuation
    "''"
    "…–—"
)


def generate_sdf_atlas(
    font_path: Path | str,
    font_size: int = 64,
    charset: str = DEFAULT_CHARSET,
    spread: int = 8,
    padding: int = 4,
) -> SDFFontAtlas:
    """Generate an SDF font atlas from a font file.

    Args:
        font_path: Path to TTF/OTF font file.
        font_size: Size to render glyphs at (larger = better quality).
        charset: Characters to include in atlas.
        spread: SDF spread distance in pixels.
        padding: Padding between glyphs in atlas.

    Returns:
        SDFFontAtlas with texture and glyph metrics.
    """
    if not FREETYPE_AVAILABLE:
        raise ImportError("freetype-py required for SDF font generation")

    # Load font
    face = freetype.Face(str(font_path))
    face.set_pixel_sizes(font_size, font_size)

    # Get font metrics
    ascender = face.ascender >> 6  # Convert from 26.6 fixed-point
    descender = face.descender >> 6
    line_height = face.height >> 6

    # First pass: render all glyphs and calculate atlas size
    glyph_data: list[tuple[str, np.ndarray, GlyphMetrics]] = []
    total_width = 0
    max_height = 0

    for char in charset:
        try:
            # Load and render glyph as SDF
            face.load_char(char, freetype.FT_LOAD_DEFAULT)
            face.glyph.render(freetype.FT_RENDER_MODE_SDF)

            bitmap = face.glyph.bitmap
            if bitmap.width == 0 or bitmap.rows == 0:
                # Space or empty glyph
                glyph_data.append(
                    (
                        char,
                        np.zeros((1, 1), dtype=np.uint8),
                        GlyphMetrics(
                            char=char,
                            atlas_x=0,
                            atlas_y=0,
                            atlas_width=1,
                            atlas_height=1,
                            bearing_x=0,
                            bearing_y=0,
                            advance_x=face.glyph.advance.x >> 6,
                        ),
                    )
                )
                continue

            # Copy bitmap buffer
            buffer = np.array(bitmap.buffer, dtype=np.uint8).reshape((bitmap.rows, bitmap.width))

            metrics = GlyphMetrics(
                char=char,
                atlas_x=0,  # Will be set during packing
                atlas_y=0,
                atlas_width=bitmap.width,
                atlas_height=bitmap.rows,
                bearing_x=face.glyph.bitmap_left,
                bearing_y=face.glyph.bitmap_top,
                advance_x=face.glyph.advance.x >> 6,
            )

            glyph_data.append((char, buffer, metrics))
            total_width += bitmap.width + padding
            max_height = max(max_height, bitmap.rows)

        except Exception as e:
            logger.debug(f"Failed to render glyph '{char}': {e}")

    # Calculate atlas dimensions (try to make it roughly square)
    atlas_width = int(math.sqrt(total_width * max_height) * 1.2)
    atlas_width = min(2048, max(256, (atlas_width + 255) // 256 * 256))  # Round to 256

    # Pack glyphs into atlas (simple left-to-right, top-to-bottom)
    atlas_height = 256
    texture = np.zeros((atlas_height, atlas_width), dtype=np.uint8)
    glyphs: dict[str, GlyphMetrics] = {}

    x, y = padding, padding
    row_height = 0

    for char, buffer, metrics in glyph_data:
        w, h = metrics.atlas_width, metrics.atlas_height

        # Check if we need a new row
        if x + w + padding > atlas_width:
            x = padding
            y += row_height + padding
            row_height = 0

        # Check if we need to expand atlas height
        while y + h + padding > atlas_height:
            new_texture = np.zeros((atlas_height * 2, atlas_width), dtype=np.uint8)
            new_texture[:atlas_height, :] = texture
            texture = new_texture
            atlas_height *= 2

        # Copy glyph to atlas
        if h > 0 and w > 0:
            texture[y : y + h, x : x + w] = buffer

        # Update metrics
        metrics.atlas_x = x
        metrics.atlas_y = y
        glyphs[char] = metrics

        # Advance position
        x += w + padding
        row_height = max(row_height, h)

    # Trim unused space
    final_height = y + row_height + padding
    final_height = ((final_height + 63) // 64) * 64  # Round up to 64
    texture = texture[:final_height, :]

    logger.info(f"Generated SDF atlas: {atlas_width}x{final_height}, {len(glyphs)} glyphs")

    return SDFFontAtlas(
        texture=texture,
        glyphs=glyphs,
        font_size=font_size,
        line_height=line_height,
        ascender=ascender,
        descender=descender,
        spread=spread,
    )


# =============================================================================
# Atlas Caching
# =============================================================================

_atlas_cache: dict[tuple[str, int], SDFFontAtlas] = {}


def get_cached_atlas(font_path: Path | str, font_size: int = 64) -> SDFFontAtlas:
    """Get or create a cached SDF atlas for a font.

    Args:
        font_path: Path to font file.
        font_size: Size for atlas generation.

    Returns:
        Cached or newly generated SDFFontAtlas.
    """
    cache_key = (str(font_path), font_size)
    if cache_key not in _atlas_cache:
        _atlas_cache[cache_key] = generate_sdf_atlas(font_path, font_size)
    return _atlas_cache[cache_key]


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
