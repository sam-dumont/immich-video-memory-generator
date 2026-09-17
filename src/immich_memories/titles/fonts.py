"""Font management for title screens.

Fonts are bundled in the package (bundled_fonts/), and that is where every
lookup starts. The Fontsource CDN is the last resort for a family the wheel
does not carry, and it is only reached when `network.font_downloads` is on.
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

_CDN_HOST = "cdn.jsdelivr.net"


def font_downloads_allowed() -> bool:
    """Whether this install lets a font come from the CDN. A missing config says no."""
    try:
        from immich_memories.config import get_config

        return get_config().network.font_downloads
    except Exception:  # noqa: BLE001 -- an unreadable config is a "no", not a crash
        return False


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

    Looks in the bundled families, then the user's own font directory, and only
    then asks the CDN -- which answers nothing unless `network.font_downloads`
    is on.

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


# WHY the magic check: the CDN URL is @latest (unpinned) — an sfnt magic plus a
# sane size is the minimum bar before writing executable-adjacent content into
# the font cache.
_SFNT_MAGIC = (
    b"\x00\x01\x00\x00",  # TrueType
    b"OTTO",  # CFF OpenType
    b"true",  # legacy Apple TrueType
)


def _fetch_font_file(client: httpx.Client, slug: str, weight_value: int) -> bytes | None:
    """One Fontsource TTF, or None when what came back is not a font."""
    response = client.get(
        f"https://{_CDN_HOST}/fontsource/fonts/{slug}@latest/latin-{weight_value}-normal.ttf"
    )
    response.raise_for_status()
    if len(response.content) < 100 or response.content[:4] not in _SFNT_MAGIC:
        return None
    return response.content


def download_font(
    font_family: str,
    fonts_dir: Path | None = None,
    *,
    allowed: bool | None = None,
) -> bool:
    """Download a font family from Fontsource CDN.

    Args:
        font_family: Font family name (e.g., "Outfit").
        fonts_dir: Override fonts directory.
        allowed: Whether reaching the CDN is permitted. None asks the config's
            `network.font_downloads`, which is off in a fresh install; `titles
            fonts --download` passes True because the person asked for it.

    Returns:
        True if download succeeded.
    """
    if allowed is None:
        allowed = font_downloads_allowed()
    if not allowed:
        logger.info(
            "Not fetching %s from %s: set network.font_downloads: true to allow it",
            font_family,
            _CDN_HOST,
        )
        return False

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
                content = _fetch_font_file(client, fontsource_slug, weight_value)
                if content is None:
                    logger.warning(
                        f"Downloaded file for {font_family} {weight_name} is not a font; skipping"
                    )
                    continue
                # Save with our naming convention: FontFamily-Weight.ttf
                (font_dir / f"{dir_name}-{weight_name}.ttf").write_bytes(content)
                downloaded_count += 1

        if downloaded_count == 0:
            logger.warning(f"No font files downloaded for {font_family}")
            return False

        logger.info(f"Downloaded {font_family}: {downloaded_count} font files")
        return True

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error downloading {font_family}: {e}")
        return False
    except (OSError, RuntimeError) as e:
        logger.error(f"Error downloading {font_family}: {e}")
        return False


def download_all_fonts(
    fonts_dir: Path | None = None,
    force: bool = False,
    *,
    allowed: bool | None = None,
) -> dict[str, bool]:
    """Download all supported fonts.

    Args:
        fonts_dir: Override fonts directory.
        force: If True, re-download even if cached.
        allowed: Whether reaching the CDN is permitted; None asks the config.

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

        results[font_family] = download_font(font_family, fonts_dir, allowed=allowed)

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
