"""Trip map rendering — staticmap tiles + PIL text overlay.

Renders map tiles with big location pins + city labels, produces PIL Images
or numpy arrays ready for the Taichi GPU pipeline.
"""

from __future__ import annotations

import contextlib
import logging
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from staticmap import CircleMarker, StaticMap  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Map styling constants
_PIN_COLOR = "#E85D4A"
_PIN_SIZE = 16
_PIN_OUTLINE_COLOR = "#FFFFFF"
_PIN_OUTLINE_SIZE = 20
_LABEL_COLOR = (255, 255, 255, 190)  # Semi-transparent white
_LABEL_SHADOW_COLOR = (0, 0, 0, 80)  # Very subtle shadow
_TEXT_COLOR = "#FFFFFF"
_OSM_ATTRIBUTION = "\u00a9 OpenStreetMap contributors"

# Tile URL templates — only providers that work without API keys.
MAP_STYLES: dict[str, str] = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "topo": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}
DEFAULT_MAP_STYLE = "satellite"


def render_trip_map_frame(
    locations: list[tuple[float, float]],
    title_text: str,
    width: int = 1920,
    height: int = 1080,
    location_names: list[str] | None = None,
    map_style: str = DEFAULT_MAP_STYLE,
    **_kwargs,
) -> Image.Image:
    """Render a map frame with big pins, city labels, and centered title."""
    base_map, sm = _render_base_map(locations, width, height, map_style)

    if location_names and sm is not None:
        _draw_pin_labels(base_map, locations, location_names, sm)

    _draw_title_band(base_map, title_text, width, height)
    _add_attribution(base_map, width, height)

    return base_map


def render_trip_map_array(
    locations: list[tuple[float, float]],
    width: int = 1920,
    height: int = 1080,
    location_names: list[str] | None = None,
    map_style: str = DEFAULT_MAP_STYLE,
) -> np.ndarray:
    """Render map as numpy float32 array for Taichi GPU pipeline.

    Returns array normalized to [0, 1] range, shape (height, width, 3).
    Includes pins and city labels but no title (title rendered by GPU).
    """
    base_map, sm = _render_base_map(locations, width, height, map_style)

    if location_names and sm is not None:
        _draw_pin_labels(base_map, locations, location_names, sm)

    _add_attribution(base_map, width, height)
    arr = np.array(base_map, dtype=np.float32) / 255.0
    return arr


def render_location_card(
    location_name: str,
    width: int = 1920,
    height: int = 1080,
    lat: float | None = None,
    lon: float | None = None,
    map_style: str = DEFAULT_MAP_STYLE,
) -> Image.Image:
    """Render a location interstitial card with map background.

    If lat/lon provided, renders a zoomed satellite map behind the name.
    Otherwise falls back to dark gradient.
    """
    if lat is not None and lon is not None:
        img, _sm = _render_base_map([(lat, lon)], width, height, map_style)
        # Darken the map so text pops
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 100))
        img_rgba = img.convert("RGBA")
        img = Image.alpha_composite(img_rgba, overlay).convert("RGB")
    else:
        img = Image.new("RGB", (width, height), color=(30, 30, 35))

    draw = ImageDraw.Draw(img)
    font_size = int(height * 0.10)
    font = _get_font(font_size, bold=True)

    bbox = draw.textbbox((0, 0), location_name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) // 2
    y = (height - text_h) // 2

    draw.text((x, y), location_name, fill=_TEXT_COLOR, font=font)
    return img


