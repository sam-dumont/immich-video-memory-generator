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

# 0xBF is 75% of full range: HLG graphics white. Measured, plain white@0.85
# draws at 872/1023 in a 10-bit pipe, which glares above the picture's own
# diffuse white; this lands at 721.
_HDR_COLOUR = "0xBFBFBF"
_SDR_COLOUR = "white@0.85"


def caption_for(clip: Any) -> str:
    """The clip's capture date, or empty when it does not carry a usable one."""
    raw = getattr(clip, "date", None)
    if not raw:
        return ""
    try:
        taken = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return ""
    return f"{taken.day} {_MONTHS[taken.month - 1]} {taken.year}"


def caption_filter(text: str, width: int, height: int, *, is_hdr: bool = False) -> str:
    """An FFmpeg drawtext filter placing ``text`` in the bottom-right corner.

    Sized and inset off the short side so the caption holds its proportions
    across output resolutions, with a drop shadow that keeps it legible over a
    bright sky.
    """
    short_side = min(width, height)
    font_size = max(12, round(short_side * _FONT_RATIO))
    margin = round(short_side * _MARGIN_RATIO)
    colour = _HDR_COLOUR if is_hdr else _SDR_COLOUR
    return (
        f"drawtext=text='{text}'"
        f":fontsize={font_size}"
        f":fontcolor={colour}"
        f":shadowcolor=black@0.6:shadowx=2:shadowy=2"
        f":x=w-tw-{margin}:y=h-th-{margin}"
    )
