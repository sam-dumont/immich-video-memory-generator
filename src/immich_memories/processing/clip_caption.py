"""The per-clip date caption: what it says and how it is drawn.

Kept apart from the assembler because none of it depends on decoding — it is
text and geometry, and it is worth testing without spinning up FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

# Spelled out rather than left to strftime so the caption does not change with
# the machine's locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Caption metrics as a share of the frame's short side, so the date reads the
# same on a 4K memory and a 720p one.
_FONT_RATIO = 0.040
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


@dataclass(frozen=True)
class ClipCaption:
    """What one clip shows: a place (left) and a date (right), either empty."""

    place: str = ""
    date: str = ""

    def __bool__(self) -> bool:
        return bool(self.place or self.date)


def captions_for_timeline(clips: list[Any], *, place: bool = False) -> list[ClipCaption]:
    """Captions for a clip sequence, shown-place deduplicated.

    The interesting information in a place caption is the CHANGE of place, so
    it appears when it differs from the last known place and stays silent while
    the location holds. A clip without EXIF place does not reset the run —
    gaps are common inside one event, and repeating "Nice, France" after every
    gap would caption almost every clip.
    """
    dates = [_parsed_date(clip) for clip in clips]
    known = [d for d in dates if d is not None]
    # The span of the CONTENT is the period the viewer already knows: a video
    # of one August needs no "Aug 2025" on every clip, only the day.
    same_month = bool(known) and len({(d.year, d.month) for d in known}) == 1
    same_year = bool(known) and len({d.year for d in known}) == 1

    captions: list[ClipCaption] = []
    last_place: str | None = None
    for clip, taken in zip(clips, dates, strict=True):
        shown_place = ""
        if place:
            where = getattr(clip, "location_name", None)
            if where and str(where) != last_place:
                shown_place = str(where)
                last_place = shown_place
        captions.append(ClipCaption(place=shown_place, date=_worded(taken, same_month, same_year)))
    return captions


def _worded(taken: date | None, same_month: bool, same_year: bool) -> str:
    if taken is None:
        return ""
    if same_month:
        return f"{_WEEKDAYS[taken.weekday()]} {taken.day}"
    if same_year:
        return f"{taken.day} {_MONTHS[taken.month - 1]}"
    return f"{taken.day} {_MONTHS[taken.month - 1]} {taken.year}"


def _parsed_date(clip: Any) -> date | None:
    raw = getattr(clip, "date", None)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


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


def caption_filters(
    caption: ClipCaption,
    width: int,
    height: int,
    *,
    is_hdr: bool = False,
    font_path: str | None = None,
) -> list[str]:
    """FFmpeg drawtext filters: the place bottom-left, the date bottom-right.

    Sized and inset off the short side so captions hold their proportions
    across output resolutions. A translucent box scrim plus drop shadow keeps
    the text legible over bright content — a white date on sunlit skin is
    invisible with a shadow alone. Vertical output insets further from the
    bottom, where the platforms draw their own UI.
    """
    short_side = min(width, height)
    font_size = max(12, round(short_side * _FONT_RATIO))
    margin = round(short_side * _MARGIN_RATIO)
    bottom = round(height * _VERTICAL_BOTTOM_RATIO) if height > width else margin
    colour = _HDR_COLOUR if is_hdr else _SDR_COLOUR
    # WHY a box over a heavier shadow: measured on the matrix renders, a 2px
    # shadow disappears against skin tones and sand; a soft scrim never does.
    common = (
        f":fontsize={font_size}"
        f":fontcolor={colour}"
        f":shadowcolor=black@0.6:shadowx=2:shadowy=2"
        f":box=1:boxcolor=black@0.3:boxborderw={max(4, round(font_size * 0.35))}"
        f":y=h-th-{bottom}"
    )
    if font_path:
        common = f":fontfile='{font_path}'" + common
    filters = []
    if caption.place:
        filters.append(f"drawtext=text='{_escape(caption.place)}'{common}:x={margin}")
    if caption.date:
        filters.append(f"drawtext=text='{_escape(caption.date)}'{common}:x=w-tw-{margin}")
    return filters


def caption_font_path() -> str | None:
    """The captions' font file: Outfit Regular, the flagship title family.

    One face for every caption rather than chasing the run's title style —
    consistent product typography, and the bundled file needs no network.
    None falls back to drawtext's default rather than failing the render.
    """
    from immich_memories.titles.fonts import get_font_path

    path = get_font_path("Outfit", "Regular")
    return str(path) if path else None


def timeline_captions(
    clips: list, date_overlay: bool, place_overlay: bool
) -> tuple[list[ClipCaption] | None, str | None]:
    """Captions for the whole sequence, or nothing when neither flag is set.

    Timeline-aware (place dedupe, span-relative dates), so computed once for
    the sequence rather than per decoder.
    """
    if not (date_overlay or place_overlay):
        return None, None
    captions = captions_for_timeline(clips, place=place_overlay)
    if not date_overlay:
        captions = [ClipCaption(place=c.place) for c in captions]
    return captions, caption_font_path()