def render_equirectangular_map(
    center_lat: float,
    center_lon: float,
    width: int = 720,
    height: int = 360,
    map_style: str = DEFAULT_MAP_STYLE,
) -> np.ndarray:
    """Fetch satellite tiles as a wide-area equirectangular texture.

    Used as the globe projection texture — no pins or text, just the
    raw satellite imagery centered on the trip area.

    Returns:
        float32 array (height, width, 3) normalized to [0, 1].
    """
    url_template = MAP_STYLES.get(map_style, MAP_STYLES[DEFAULT_MAP_STYLE])
    m = StaticMap(width, height, url_template=url_template)

    # Add an invisible marker at center to set the map viewport
    m.add_marker(CircleMarker((center_lon, center_lat), "#00000000", 1))

    try:
        image = m.render()
    except Exception:
        logger.warning("Equirectangular tile fetch failed, using dark fallback")
        dark = np.full((height, width, 3), 0.15, dtype=np.float32)
        return dark

    if image.size != (width, height):
        image = image.resize((width, height))

    return np.array(image.convert("RGB"), dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------
# Internal rendering helpers
# ---------------------------------------------------------------------------


def _render_base_map(
    locations: list[tuple[float, float]],
    width: int,
    height: int,
    map_style: str = DEFAULT_MAP_STYLE,
) -> tuple[Image.Image, StaticMap | None]:
    """Render map tiles with big location pins. Returns (image, StaticMap)."""
    url_template = MAP_STYLES.get(map_style, MAP_STYLES[DEFAULT_MAP_STYLE])
    m = StaticMap(width, height, url_template=url_template)

    for lat, lon in locations:
        m.add_marker(CircleMarker((lon, lat), _PIN_OUTLINE_COLOR, _PIN_OUTLINE_SIZE))
        m.add_marker(CircleMarker((lon, lat), _PIN_COLOR, _PIN_SIZE))

    try:
        image = m.render()
    except Exception:
        logger.warning("Map tile fetch failed, using solid background")
        image = Image.new("RGB", (width, height), color=(40, 50, 60))
        return image, None

    if image.size != (width, height):
        image = image.resize((width, height))

    return image, m


def _draw_pin_labels(
    image: Image.Image,
    locations: list[tuple[float, float]],
    names: list[str],
    sm: StaticMap,
) -> None:
    """Draw city name labels next to each pin, blended with the map."""
    if not locations or not names:
        return

    w, h = image.size
    label_size = max(12, int(min(w, h) * 0.018))
    font = _get_font(label_size, bold=False)

    # Draw labels on RGBA overlay for semi-transparent blending
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for (lat, lon), name in zip(locations, names, strict=False):
        px, py = _geo_to_pixel(lat, lon, sm)
        px = max(5, min(w - 5, px))
        py = max(5, min(h - 5, py))
        _draw_label_at(draw, name, px, py, w, h, font)

    # Composite the semi-transparent labels onto the map
    image_rgba = image.convert("RGBA")
    composited = Image.alpha_composite(image_rgba, overlay)
    image.paste(composited.convert("RGB"))


def _geo_to_pixel(lat: float, lon: float, sm: StaticMap) -> tuple[int, int]:
    """Convert (lat, lon) to pixel coordinates using staticmap internals."""
    from staticmap.staticmap import _lat_to_y, _lon_to_x  # type: ignore[import-untyped]

    x_tile = _lon_to_x(lon, sm.zoom)
    y_tile = _lat_to_y(lat, sm.zoom)

    px = sm._x_to_px(x_tile)
    py = sm._y_to_px(y_tile)

    return px, py


def _draw_label_at(draw, name: str, px: int, py: int, width: int, height: int, font) -> None:
    """Draw a single city label near a pin location."""
    offset_x = int(width * 0.015)
    offset_y = int(-height * 0.012)

    lx = px + offset_x
    ly = py + offset_y

    # Measure text to check bounds
    bbox = draw.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]

    # If label would go off right edge, place it to the left of the pin
    if lx + text_w > width - 10:
        lx = px - offset_x - text_w

    # Shadow for readability (2px offset)
    draw.text((lx + 2, ly + 2), name, fill=_LABEL_SHADOW_COLOR, font=font)
    draw.text((lx, ly), name, fill=_LABEL_COLOR, font=font)


def _draw_title_band(
    image: Image.Image,
    title: str,
    width: int,
    height: int,
) -> None:
    """Draw the title centered with a soft blended backdrop.

    For portrait: multiline with bigger font.
    For landscape: single line centered.
    """
    is_portrait = height > width
    draw = ImageDraw.Draw(image)

    if is_portrait:
        _draw_title_portrait(draw, image, title, width, height)
    else:
        _draw_title_landscape(draw, image, title, width, height)


