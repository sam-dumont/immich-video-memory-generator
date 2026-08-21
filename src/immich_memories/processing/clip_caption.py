"""The per-clip date caption: what it says and how it is drawn.

Kept apart from the assembler because none of it depends on decoding — it is
text and geometry, and it is worth testing without spinning up FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from immich_memories.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    detect_system_locale,
    get_month_name,
    get_weekday_name,
)


def resolve_caption_locale(value: str | None) -> str:
    """The captions' language: the configured locale, with `auto` following
    the host machine — which, deployed next to Immich, is the server's locale."""
    if value in SUPPORTED_LOCALES:
        return value
    if value == "auto":
        return detect_system_locale()
    return DEFAULT_LOCALE


# Caption metrics as a share of the frame's short side, so the date reads the
# same on a 4K memory and a 720p one. Ratios validated on the 2026-08-21
# proof-sheet review (v4): title-scale bold uppercase, corners hugged the
# same way in both orientations.
_FONT_RATIO = 0.062
_MARGIN_RATIO = 0.055

# 0xBF is 75% of full range: HLG graphics white. Measured, plain white@0.85
# draws at 872/1023 in a 10-bit pipe, which glares above the picture's own
# diffuse white; this lands at 721.
_HDR_COLOUR = "0xBFBFBF"
_SDR_COLOUR = "white"

# Middle dot rather than a comma: place names already contain commas.
_SEPARATOR = " \u00b7 "


@dataclass(frozen=True)
class ClipCaption:
    """What one clip shows: a place (left) and a date (right), either empty."""

    place: str = ""
    date: str = ""

    def __bool__(self) -> bool:
        return bool(self.place or self.date)


def captions_for_timeline(
    clips: list[Any], *, place: bool = False, locale_code: str = "en"
) -> list[ClipCaption]:
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
        captions.append(
            ClipCaption(
                place=shown_place,
                date=_worded(taken, same_month, same_year, locale_code),
            )
        )
    return captions


def _worded(taken: date | None, same_month: bool, same_year: bool, locale_code: str) -> str:
    if taken is None:
        return ""
    if same_month:
        return f"{get_weekday_name(taken.weekday(), locale_code)} {taken.day}"
    if same_year:
        return f"{taken.day} {get_month_name(taken.month, locale_code)}"
    return f"{taken.day} {get_month_name(taken.month, locale_code)} {taken.year}"


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
    """FFmpeg drawtext filters: the place top-left, the date bottom-right.

    Bold uppercase at title scale, hugging the corners the same way in both
    orientations, anchored at constant y so nothing drifts with descenders.
    A heavy dark outline plus shadow keeps white text legible over bright
    content — validated frame-by-frame on light, dark, colorful and busy
    backgrounds in the 2026-08-21 proof-sheet review.
    """
    short_side = min(width, height)
    font_size = max(12, round(short_side * _FONT_RATIO))
    inset = round(short_side * _MARGIN_RATIO)
    line = round(font_size * 1.05)
    colour = _HDR_COLOUR if is_hdr else _SDR_COLOUR
    common = (
        f":fontsize={font_size}"
        f":fontcolor={colour}"
        f":borderw={max(2, font_size // 10)}:bordercolor=black@0.75"
        f":shadowcolor=black@0.6"
        f":shadowx={max(2, font_size // 20)}:shadowy={max(2, font_size // 20)}"
    )
    if font_path:
        common = f":fontfile='{font_path}'" + common
    filters = []
    if caption.place:
        filters.append(
            f"drawtext=text='{_escape(caption.place.upper())}'{common}:x={inset}:y={inset}"
        )
    if caption.date:
        filters.append(
            f"drawtext=text='{_escape(caption.date.upper())}'{common}"
            f":x=w-tw-{inset}:y=h-{inset}-{line}"
        )
    return filters


def caption_font_path() -> str | None:
    """The captions' font file: Outfit SemiBold, the flagship title family.

    One face for every caption rather than chasing the run's title style —
    consistent product typography, and the bundled file needs no network.
    None falls back to drawtext's default rather than failing the render.
    """
    from immich_memories.titles.fonts import get_font_path

    path = get_font_path("Outfit", "SemiBold")
    return str(path) if path else None


def timeline_captions(
    clips: list,
    date_overlay: bool,
    place_overlay: bool,
    locale_code: str = "en",
) -> tuple[list[ClipCaption] | None, str | None]:
    """Captions for the whole sequence, or nothing when neither flag is set.

    Timeline-aware (place dedupe, span-relative dates), so computed once for
    the sequence rather than per decoder.
    """
    if not (date_overlay or place_overlay):
        return None, None
    captions = captions_for_timeline(clips, place=place_overlay, locale_code=locale_code)
    if not date_overlay:
        captions = [ClipCaption(place=c.place) for c in captions]
    return captions, caption_font_path()
