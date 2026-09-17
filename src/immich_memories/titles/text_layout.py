"""Where a title's two text blocks sit, and whether they would collide.

Both blocks are rasterized centred on the full frame, so their position is a
vertical shift applied when the layer is composited. That shift has to come from
the blocks' wrapped heights: a constant one is only ever right for the line
counts it was measured against, and the two blocks land on top of each other as
soon as either of them wraps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from immich_memories.titles.safe_zones import safe_text_height

LINE_HEIGHT_FACTOR = 1.2
"""What one wrapped line costs in font heights, as the layers draw it."""

DEFAULT_GAP_RATIO = 0.25
"""Ink gap between the two blocks, in title font heights.

Derived from the spacing this replaces. The old rule pushed the subtitle layer
down by ``1.3 * title_size`` from a frame-centred layer, so with one line each
the two block centres sat ``1.3 * title_size`` apart. Here that distance is
``gap + (title_height + subtitle_height) / 2``, and one line is
``LINE_HEIGHT_FACTOR * size`` tall, which leaves

    gap = 1.3 * title - 0.6 * title - 0.6 * subtitle

A title screen draws its subtitle at 0.75 of the title size
(``TitleStyle.subtitle_size_ratio``), so that is ``0.25 * title``.
"""

MIN_TEXT_SIZE_RATIO = 0.02
"""Smallest a shrinking font may get, in frame heights."""

_SHRINK_STEP = 0.9
_MAX_SHRINK_STEPS = 8


def block_height(lines: int, font_size: int) -> float:
    """How tall a wrapped block of this many lines is drawn."""
    return lines * LINE_HEIGHT_FACTOR * font_size


@dataclass(frozen=True)
class TextStack:
    """Two frame-centred text layers and the shift each one needs."""

    title_size: int
    subtitle_size: int
    title_lines: int
    subtitle_lines: int
    title_shift: float
    subtitle_shift: float

    @property
    def title_height(self) -> float:
        return block_height(self.title_lines, self.title_size)

    @property
    def subtitle_height(self) -> float:
        return block_height(self.subtitle_lines, self.subtitle_size)

    @property
    def top(self) -> float:
        """Top of the pair, relative to the centre of the frame."""
        return self.title_shift - self.title_height / 2

    @property
    def bottom(self) -> float:
        """Bottom of the pair, relative to the centre of the frame."""
        if not self.subtitle_lines:
            return self.title_shift + self.title_height / 2
        return self.subtitle_shift + self.subtitle_height / 2


def min_text_size(frame_height: int) -> int:
    """Smallest font a frame this tall may still shrink text to."""
    return max(1, int(frame_height * MIN_TEXT_SIZE_RATIO))


def shrink_sizes(title_size: int, subtitle_size: int, frame_height: int) -> tuple[int, int] | None:
    """Both sizes one step smaller, or None once that would stop being readable."""
    smaller = (int(title_size * _SHRINK_STEP), int(subtitle_size * _SHRINK_STEP))
    if min(smaller) < min_text_size(frame_height):
        return None
    return smaller


def stack_text_blocks(
    title: str,
    subtitle: str | None,
    title_size: int,
    subtitle_size: int,
    frame_height: int,
    count_lines: Callable[[str, int], int],
    gap_ratio: float = DEFAULT_GAP_RATIO,
) -> TextStack:
    """Place the title above the subtitle, the pair centred on the frame.

    ``count_lines`` measures a string the way the caller will draw it, so the
    layout sees the real line counts. A pair taller than the frame's safe
    height shrinks both fonts a step at a time and re-wraps, down to the
    smallest size still worth reading.
    """
    if not subtitle:
        lines = count_lines(title, title_size)
        return TextStack(title_size, subtitle_size, lines, 0, 0.0, 0.0)

    safe_height = safe_text_height(frame_height)
    stack = _stack_at(title, subtitle, title_size, subtitle_size, count_lines, gap_ratio)
    for _ in range(_MAX_SHRINK_STEPS):
        if stack.bottom - stack.top <= safe_height:
            break
        smaller = shrink_sizes(stack.title_size, stack.subtitle_size, frame_height)
        if smaller is None:
            break
        stack = _stack_at(title, subtitle, smaller[0], smaller[1], count_lines, gap_ratio)
    return stack


def _stack_at(
    title: str,
    subtitle: str,
    title_size: int,
    subtitle_size: int,
    count_lines: Callable[[str, int], int],
    gap_ratio: float,
) -> TextStack:
    title_lines = count_lines(title, title_size)
    subtitle_lines = count_lines(subtitle, subtitle_size)
    gap = gap_ratio * title_size
    title_height = block_height(title_lines, title_size)
    subtitle_height = block_height(subtitle_lines, subtitle_size)
    return TextStack(
        title_size=title_size,
        subtitle_size=subtitle_size,
        title_lines=title_lines,
        subtitle_lines=subtitle_lines,
        title_shift=-(gap + subtitle_height) / 2,
        subtitle_shift=(gap + title_height) / 2,
    )


def text_blocks_overlap(
    title_layer: np.ndarray,
    subtitle_layer: np.ndarray | None,
    title_shift: float,
    subtitle_shift: float,
) -> bool:
    """Whether the two layers, once shifted, share a row of ink.

    Reads the rasterized layers rather than the planned heights, so a face that
    draws taller than the layout assumed is caught rather than trusted.
    """
    if subtitle_layer is None:
        return False
    title_rows = _ink_rows(title_layer)
    subtitle_rows = _ink_rows(subtitle_layer)
    if title_rows is None or subtitle_rows is None:
        return False
    title_top, title_bottom = (row + title_shift for row in title_rows)
    subtitle_top, subtitle_bottom = (row + subtitle_shift for row in subtitle_rows)
    return title_top <= subtitle_bottom and subtitle_top <= title_bottom


def _ink_rows(layer: np.ndarray) -> tuple[int, int] | None:
    """First and last row of an RGBA layer carrying any ink at all."""
    rows = np.flatnonzero(np.any(layer[:, :, 3] > 0.0, axis=1))
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1])
