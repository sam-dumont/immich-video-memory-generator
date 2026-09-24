"""Captions drawtext cannot draw: rendered with the title fonts, laid over the frame.

drawtext takes one font file and no font fallback, and whether it shapes
Arabic or orders Hebrew depends on how FFmpeg was built (FFmpeg 8.1 without
FriBiDi joins Arabic letters but lays the words out left to right). A caption
the caption font draws whole keeps drawtext. Any other caption (a Greek or
Japanese place, anything right to left) is drawn once with the title text
stack (`font_chain`: Noto fallback letter by letter, Raqm shaping) into a
small transparent PNG, and FFmpeg's `overlay` puts it where drawtext would
have drawn the text.
"""

from __future__ import annotations

import hashlib
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from immich_memories.titles.font_chain import face_covers, title_font

_RTL = frozenset({"R", "AL"})


def needs_image(text: str, font_path: str | None) -> bool:
    """Whether drawtext with `font_path` would draw this wrong: a missing letter, or RTL text."""
    if not text or font_path is None or not Path(font_path).is_file():
        return False
    if any(unicodedata.bidirectional(c) in _RTL for c in text):
        return True
    return not face_covers(font_path, text)


@dataclass(frozen=True)
class CaptionStyle:
    """What drawtext is told, so the image looks the same."""

    font_path: str
    font_size: int
    colour: tuple[int, int, int]
    border: int
    shadow: int


def render_caption(text: str, style: CaptionStyle) -> tuple[Path, int, int]:
    """A PNG of the caption and the offset of its top-left from the text origin.

    The origin is where drawtext's x/y would put the text; the image carries its
    outline and shadow, which reach past it on every side.
    """
    font = title_font(style.font_path, style.font_size, bold=True)
    pad = style.border + style.shadow + 2
    left, top, right, bottom = font.getbbox(text, stroke_width=style.border)
    size = (int(right - min(left, 0)) + 2 * pad, int(bottom - min(top, 0)) + 2 * pad)
    origin = (pad - min(left, 0), pad - min(top, 0))

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(
        (origin[0] + style.shadow, origin[1] + style.shadow),
        text,
        font=font,
        fill=(0, 0, 0, round(255 * 0.35)),
    )
    body = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(body).text(
        origin,
        text,
        font=font,
        fill=(*style.colour, round(255 * 0.85)),
        stroke_width=style.border,
        stroke_fill=(0, 0, 0, round(255 * 0.45)),
    )
    image = Image.alpha_composite(shadow, body)

    key = hashlib.sha256(repr((text, style)).encode()).hexdigest()[:24]
    folder = Path(tempfile.gettempdir()) / "immich-memories-captions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key}.png"
    if not path.exists():
        image.save(path)
    return path, -int(origin[0]), -int(origin[1])


def text_width(text: str, style: CaptionStyle) -> int:
    """How wide the caption's letters are, as drawtext's `tw` would report."""
    font = title_font(style.font_path, style.font_size, bold=True)
    return round(font.getlength(text))