def _draw_title_landscape(draw, image: Image.Image, title: str, width: int, height: int) -> None:
    """Landscape: single line, centered, 9% height."""
    title_size = int(height * 0.09)
    title_font = _get_font(title_size, bold=True)

    bbox = draw.textbbox((0, 0), title, font=title_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    if text_w > width * 0.9:
        title_size = int(title_size * (width * 0.9) / text_w)
        title_font = _get_font(title_size, bold=True)
        bbox = draw.textbbox((0, 0), title, font=title_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    x = (width - text_w) // 2
    y = (height - text_h) // 2

    _apply_gradient_band(image, y + text_h // 2, int(height * 0.12), width, height)
    draw = ImageDraw.Draw(image)
    draw.text((x, y), title, fill=_TEXT_COLOR, font=title_font)


def _draw_title_portrait(draw, image: Image.Image, title: str, width: int, height: int) -> None:
    """Portrait: multiline with MUCH bigger font. Split on comma or space."""
    title_size = int(width * 0.12)
    title_font = _get_font(title_size, bold=True)

    # Split into lines — prefer splitting at comma, then at spaces
    lines = _split_title_for_portrait(title, draw, title_font, int(width * 0.9))

    # Recalculate if still too wide
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        if line_w > width * 0.92:
            scale = (width * 0.92) / line_w
            title_size = int(title_size * scale)
            title_font = _get_font(title_size, bold=True)
            break

    line_height = int(title_size * 1.25)
    total_text_height = line_height * len(lines)
    start_y = (height - total_text_height) // 2

    _apply_gradient_band(
        image,
        start_y + total_text_height // 2,
        int(height * 0.1) + total_text_height // 2,
        width,
        height,
    )
    draw = ImageDraw.Draw(image)

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        y = start_y + i * line_height
        draw.text((x, y), line, fill=_TEXT_COLOR, font=title_font)


def _split_title_for_portrait(title: str, draw, font, max_width: int) -> list[str]:
    """Split title into lines that fit within max_width."""
    # First try splitting at comma
    if "," in title:
        parts = [p.strip() for p in title.split(",", 1)]
        all_fit = all(
            draw.textbbox((0, 0), p, font=font)[2] - draw.textbbox((0, 0), p, font=font)[0]
            <= max_width
            for p in parts
        )
        if all_fit:
            return parts

    # Word-wrap
    words = title.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [title]


def _apply_gradient_band(
    image: Image.Image,
    center_y: int,
    half_height: int,
    width: int,
    height: int,
    max_alpha: int = 100,
) -> None:
    """Apply a soft gradient band (cosine falloff) behind text."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    for dy in range(-half_height, half_height + 1):
        progress = abs(dy) / half_height if half_height > 0 else 1.0
        alpha = int(max_alpha * (0.5 + 0.5 * math.cos(progress * math.pi)))
        row_y = center_y + dy
        if 0 <= row_y < height:
            overlay_draw.line([(0, row_y), (width, row_y)], fill=(0, 0, 0, alpha))

    image_rgba = image.convert("RGBA")
    composited = Image.alpha_composite(image_rgba, overlay)
    image.paste(composited.convert("RGB"))


def _add_attribution(image: Image.Image, width: int, height: int) -> None:
    """Add OSM attribution text in bottom-right corner."""
    draw = ImageDraw.Draw(image)
    font_size = max(10, int(height * 0.015))
    font = _get_font(font_size)
    bbox = draw.textbbox((0, 0), _OSM_ATTRIBUTION, font=font)
    text_w = bbox[2] - bbox[0]
    x = width - text_w - 10
    y = height - font_size - 8
    draw.text((x, y), _OSM_ATTRIBUTION, fill=(200, 200, 200), font=font)


_montserrat_checked = False


def _ensure_montserrat() -> bool:
    """Download Montserrat if not already cached. Returns True if available."""
    global _montserrat_checked  # noqa: PLW0603
    if _montserrat_checked:
        return True

    from pathlib import Path

    cache_dir = Path.home() / ".immich-memories" / "fonts" / "Montserrat"
    if (cache_dir / "Montserrat-Bold.ttf").exists():
        _montserrat_checked = True
        return True

    try:
        from immich_memories.titles.fonts import download_font

        ok = download_font("Montserrat")
        _montserrat_checked = ok
        return ok
    except Exception:
        logger.warning("Could not auto-download Montserrat font")
        return False


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font at the given size, preferring Montserrat (OFL)."""
    from pathlib import Path

    _ensure_montserrat()

    # Prefer Montserrat from our font cache (open source, OFL)
    cache_dir = Path.home() / ".immich-memories" / "fonts" / "Montserrat"
    montserrat = cache_dir / ("Montserrat-Bold.ttf" if bold else "Montserrat-Regular.ttf")
    if montserrat.exists():
        with contextlib.suppress(OSError):
            return ImageFont.truetype(str(montserrat), size)

    # System fallbacks
    fallbacks = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in fallbacks:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_w: int,
) -> list[str]:
    """Word-wrap text to fit within max_w pixels."""
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        if all(draw.textbbox((0, 0), p, font=font)[2] <= max_w for p in parts):
            return parts
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] > max_w and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


def _draw_gradient_band(draw: ImageDraw.ImageDraw, y: int, bh: int, w: int, h: int) -> None:
    """Soft dark gradient band for text readability."""
    cy, half = y + bh // 2, bh // 2
    for dy in range(-half, half + 1):
        row = cy + dy
        if 0 <= row < h:
            a = int(100 * (1 - (abs(dy) / max(1, half)) ** 2))
            draw.line([(0, row), (w, row)], fill=(0, 0, 0, a))


def _overlay_composite(frame: Image.Image, ov: Image.Image, alpha: float) -> Image.Image:
    """Composite RGBA overlay onto RGB frame with given opacity."""
    import numpy as np

    if alpha >= 0.99:
        return Image.alpha_composite(frame.convert("RGBA"), ov).convert("RGB")
    arr = np.array(ov.copy())
    arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * alpha).astype(np.uint8)
    return Image.alpha_composite(
        frame.convert("RGBA"),
        Image.fromarray(arr, "RGBA"),
    ).convert("RGB")
