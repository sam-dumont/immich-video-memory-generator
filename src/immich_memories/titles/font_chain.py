"""A title font that draws every script it can find a face for.

`title_font(path, size, bold=...)` returns a Pillow font every ImageDraw call
already takes. Text the family's own face draws alone goes straight through
Pillow, so a Latin title is the same pixels it always was. Text with letters
the face lacks is cut into runs, each drawn in the first face of a chain that
has them: the family's own extra subsets, Noto Sans (bundled: Latin, Greek,
Cyrillic, Vietnamese), then the Noto script faces `titles fonts --install`
put on disk. A word never switches face halfway through.

Right-to-left and complex scripts (Arabic, Hebrew, the Indic scripts) are
shaped by Pillow's Raqm layout, which is HarfBuzz plus FriBiDi. The runs
themselves are put in visual order here, so a Hebrew name inside a French
title lands where a reader expects it.
"""

from __future__ import annotations

import functools
import logging
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont, features

from immich_memories.titles.fonts import BUNDLED_FONTS_DIR
from immich_memories.titles.script_fonts import installed_script_fonts

logger = logging.getLogger(__name__)

_NOTO_CORE = {
    False: BUNDLED_FONTS_DIR / "noto-sans" / "core-400-normal.ttf",
    True: BUNDLED_FONTS_DIR / "noto-sans" / "core-700-normal.ttf",
}
# Fontsource cuts a family into subsets; the extra ones sit next to latin-<w>.
_SIBLING_SUBSETS = ("latin-ext", "vietnamese")
_RTL_CLASSES = frozenset({"R", "AL"})
_NEUTRAL_CLASSES = frozenset({"WS", "ON", "CS", "ES", "ET", "BN", "S", "B", "EN", "AN"})
# Noto Sans CJK's collection: 0 JP, 1 KR, 2 SC, 3 TC.
_CJK_JP, _CJK_KR, _CJK_SC = 0, 1, 2

_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


@dataclass(frozen=True)
class _Face:
    path: str
    index: int = 0


@dataclass(frozen=True)
class _Run:
    text: str
    face: int
    rtl: bool


@functools.lru_cache(maxsize=64)
def _codepoints(path: str, index: int = 0) -> frozenset[int]:
    """Every character this face has a glyph for; empty when it cannot be read."""
    import freetype

    try:
        face = freetype.Face(path, index)
    except (freetype.FT_Exception, OSError):
        return frozenset()
    return frozenset(code for code, _glyph in face.get_chars())


def _covers(face: _Face, char: str) -> bool:
    return ord(char) in _codepoints(face.path, face.index)


# Scripts whose letters join, reorder or stack into clusters: without Raqm
# they draw wrong. CJK, Hangul, Latin, Greek and Cyrillic draw the same either way.
_SHAPED_SCRIPTS = (
    "ARABIC", "SYRIAC", "THAANA", "NKO", "HEBREW", "DEVANAGARI", "BENGALI", "GURMUKHI",
    "GUJARATI", "ORIYA", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM", "SINHALA", "THAI",
    "LAO", "TIBETAN", "MYANMAR", "KHMER",
)  # fmt: skip


def needs_shaping(text: str) -> bool:
    """Whether `text` holds a letter of a script that only draws right when shaped."""
    return any(unicodedata.name(char, "").startswith(_SHAPED_SCRIPTS) for char in text)


def shaping_hint() -> str:
    """What to do on this system so Pillow shapes text."""
    if sys.platform == "darwin":
        # WHY the library path: dlopen does not search Homebrew's lib folder, so
        # a brew-installed FriBiDi stays invisible to Pillow without it.
        return (
            "brew install fribidi, then start immich-memories with "
            "DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix)/lib"
        )
    return "install FriBiDi (libfribidi0) to shape it"


def raqm_available() -> bool:
    """Whether this Pillow shapes text (HarfBuzz) and orders it (FriBiDi)."""
    return bool(features.check_feature("raqm"))


