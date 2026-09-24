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

Those five are Latin subsets. A title with a letter they lack (a Greek or
Cyrillic place name) is drawn whole in Noto Sans, which the wheel carries in
Latin, Greek and Cyrillic for exactly that; see `font_covering`.
"""

from __future__ import annotations

import functools
import hashlib
import logging
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

# Bundled fonts ship with the package — no network needed
BUNDLED_FONTS_DIR = Path(__file__).parent / "bundled_fonts"

_CDN_HOST = "cdn.jsdelivr.net"

# Noto Sans cut to Latin, Greek and Cyrillic: the face a title falls back to,
# whole, when its own family cannot draw one of its letters (#1101).
_COVERAGE_FALLBACK = {
    False: BUNDLED_FONTS_DIR / "noto-sans" / "latin-greek-cyrillic-400-normal.ttf",
    True: BUNDLED_FONTS_DIR / "noto-sans" / "latin-greek-cyrillic-700-normal.ttf",
}


@functools.lru_cache(maxsize=32)
def _codepoints(font_path: str) -> frozenset[int]:
    """Every character this face has a glyph for; empty when it cannot be read."""
    import freetype

    try:
        face = freetype.Face(font_path)
    except (freetype.FT_Exception, OSError):
        return frozenset()
    return frozenset(code for code, _glyph in face.get_chars())


def _missing(font_path: Path | str, text: str) -> str:
    drawn = _codepoints(str(font_path))
    return "".join(dict.fromkeys(c for c in text if not c.isspace() and ord(c) not in drawn))


def font_covering(font_path: Path | str, text: str, *, bold: bool = False) -> str:
    """The face to draw `text` with: `font_path` when it has every letter, else one that does.

    A title is drawn in one face, never glyph by glyph, so a Greek place in a
    French title does not switch typeface mid-line. When no bundled face draws
    it all, the requested face is kept and the missing letters are logged: they
    will show as boxes, and that must not be silent. A path that is not a file
    (a family name the host resolves itself) is passed through untouched.
    """
    if not text or not Path(font_path).is_file() or not _missing(font_path, text):
        return str(font_path)
    fallback = _COVERAGE_FALLBACK[bold]
    if missing := _missing(fallback, text):
        logger.warning("No bundled font draws %r in %r; they will render as boxes", missing, text)
        return str(font_path)
    return str(fallback)


def font_downloads_allowed() -> bool:
    """Whether this install lets a font come from the CDN. A missing config says no."""
    try:
        from immich_memories.config import get_config

        return get_config().network.font_downloads
    except Exception:  # noqa: BLE001 -- an unreadable config is a "no", not a crash
        return False


# Font metadata with Fontsource CDN download URLs
# URL pattern: https://cdn.jsdelivr.net/fontsource/fonts/{slug}@{version}/latin-{weight}-normal.ttf
# WHY pinned (#1212): a download is only written when it is byte-for-byte the file
# hashed here. Bumping FONTSOURCE_VERSION means re-hashing every weight below.
FONTSOURCE_VERSION = "5.3.0"
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
        "sha256": {
            300: "7cef2b493a1ffac30d953d4033c0b8425974793aac64618d8b9fa6b0007110d0",
            400: "5a3544ab64f4141df71b00c1b419a8db7875b3fdbecac0ac6719ee6b22492b63",
            500: "b3f3cf7e4a76854ae40c1fe52a5c78731d4ec334bb8cd88fb64e182b3e78a4cb",
            600: "8c2e78057d3cf15dd306d13de33b96222a6165dc1f1d6a911b268c5d9afa7161",
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
        "sha256": {
            300: "798ef5f6181523db2229c110f65801685dd37ba049c856e5209d6b65f3962cfd",
            400: "d06bc6dfc4fa496a2708997f437869758d9eaa602a0d587199c189f89d343997",
            500: "43fe1f5bf092ed966393fbd7a75a950d959b3e5aa13a870c915f8fbd522e453c",
            600: "1c30e97f84294c253c76bfdc419998ddddddcf72808b0fb47663da75f0aa59cf",
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
        "sha256": {
            300: "538a04b04499568422f3431fe877f4e760c0d4209c11b2658e46d115e2c2293e",
            400: "980cd9c089b2af273a7d4ad09a27f58cbc8bde569469a6812f251fead1734ed0",
            600: "f8f9aefb0d5a873c13a5647e73a0b5a1c7b58732e5fbbc705963f61f0342714a",
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
        "sha256": {
            300: "f2df2ed710e0df7483d24a39479008fd5c0cd3930cff56e9eba2b45033419487",
            400: "11b15e5b98bdf122a1ad97b9eae88c22d8ebe12aa3e53b4fa93811e7b7b6e159",
            500: "f50040b2984d42ae6c0f1ca63fe7b90f3decac9d12fcb3a1a3dd02f3ca50c67c",
            600: "eb226417c527b39bff615c16fd123d84f46532a4311b878c49a585ef6c89ec2b",
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
        "sha256": {
            400: "ee184384f5cfb207380099f29b6d018deb381ee952c977fa79b4f7cd8858a4c0",
            500: "ceefe8658a7b86fd27f23ff1571da1a5ff15b4c502ba3c0f9130f5bb9fed9cc7",
            600: "9d6d7ddc09b5f0f2eb570d8622cf8f8c9742fdcb7c1801cb76b37fa45866accf",
            700: "1f155ae658c6f1ba0ea4ee53726c496f774a1ab8462b46a03812377e157166ca",
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


def _fetch_font_file(client: httpx.Client, font_def: dict, weight_value: int) -> bytes | None:
    """One pinned Fontsource TTF, or None when what came back is not the pinned file."""
    slug = font_def["fontsource_slug"]
    response = client.get(
        f"https://{_CDN_HOST}/fontsource/fonts/{slug}@{FONTSOURCE_VERSION}"
        f"/latin-{weight_value}-normal.ttf"
    )
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != font_def["sha256"].get(weight_value):
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
    weights = font_def["weights"]

    # Create font directory
    font_dir = fonts_dir / dir_name
    font_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading font: {font_family}")

    try:
        downloaded_count = 0

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            for weight_name, weight_value in weights.items():
                content = _fetch_font_file(client, font_def, weight_value)
                if content is None:
                    logger.warning(
                        f"Downloaded file for {font_family} {weight_name} does not match "
                        "its pinned hash; skipping"
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
