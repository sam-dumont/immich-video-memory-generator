"""Font management for title screens.

Fonts are bundled in the package (bundled_fonts/) — no CDN download needed.
Falls back to Fontsource CDN download if a font isn't bundled.
All fonts are OFL-1.1 licensed (see bundled_fonts/LICENSE).

Supported fonts:
- Outfit (modern geometric)
- Raleway (elegant minimal)
- JosefinSans (vintage elegant)
- Quicksand (friendly rounded)
- Montserrat (geometric humanist)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

# Bundled fonts ship with the package — no network needed
BUNDLED_FONTS_DIR = Path(__file__).parent / "bundled_fonts"

# Font metadata with Fontsource CDN download URLs
# URL pattern: https://cdn.jsdelivr.net/fontsource/fonts/{slug}@latest/latin-{weight}-normal.ttf
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


def get_font_path(
    font_family: str,
    weight: Literal["Light", "Regular", "Medium", "SemiBold"] = "Regular",
    fonts_dir: Path | None = None,
) -> Path | None:
    """Get the path to a specific font file.

    Downloads the font if not already cached.

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
    if dir_name in FONT_DEFINITIONS:
        slug = FONT_DEFINITIONS[dir_name]["fontsource_slug"]
        weight_num = FONT_DEFINITIONS[dir_name]["weights"].get(weight, 400)
        bundled = BUNDLED_FONTS_DIR / slug / f"latin-{weight_num}-normal.ttf"
        if bundled.exists():
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

    # 3. Download from CDN as last resort
    if ensure_font_available(font_family, fonts_dir):
        for name in possible_names:
            font_path = font_dir / name
            if font_path.exists():
                return font_path

    return None


def is_font_cached(
    font_family: str,
    fonts_dir: Path | None = None,
) -> bool:
    """Check if a font family is already cached.

    Args:
        font_family: Font family name.
        fonts_dir: Override fonts directory.

    Returns:
        True if at least one weight of the font is cached.
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    dir_name = font_family.replace(" ", "")
    font_dir = fonts_dir / dir_name

    if not font_dir.exists():
        return False

    # Check for any font files
    return any(font_dir.glob("*.ttf")) or any(font_dir.glob("*.otf"))


def ensure_font_available(
    font_family: str,
    fonts_dir: Path | None = None,
) -> bool:
    """Ensure a font family is available, downloading if needed.

    Args:
        font_family: Font family name.
        fonts_dir: Override fonts directory.

    Returns:
        True if font is available (cached or freshly downloaded).
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    if is_font_cached(font_family, fonts_dir):
        return True

    return download_font(font_family, fonts_dir)


def download_font(
    font_family: str,
    fonts_dir: Path | None = None,
) -> bool:
    """Download a font family from Fontsource CDN.

    Args:
        font_family: Font family name (e.g., "Outfit").
        fonts_dir: Override fonts directory.

    Returns:
        True if download succeeded.
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    # Get font definition
    dir_name = font_family.replace(" ", "")
    if dir_name not in FONT_DEFINITIONS:
        logger.warning(f"Unknown font family: {font_family}")
        return False

    font_def = FONT_DEFINITIONS[dir_name]
    fontsource_slug = font_def["fontsource_slug"]
    weights = font_def["weights"]

    # Create font directory
    font_dir = fonts_dir / dir_name
    font_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading font: {font_family}")

    try:
        downloaded_count = 0

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            for weight_name, weight_value in weights.items():
                # Fontsource CDN URL pattern
                # https://cdn.jsdelivr.net/fontsource/fonts/{slug}@latest/latin-{weight}-normal.ttf
                download_url = (
                    f"https://cdn.jsdelivr.net/fontsource/fonts/"
                    f"{fontsource_slug}@latest/latin-{weight_value}-normal.ttf"
                )

                response = client.get(download_url)
                response.raise_for_status()

                # Verify it's actually a font file (TTF starts with specific bytes)
                if len(response.content) < 100:
                    logger.warning(f"Downloaded file too small for {font_family} {weight_name}")
                    continue

                # Save with our naming convention: FontFamily-Weight.ttf
                target_path = font_dir / f"{dir_name}-{weight_name}.ttf"

                with target_path.open("wb") as f:
                    f.write(response.content)
                    downloaded_count += 1
                    logger.debug(f"Downloaded: {target_path.name}")

        if downloaded_count == 0:
            logger.warning(f"No font files downloaded for {font_family}")
            return False

        logger.info(f"Downloaded {font_family}: {downloaded_count} font files")
        return True

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error downloading {font_family}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error downloading {font_family}: {e}")
        return False


def download_all_fonts(
    fonts_dir: Path | None = None,
    force: bool = False,
) -> dict[str, bool]:
    """Download all supported fonts.

    Args:
        fonts_dir: Override fonts directory.
        force: If True, re-download even if cached.

    Returns:
        Dict mapping font name to success status.
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    results = {}

    for font_family in FONT_DEFINITIONS:
        if not force and is_font_cached(font_family, fonts_dir):
            logger.info(f"Font already cached: {font_family}")
            results[font_family] = True
            continue

        results[font_family] = download_font(font_family, fonts_dir)

    return results


def get_available_fonts(fonts_dir: Path | None = None) -> list[str]:
    """Get list of currently cached font families.

    Args:
        fonts_dir: Override fonts directory.

    Returns:
        List of cached font family names.
    """
    if fonts_dir is None:
        fonts_dir = get_fonts_cache_dir()

    return [
        font_family for font_family in FONT_DEFINITIONS if is_font_cached(font_family, fonts_dir)
    ]


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
    """High-level font management for title screens.

    Usage:
        manager = FontManager()
        manager.ensure_fonts()  # Downloads missing fonts
        font_path = manager.get_font("Outfit", "Medium")
    """

    def __init__(self, fonts_dir: Path | None = None):
        """Initialize font manager.

        Args:
            fonts_dir: Override fonts directory.
        """
        self.fonts_dir = fonts_dir or get_fonts_cache_dir()

    def ensure_fonts(self, fonts: list[str] | None = None) -> bool:
        """Ensure specified fonts are available.

        Args:
            fonts: List of font families to ensure. If None, ensures all.

        Returns:
            True if all fonts are available.
        """
        if fonts is None:
            fonts = list(FONT_DEFINITIONS.keys())

        all_ok = True
        for font in fonts:
            if not ensure_font_available(font, self.fonts_dir):
                all_ok = False

        return all_ok

    def get_font(
        self,
        font_family: str,
        weight: Literal["Light", "Regular", "Medium", "SemiBold"] = "Regular",
    ) -> Path | None:
        """Get path to a font file.

        Args:
            font_family: Font family name.
            weight: Font weight.

        Returns:
            Path to font file, or None.
        """
        return get_font_path(font_family, weight, self.fonts_dir)

    def list_cached(self) -> list[str]:
        """List cached font families.

        Returns:
            List of cached font family names.
        """
        return get_available_fonts(self.fonts_dir)

    def clear_cache(self) -> None:
        """Clear all cached fonts."""
        clear_font_cache(self.fonts_dir)