def _sibling_subsets(primary: Path) -> list[_Face]:
    stem = primary.name
    if not stem.startswith("latin-"):
        return []
    weight = stem.removeprefix("latin-")
    siblings = (primary.with_name(f"{subset}-{weight}") for subset in _SIBLING_SUBSETS)
    return [_Face(str(path)) for path in siblings if path.is_file()]


def _cjk_index(text: str) -> int:
    """Which face of the CJK collection: Korean or Japanese when the text says so."""
    if any("가" <= c <= "힯" or "ᄀ" <= c <= "ᇿ" for c in text):
        return _CJK_KR
    if any("぀" <= c <= "ヿ" for c in text):
        return _CJK_JP
    return _CJK_SC


def _chain(primary: str, bold: bool, text: str) -> tuple[_Face, ...]:
    faces = [_Face(primary), *_sibling_subsets(Path(primary)), _Face(str(_NOTO_CORE[bold]))]
    for path in installed_script_fonts(bold=bold):
        index = _cjk_index(text) if path.suffix == ".ttc" else 0
        faces.append(_Face(str(path), index))
    return tuple(faces)


def _first_covering(faces: tuple[_Face, ...], char: str) -> int | None:
    return next((i for i, face in enumerate(faces) if _covers(face, char)), None)


def _is_neutral(char: str) -> bool:
    return char.isspace() or unicodedata.bidirectional(char) in _NEUTRAL_CLASSES


def _assign_faces(text: str, faces: tuple[_Face, ...]) -> list[int | None]:
    """A face per letter; None for spaces and punctuation, which follow their neighbours."""
    assigned: list[int | None] = []
    for char in text:
        if unicodedata.category(char).startswith("M") and assigned:
            assigned.append(assigned[-1])
        elif _is_neutral(char):
            assigned.append(None)
        else:
            assigned.append(_first_covering(faces, char))
    return assigned


def _family(faces: tuple[_Face, ...], face: int) -> int:
    """Faces from the primary's own folder are one family: its Latin, Latin Ext, Vietnamese."""
    return 0 if Path(faces[face].path).parent == Path(faces[0].path).parent else face


def _unify_words(text: str, faces: tuple[_Face, ...], assigned: list[int | None]) -> None:
    """A word drawn in two typefaces reads as a typo: give it the first face that has it all."""
    start = 0
    for end in (*(i for i, c in enumerate(text) if c.isspace()), len(text)):
        word = [i for i in range(start, end) if assigned[i] is not None]
        if len({_family(faces, cast(int, assigned[i])) for i in word}) > 1:
            letters = [text[i] for i in word]
            whole = next(
                (f for f, face in enumerate(faces) if all(_covers(face, c) for c in letters)),
                None,
            )
            for i in word if whole is not None else []:
                assigned[i] = whole
        start = end + 1


def _resolve_neutrals(text: str, faces: tuple[_Face, ...], assigned: list[int | None]) -> list[int]:
    resolved: list[int] = []
    for i, char in enumerate(text):
        face = assigned[i]
        if face is None:
            before = resolved[-1] if resolved else None
            after = next((f for f in assigned[i + 1 :] if f is not None), None)
            face = next(
                (f for f in (before, after) if f is not None and _covers(faces[f], char)),
                _first_covering(faces, char),
            )
        resolved.append(0 if face is None else face)
    return resolved


def _logical_runs(text: str, faces: tuple[_Face, ...]) -> list[_Run]:
    assigned = _assign_faces(text, faces)
    _unify_words(text, faces, assigned)
    resolved = _resolve_neutrals(text, faces, assigned)
    runs: list[_Run] = []
    chars: list[str] = []
    rtl: bool | None = None
    for i, char in enumerate(text):
        strong = unicodedata.bidirectional(char)
        char_rtl = strong in _RTL_CLASSES if strong in ("L", "R", "AL") else None
        switch_face = bool(chars) and resolved[i] != resolved[i - 1]
        switch_dir = char_rtl is not None and rtl is not None and char_rtl != rtl
        if switch_face or switch_dir:
            runs.append(_Run("".join(chars), resolved[i - 1], bool(rtl)))
            chars, rtl = [], None
        chars.append(char)
        rtl = char_rtl if char_rtl is not None else rtl
    if chars:
        runs.append(_Run("".join(chars), resolved[-1], bool(rtl)))
    return runs


