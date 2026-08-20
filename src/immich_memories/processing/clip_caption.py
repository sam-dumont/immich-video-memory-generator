"""The per-clip date caption: what it says and how it is drawn.

Kept apart from the assembler because none of it depends on decoding — it is
text and geometry, and it is worth testing without spinning up FFmpeg.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# Spelled out rather than left to strftime so the caption does not change with
# the machine's locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Caption metrics as a share of the frame's short side, so the date reads the
# same on a 4K memory and a 720p one.
_FONT_RATIO = 0.028
_MARGIN_RATIO = 0.04

# Vertical renders go to Reels, Shorts and Stories, which paint captions, the
# handle and an action rail over roughly the bottom sixth of the frame. A
# short-side inset puts the date underneath all of it, so portrait insets off
# the height instead.
_VERTICAL_BOTTOM_RATIO = 0.16

# 0xBF is 75% of full range: HLG graphics white. Measured, plain white@0.85
# draws at 872/1023 in a 10-bit pipe, which glares above the picture's own
# diffuse white; this lands at 721.
_HDR_COLOUR = "0xBFBFBF"
_SDR_COLOUR = "white@0.85"

# Middle dot rather than a comma: place names already contain commas.
_SEPARATOR = " \u00b7 "


def caption_for(clip: Any, *, place: bool = False) -> str:
    """What this clip says on screen: its capture date, optionally its place.

    Empty when the clip carries neither, so the caller can skip drawing rather
    than render an empty box.
    """
    parts = []
    if place:
        where = getattr(clip, "location_name", None)
        if where:
            parts.append(str(where))
    when = _taken_on(clip)
    if when:
        parts.append(when)
    return _SEPARATOR.join(parts)


def _taken_on(clip: Any) -> str:
    raw = getattr(clip, "date", None)
    if not raw:
        return ""
    try:
        taken = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return ""
    return f"{taken.day} {_MONTHS[taken.month - 1]} {taken.year}"


def _escape(text: str) -> str:
    """Escape for drawtext, which parses its own options out of the value.

    A raw colon does not merely look wrong, it fails the filter graph. Backslash
    goes first, or it would double-escape everything added after it.

    The apostrophe is substituted rather than escaped. Measured against a
    `textfile=` reference render, drawtext silently *drops* an ASCII apostrophe
    however it is escaped -- \\', \\\\', quoted or not, all render "LAquila"
    for "L'Aquila" -- so the only forms that reach the screen are a temp file per
    caption or U+2019, which is the correct typographic mark regardless.
    """
    stripped = "".join(c for c in text if c == " " or (ord(c) >= 32 and ord(c) != 127))
    for old, new in (
        ("\\", "\\\\"),
        (":", "\\:"),
        # Measured: drawtext drops an ASCII apostrophe however it is escaped.
        ("'", "’"),
        ("%", "\\%"),
        ("[", "\\["),
        ("]", "\\]"),
        (";", "\\;"),
    ):
        stripped = stripped.replace(old, new)
    return stripped


def caption_filter(text: str, width: int, height: int, *, is_hdr: bool = False) -> str:
    """An FFmpeg drawtext filter placing ``text`` in the bottom-right corner.

    Sized and inset off the short side so the caption holds its proportions
    across output resolutions, with a drop shadow that keeps it legible over a
    bright sky. Vertical output insets further from the bottom, where the
    platforms draw their own UI.
    """
    short_side = min(width, height)
    font_size = max(12, round(short_side * _FONT_RATIO))
    margin = round(short_side * _MARGIN_RATIO)
    bottom = round(height * _VERTICAL_BOTTOM_RATIO) if height > width else margin
    colour = _HDR_COLOUR if is_hdr else _SDR_COLOUR
    return (
        f"drawtext=text='{_escape(text)}'"
        f":fontsize={font_size}"
        f":fontcolor={colour}"
        f":shadowcolor=black@0.6:shadowx=2:shadowy=2"
        f":x=w-tw-{margin}:y=h-th-{bottom}"
    )
