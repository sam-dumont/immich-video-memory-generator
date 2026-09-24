"""Font management for title screens.

Every title font ships in the package (bundled_fonts/), and nothing here is
ever downloaded while a film renders. All fonts are OFL-1.1 licensed (see
bundled_fonts/LICENSE).

Supported fonts:
- Outfit (modern geometric)
- Raleway (elegant minimal)
- JosefinSans (vintage elegant)
- Quicksand (friendly rounded)
- Montserrat (geometric humanist)

Those five are Latin subsets (Montserrat also carries Latin Extended and
Vietnamese). A letter they lack comes from Noto Sans, bundled for Latin,
Greek, Cyrillic and Vietnamese, or from the Noto script faces installed by
`titles fonts --install`: see `font_chain` and `script_fonts`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

BUNDLED_FONTS_DIR = Path(__file__).parent / "bundled_fonts"


# The bundled families: bundled_fonts/<fontsource_slug>/latin-<weight>-normal.ttf
FONT_DEFINITIONS: dict[str, dict] = {
    "Outfit": {
        "family": "Outfit",
        "fontsource_slug": "outfit",
        "weights": {
            "Light": 300,
            "Regular": 400,
            "Medium": 500,
            "SemiBold": 600,
        },
        "license": "OFL",
    },
    "Raleway": {
        "family": "Raleway",
        "fontsource_slug": "raleway",
        "weights": {
            "Light": 300,
            "Regular": 400,
            "Medium": 500,
            "SemiBold": 600,
        },
        "license": "OFL",
    },
    "JosefinSans": {
        "family": "Josefin Sans",
        "fontsource_slug": "josefin-sans",
        "weights": {
            "Light": 300,
            "Regular": 400,
            "SemiBold": 600,
        },
        "license": "OFL",
    },
    "Quicksand": {
        "family": "Quicksand",
        "fontsource_slug": "quicksand",
        "weights": {
            "Light": 300,
            "Regular": 400,
            "Medium": 500,
            "SemiBold": 600,
        },
        "license": "OFL",
    },
    "Montserrat": {
        "family": "Montserrat",
        "fontsource_slug": "montserrat",
        "weights": {
            "Regular": 400,
            "Medium": 500,
            "SemiBold": 600,
            "Bold": 700,
        },
        "license": "OFL",
    },
}


def get_fonts_cache_dir() -> Path:
    """Get the fonts cache directory.

    Returns:
        Path to ~/.immich-memories/fonts/
    """
    cache_dir = Path.home() / ".immich-memories" / "fonts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


FontWeight = Literal["Light", "Regular", "Medium", "SemiBold", "Bold"]


def bundled_font_path(font_family: str, weight: FontWeight) -> Path | None:
    """The TTF the wheel ships for this exact family and weight, if there is one.

    Unlike `get_font_path` this never substitutes a different weight, so a
    caller walking Bold -> SemiBold -> Medium -> Regular gets the heaviest one
    the family actually has instead of Regular under a Bold name.
    """
    definition = FONT_DEFINITIONS.get(font_family.replace(" ", ""))
    if definition is None:
        return None
    weight_num = definition["weights"].get(weight)
    if weight_num is None:
        return None
    bundled = BUNDLED_FONTS_DIR / definition["fontsource_slug"] / f"latin-{weight_num}-normal.ttf"
    return bundled if bundled.exists() else None


def get_font_path(
    font_family: str,
    weight: FontWeight = "Regular",
    fonts_dir: Path | None = None,
) -> Path | None:
    """Get the path to a specific font file.

    Looks in the bundled families, then the user's own font directory. Nothing
    is downloaded.

    Args:
        font_family: Font family name (e.g., "Outfit", "Raleway").
        weight: Font weight name.
        fonts_dir: Override fonts directory (for testing).

    Returns:
        Path to font file, or None if not available.
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    # Normalize font family name (remove spaces for directory name)
    dir_name = font_family.replace(" ", "")

    # 1. Check bundled fonts first (no network, always available)
    bundled = bundled_font_path(dir_name, weight)
    if bundled is None and dir_name in FONT_DEFINITIONS:
        bundled = bundled_font_path(dir_name, "Regular")
    if bundled is not None:
        return bundled

    # 2. Check user cache
    font_dir = fonts_dir / dir_name
    possible_names = [
        f"{dir_name}-{weight}.ttf",
        f"{font_family}-{weight}.ttf",
        f"{dir_name}-{weight}.otf",
    ]

    for name in possible_names:
        font_path = font_dir / name
        if font_path.exists():
            return font_path

    return None


def clear_font_cache(fonts_dir: Path | None = None) -> None:
    """Clear all cached fonts.

    Args:
        fonts_dir: Override fonts directory.
    """
    import shutil

    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    if fonts_dir.exists():
        for item in fonts_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        logger.info("Font cache cleared")


class FontManager:
    """High-level font management for title screens."""

    def __init__(self, fonts_dir: Path | None = None):
        """Initialize font manager.

        Args:
            fonts_dir: Override fonts directory.
        """
        self.fonts_dir = fonts_dir or get_fonts_cache_dir()

    def clear_cache(self) -> None:
        """Clear all cached fonts."""
        clear_font_cache(self.fonts_dir)
