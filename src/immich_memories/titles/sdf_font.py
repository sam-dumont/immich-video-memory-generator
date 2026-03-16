"""SDF (Signed Distance Field) font atlas generation and GPU text rendering.

This module provides GPU-accelerated text rendering using Signed Distance Fields.
SDFs allow crisp text at any scale with minimal memory, perfect for video titles.

Architecture:
1. Generate SDF glyphs using FreeType's native SDF renderer
2. Pack glyphs into a texture atlas
3. Use Taichi GPU kernels to sample and render text

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.

The implementation is split across helper modules:
- sdf_font.py (this file): Data structures, font discovery
- sdf_atlas_gen.py: Atlas generation and caching
- sdf_font_rendering.py: GPU kernel compilation, text layout, rendering
"""

import logging
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
    # App-managed font cache (downloaded OFL fonts)
    Path.home() / ".immich-memories" / "fonts",
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


def _build_font_candidates(family: str, weight: str) -> list[str]:
    """Build a list of candidate font filenames for a family and weight."""
    candidates = list(
        FONT_MAPPINGS.get(family, [f"{family}.ttf", f"{family}.otf", f"{family}.ttc"])
    )
    weight_suffixes = {
        "light": ["Light", "Thin"],
        "regular": ["Regular", ""],
        "medium": ["Medium"],
        "semibold": ["SemiBold", "DemiBold"],
        "bold": ["Bold"],
    }
    for suffix in weight_suffixes.get(weight, ["Regular"]):
        for ext in (".ttf", ".otf", ".ttc"):
            candidates.extend((f"{family}-{suffix}{ext}", f"{family}{suffix}{ext}"))
    return candidates


def _search_font_paths(candidates: list[str]) -> Path | None:
    """Search FONT_SEARCH_PATHS for a matching candidate file (one level deep)."""
    for search_path in FONT_SEARCH_PATHS:
        if not search_path.exists():
            continue
        for candidate in candidates:
            font_path = search_path / candidate
            if font_path.exists():
                return font_path
            for subdir in search_path.iterdir():
                if subdir.is_dir():
                    font_path = subdir / candidate
                    if font_path.exists():
                        return font_path
    return None


def find_font(family: str, weight: str = "regular") -> Path | None:
    """Find a font file for the given family and weight.

    Args:
        family: Font family name (e.g., "Outfit", "Helvetica")
        weight: Font weight ("light", "regular", "medium", "semibold", "bold")

    Returns:
        Path to font file, or None if not found.
    """
    candidates = _build_font_candidates(family, weight)
    result = _search_font_paths(candidates)
    if result:
        return result

    # System fallback
    for fallback in ("Helvetica", "Arial", "SF Pro"):
        if fallback != family:
            result = find_font(fallback, weight)
            if result:
                logger.warning(f"Font '{family}' not found, using fallback: {result}")
                return result

    return None


__all__ = [
    # Data structures
    "GlyphMetrics",
    "SDFFontAtlas",
    # Font discovery
    "FONT_SEARCH_PATHS",
    "FONT_MAPPINGS",
    "find_font",
    # Optional dependency flags
    "FREETYPE_AVAILABLE",
    "TAICHI_AVAILABLE",
    "freetype",
]