def visual_order(runs: list[_Run]) -> list[_Run]:
    """Runs left to right on screen (the Unicode bidi rule L2, at run granularity).

    The paragraph takes the direction of its first strong letter; every run of
    the other direction is embedded one level up, and each level is reversed.
    """
    base = 1 if runs and runs[0].rtl else 0
    levels = [(base if run.rtl == bool(base) else base + 1) for run in runs]
    order = list(range(len(runs)))
    for level in range(max(levels, default=0), 0, -1):
        i = 0
        while i < len(order):
            if levels[order[i]] >= level:
                j = i
                while j < len(order) and levels[order[j]] >= level:
                    j += 1
                order = [*order[:i], *reversed(order[i:j]), *order[j:]]
                i = j
            else:
                i += 1
    return [runs[i] for i in order]


def _anchor_origin(anchor: str | None, advance: float, ascent: int, descent: int):
    horizontal, vertical = (anchor or "la")[0], (anchor or "la")[1]
    ox = {"l": 0.0, "m": advance / 2, "r": advance}.get(horizontal, 0.0)
    # descent is negative in Pillow's metrics; y grows downwards on the image.
    oy = {
        "a": -ascent,
        "t": -ascent,
        "s": 0,
        "d": -descent,
        "b": -descent,
        "m": (-ascent - descent) / 2,
    }.get(vertical, -ascent)
    return ox, oy


@dataclass(frozen=True)
class _Placed:
    font: ImageFont.FreeTypeFont
    pen: float
    direction: str | None
    text: str


class ChainFont(ImageFont.FreeTypeFont):
    """A FreeTypeFont whose missing letters come from the next face that has them."""

    def __init__(self, font: str, size: float = 10, index: int = 0, *, bold: bool = False):
        super().__init__(font, size, index)
        self._bold = bold
        self._fonts: dict[_Face, ImageFont.FreeTypeFont] = {}

    def _needs_chain(self, text: str) -> bool:
        own = _codepoints(str(self.path), self.index)
        return any(ord(c) not in own and not c.isspace() for c in text)

    def _run_font(self, face: _Face) -> ImageFont.FreeTypeFont:
        if face not in self._fonts:
            self._fonts[face] = ImageFont.truetype(face.path, self.size, index=face.index)
        return self._fonts[face]

    def _placed(self, text: str) -> tuple[list[_Placed], float]:
        if missing := uncovered_letters(text, str(self.path), bold=self._bold):
            _warn_once(
                f"missing:{missing}",
                "No installed font draws %r in %r; run `immich-memories titles fonts "
                "--install` to add the Noto script fonts",
                missing,
                text,
            )
        shaped = raqm_available()
        placed, pen = [], 0.0
        for run in text_runs(text, str(self.path), bold=self._bold):
            primary = run.face == str(self.path)
            font = self._run_font(_Face(run.face, self.index if primary else run.index))
            if not shaped and run.installed and needs_shaping(run.text):
                _warn_once(
                    "raqm",
                    "Pillow has no Raqm layout here, so %r is drawn unshaped (Arabic letters "
                    "unjoined, Indic clusters apart); %s",
                    run.text,
                    shaping_hint(),
                )
            direction, drawn = _direction_for(run, shaped)
            placed.append(_Placed(font, pen, direction, drawn))
            pen += font.getlength(drawn, direction=direction)
        return placed, pen

    def _layout_bbox(self, placed: list[_Placed], stroke_width: float):
        boxes = [
            (
                item.pen,
                item.font.getbbox(
                    item.text, direction=item.direction, stroke_width=stroke_width, anchor="ls"
                ),
            )
            for item in placed
        ]
        return (
            min(pen + box[0] for pen, box in boxes),
            min(box[1] for _pen, box in boxes),
            max(pen + box[2] for pen, box in boxes),
            max(box[3] for _pen, box in boxes),
        )

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        if not isinstance(text, str) or not self._needs_chain(text):
            return super().getlength(text, mode, direction, features, language)
        return self._placed(text)[1]

    def getbbox(
        self,
        text,
        mode="",
        direction=None,
        features=None,
        language=None,
        stroke_width=0,
        anchor=None,
    ):
        if not isinstance(text, str) or not self._needs_chain(text):
            return super().getbbox(text, mode, direction, features, language, stroke_width, anchor)
        placed, advance = self._placed(text)
        x0, y0, x1, y1 = self._layout_bbox(placed, stroke_width)
        ox, oy = _anchor_origin(anchor, advance, *self.getmetrics())
        return (int(x0 - ox), int(y0 - oy), int(x1 - ox), int(y1 - oy))

    def getmask2(
        self,
        text,
        mode="",
        direction=None,
        features=None,
        language=None,
        stroke_width=0,
        anchor=None,
        ink=0,
        start=None,
        *args,
        **kwargs,
    ):
        if mode == "RGBA" or not isinstance(text, str) or not self._needs_chain(text):
            return super().getmask2(
                text,
                mode,
                direction,
                features,
                language,
                stroke_width,
                anchor,
                ink,
                start,
                *args,
                **kwargs,
            )
        placed, advance = self._placed(text)
        x0, y0, x1, y1 = (int(v) for v in self._layout_bbox(placed, stroke_width))
        mask = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 0)
        pen_draw = ImageDraw.Draw(mask)
        for item in placed:
            pen_draw.text(
                (item.pen - x0, -y0),
                item.text,
                fill=255,
                font=item.font,
                anchor="ls",
                direction=item.direction,
                stroke_width=stroke_width,
            )
        ox, oy = _anchor_origin(anchor, advance, *self.getmetrics())
        return mask.im, (int(x0 - ox), int(y0 - oy))


