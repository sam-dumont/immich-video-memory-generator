"""SDF font atlas generation and caching.

Generates Signed Distance Field font atlases from TrueType/OpenType fonts
using FreeType's native SDF renderer, and provides caching for reuse.
"""

import logging
import math
from pathlib import Path

import numpy as np

from .sdf_font import FREETYPE_AVAILABLE, GlyphMetrics, SDFFontAtlas, freetype

logger = logging.getLogger(__name__)

# Characters to include in the atlas
DEFAULT_CHARSET = (
    # ASCII printable
    " !\"#$%&'()*+,-./0123456789:;<=>?@"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
    # Extended Latin (accents)
    "\u00c0\u00c1\u00c2\u00c3\u00c4\u00c5\u00c6\u00c7\u00c8\u00c9\u00ca\u00cb\u00cc\u00cd\u00ce\u00cf\u00d0\u00d1\u00d2\u00d3\u00d4\u00d5\u00d6\u00d8\u00d9\u00da\u00db\u00dc\u00dd\u00de\u00df\u00e0\u00e1\u00e2\u00e3\u00e4\u00e5\u00e6\u00e7\u00e8\u00e9\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f0\u00f1\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8\u00f9\u00fa\u00fb\u00fc\u00fd\u00fe\u00ff"
    # Common symbols
    "\u00a9\u00ae\u2122\u20ac\u00a3\u00a5\u00a2\u00b0\u00b1\u00b2\u00b3\u00b9\u00ba\u00bc\u00bd\u00be\u00d7\u00f7"
    # Quotes and punctuation
    "\u2018\u2019\u201c\u201d"
    "\u2026\u2013\u2014"
)


def _render_glyph(face, char: str) -> tuple[str, np.ndarray, GlyphMetrics] | None:
    """Render a single glyph and return its data, or None on failure."""
    try:
        face.load_char(char, freetype.FT_LOAD_DEFAULT)
        face.glyph.render(freetype.FT_RENDER_MODE_SDF)

        bitmap = face.glyph.bitmap
        if bitmap.width == 0 or bitmap.rows == 0:
            return (
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

        buffer = np.array(bitmap.buffer, dtype=np.uint8).reshape((bitmap.rows, bitmap.width))

        metrics = GlyphMetrics(
            char=char,
            atlas_x=0,
            atlas_y=0,
            atlas_width=bitmap.width,
            atlas_height=bitmap.rows,
            bearing_x=face.glyph.bitmap_left,
            bearing_y=face.glyph.bitmap_top,
            advance_x=face.glyph.advance.x >> 6,
        )

        return (char, buffer, metrics)

    except Exception as e:
        logger.debug(f"Failed to render glyph '{char}': {e}")
        return None


def _pack_glyphs(
    glyph_data: list[tuple[str, np.ndarray, GlyphMetrics]],
    total_width: int,
    max_height: int,
    padding: int,
) -> tuple[np.ndarray, dict[str, GlyphMetrics]]:
    """Pack rendered glyphs into an atlas texture.

    Returns:
        Tuple of (texture array, glyphs dict).
    """
    # Calculate atlas dimensions (try to make it roughly square)
    atlas_width = int(math.sqrt(total_width * max_height) * 1.2)
    atlas_width = min(2048, max(256, (atlas_width + 255) // 256 * 256))

    atlas_height = 256
    texture = np.zeros((atlas_height, atlas_width), dtype=np.uint8)
    glyphs: dict[str, GlyphMetrics] = {}

    x, y = padding, padding
    row_height = 0

    for char, buffer, metrics in glyph_data:
        w, h = metrics.atlas_width, metrics.atlas_height

        if x + w + padding > atlas_width:
            x = padding
            y += row_height + padding
            row_height = 0

        while y + h + padding > atlas_height:
            new_texture = np.zeros((atlas_height * 2, atlas_width), dtype=np.uint8)
            new_texture[:atlas_height, :] = texture
            texture = new_texture
            atlas_height *= 2

        if h > 0 and w > 0:
            texture[y : y + h, x : x + w] = buffer

        metrics.atlas_x = x
        metrics.atlas_y = y
        glyphs[char] = metrics

        x += w + padding
        row_height = max(row_height, h)

    # Trim unused space
    final_height = y + row_height + padding
    final_height = ((final_height + 63) // 64) * 64
    texture = texture[:final_height, :]

    return texture, glyphs


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

    face = freetype.Face(str(font_path))
    face.set_pixel_sizes(font_size, font_size)

    ascender = face.ascender >> 6
    descender = face.descender >> 6
    line_height = face.height >> 6

    glyph_data: list[tuple[str, np.ndarray, GlyphMetrics]] = []
    total_width = 0
    max_height = 0

    for char in charset:
        result = _render_glyph(face, char)
        if result is not None:
            glyph_data.append(result)
            total_width += result[2].atlas_width + padding
            max_height = max(max_height, result[2].atlas_height)

    texture, glyphs = _pack_glyphs(glyph_data, total_width, max_height, padding)

    logger.info(f"Generated SDF atlas: {texture.shape[1]}x{texture.shape[0]}, {len(glyphs)} glyphs")

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
