"""Where a title breaks into lines.

A title is broken at spaces, and between the characters of a script that
writes without them (Chinese, Japanese), never before punctuation. A lone
separator ("·", "→") stays at the end of the line it follows and a bare year
stays with the word before it ("ÉTÉ 2025"), so neither opens a line. The
lines are then balanced: as many as greedy filling needs, but as even as they
can be, so no single word is left alone on the last line.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

# A separator the title template puts between two parts, written as its own word.
_SEPARATORS = frozenset("·・→–—|")
_BALANCE_STEPS = 12


def _no_space_script(char: str) -> bool:
    return unicodedata.east_asian_width(char) in ("W", "F") and char.isalpha()


def _sticks_to_previous(char: str) -> bool:
    """Punctuation and length marks never begin a line."""
    return unicodedata.category(char)[0] == "P" or unicodedata.category(char) == "Lm"


def _pieces(word: str) -> list[str]:
    """A word cut where a line may break inside it: between CJK characters."""
    pieces: list[str] = []
    for char in word:
        breakable = pieces and _no_space_script(char) and _no_space_script(pieces[-1][-1])
        if breakable and not _sticks_to_previous(char):
            pieces.append(char)
        elif pieces:
            pieces[-1] += char
        else:
            pieces.append(char)
    return pieces


def _units(text: str, too_wide: Callable[[str], bool]) -> list[tuple[str, bool]]:
    """(piece, whether a space comes before it) for every place a line may break.

    Spaces first; a word is cut between its CJK characters only when it is
    wider than a line on its own, so "イタリア" is not split while it fits.
    """
    units: list[tuple[str, bool]] = []
    for word in text.split():
        glue = word in _SEPARATORS or word.isdigit()
        if units and glue:
            piece, spaced = units[-1]
            units[-1] = (f"{piece} {word}", spaced)
            continue
        pieces = _pieces(word) if too_wide(word) else [word]
        units.extend((piece, i == 0 and bool(units)) for i, piece in enumerate(pieces))
    return units


def _fill(units: list[tuple[str, bool]], measure: Callable[[str], float], limit: float):
    lines: list[str] = []
    current = ""
    for piece, spaced in units:
        candidate = f"{current} {piece}" if spaced and current else current + piece
        if current and measure(candidate) > limit:
            lines.append(current)
            current = piece
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def wrap_lines(text: str, font, max_width: float) -> list[str]:
    """`text` broken into the fewest, most even lines no wider than `max_width`.

    A piece wider than `max_width` on its own (one long word) still gets a
    line of its own; fitting it is the font size's job.
    """

    def measure(line: str) -> float:
        left, _, right, _ = font.getbbox(line)
        return right - left

    if measure(text) <= max_width:
        return [text]
    if "," in text:
        head, tail = (p.strip() for p in text.split(",", 1))
        parts = [f"{head},", tail]
        if all(measure(p) <= max_width for p in parts):
            return parts
    units = _units(text, lambda word: measure(word) > max_width)
    lines = _fill(units, measure, max_width)
    low, high = max_width / 2, max_width
    for _ in range(_BALANCE_STEPS):
        middle = (low + high) / 2
        if len(_fill(units, measure, middle)) == len(lines):
            high = middle
        else:
            low = middle
    return _fill(units, measure, high) or [text]


def fits(lines: list[str], font, max_width: float) -> bool:
    """Whether every line is inside `max_width`."""
    return all(font.getbbox(line)[2] - font.getbbox(line)[0] <= max_width for line in lines)