def _uncovered(faces: tuple[_Face, ...], runs: list[_Run]) -> str:
    return "".join(
        dict.fromkeys(
            c
            for run in runs
            for c in run.text
            if not _is_neutral(c)
            and not unicodedata.category(c).startswith("M")
            and not _covers(faces[run.face], c)
        )
    )


@dataclass(frozen=True)
class TextRun:
    """A stretch of text drawn in one face and one direction."""

    text: str
    face: str
    rtl: bool
    index: int = 0
    installed: bool = False


def text_runs(text: str, primary: str | Path, *, bold: bool = False) -> list[TextRun]:
    """How `text` is cut and ordered on screen, left to right, with the face of each piece.

    `installed` marks a face `titles fonts --install` put on disk rather than one
    the wheel carries.
    """
    faces = _chain(str(primary), bold, text)
    bundled = _bundled_count(faces)
    return [
        TextRun(run.text, faces[run.face].path, run.rtl, faces[run.face].index, run.face >= bundled)
        for run in visual_order(_logical_runs(text, faces))
    ]


def _bundled_count(faces: tuple[_Face, ...]) -> int:
    """The primary, its subsets and Noto Sans core come first; installed faces follow."""
    installed = {
        str(path) for weight in (False, True) for path in installed_script_fonts(bold=weight)
    }
    return sum(1 for face in faces if face.path not in installed)


def uncovered_letters(text: str, primary: str | Path, *, bold: bool = False) -> str:
    """The letters of `text` no bundled or installed face has a glyph for."""
    faces = _chain(str(primary), bold, text)
    return _uncovered(faces, _logical_runs(text, faces))


def _direction_for(run: TextRun, shaped: bool) -> tuple[str | None, str]:
    """How Pillow is asked to draw a run, and the string it is handed."""
    if not run.rtl:
        return ("ltr" if shaped else None), run.text
    if shaped:
        return "rtl", run.text
    # WHY reversed: basic layout draws in memory order, and an unjoined Hebrew
    # word read backwards is worse than one drawn right to left.
    return None, run.text[::-1]


def title_font(path: str | Path, size: float, *, bold: bool = False) -> ChainFont:
    """The face at `path`, backed by Noto for every letter it cannot draw itself."""
    return ChainFont(str(path), size, bold=bold)


def face_covers(path: str | Path, text: str) -> bool:
    """Whether the face at `path` alone has a glyph for every letter of `text`."""
    own = _codepoints(str(path))
    return all(c.isspace() or ord(c) in own for c in text)
