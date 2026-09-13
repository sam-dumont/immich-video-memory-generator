"""The live detail of a cut in progress: the pictures, the bar, and the lines.

The five phase rows say where a cut is. On a first cut over a large library one
of those rows owns the screen for a long time, and a count that only ever goes
up is not evidence that anything is happening. These three widgets are what a
watcher gets instead: the pictures the pass has just finished, a real bar for
the stage that reports numbers, and -- folded away, because the five rows stay
the default view -- the engine's own stage lines as they arrive.

Everything here is bounded by construction. The strip is a fixed ring of slots,
the log is a fixed number of labels, and both are fed from one small file the
page already polls, so a cut over ten thousand pictures costs a watcher what a
cut over ten costs.
"""

from __future__ import annotations

import base64
import io
from collections import deque
from collections.abc import Callable, Sequence

from nicegui import ui

from immich_memories.operations.cut_progress import StageUpdate

STRIP_SLOTS = 12
STRIP_THUMBNAIL_PX = 96

# How many pictures may arrive per tick. A batch can finish hundreds between
# polls, and the strip only ever shows the newest handful anyway; drawing a few
# per second keeps it moving without pushing a megabyte of base64 a second down
# a websocket that is also carrying the rest of the page.
STRIP_ARRIVALS_PER_TICK = 4

STAGE_LOG_LINES = 40


class PictureRing:
    """Which slot each arriving picture takes, the oldest replaced first.

    Fixed slots rather than a growing list: a stage that runs for an hour must
    leave the page exactly as heavy as it found it, and a picture already on
    screen is never redrawn.
    """

    def __init__(self, size: int = STRIP_SLOTS) -> None:
        self._shown: list[str | None] = [None] * size
        self._next = 0

    def admit(self, arriving: Sequence[str], *, limit: int) -> list[tuple[int, str]]:
        """The (slot, asset id) pairs to draw for this tick, oldest slot first."""
        seen: set[str | None] = set(self._shown)
        fresh: list[str] = []
        for asset_id in arriving:
            if asset_id and asset_id not in seen:
                seen.add(asset_id)
                fresh.append(asset_id)
        placed: list[tuple[int, str]] = []
        for asset_id in fresh[-limit:]:
            placed.append((self._next, asset_id))
            self._shown[self._next] = asset_id
            self._next = (self._next + 1) % len(self._shown)
        return placed


def strip_thumbnail(payload: bytes | None) -> str | None:
    """A cached library preview as a strip-sized data URI, or None if unusable.

    The cache holds Immich's full preview, which is hundreds of kilobytes. At a
    few of those a second the websocket, not the disk, is the cost, so the strip
    sends postage stamps.
    """
    if not payload:
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            image = opened.convert("RGB")
            image.thumbnail((STRIP_THUMBNAIL_PX, STRIP_THUMBNAIL_PX))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=70)
    except Exception:  # WHY: UI graceful degradation
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def remember_stage_line(lines: list[str], line: str, *, limit: int = STAGE_LOG_LINES) -> bool:
    """Keep a stage string unless it repeats the one before it; report whether it was new."""
    if not line or (lines and lines[-1] == line):
        return False
    lines.append(line)
    del lines[:-limit]
    return True


class LiveStrip:
    """The pictures the cut has most recently finished, as they go past."""

    def __init__(
        self, read_thumbnail: Callable[[str], bytes | None], slots: int = STRIP_SLOTS
    ) -> None:
        self._read = read_thumbnail
        self._ring = PictureRing(slots)
        self._cells: list[ui.image] = []
        with ui.row().classes("gap-1 mb-3 flex-wrap items-center"):
            for _ in range(slots):
                cell = ui.image().classes("w-16 h-16 rounded object-cover")
                cell.set_visibility(False)
                self._cells.append(cell)

    def show(self, asset_ids: Sequence[str]) -> None:
        for index, asset_id in self._ring.admit(asset_ids, limit=STRIP_ARRIVALS_PER_TICK):
            # A slot the ring has handed out stays spent even when the picture
            # cannot be read, so an unreadable thumbnail is skipped once rather
            # than retried on every tick for as long as the stage runs.
            uri = strip_thumbnail(self._read(asset_id))
            if uri is None:
                continue
            self._cells[index].set_source(uri)
            self._cells[index].set_visibility(True)


class StageBar:
    """A determinate bar for the stage that reports numbers, hidden for the ones that do not."""

    def __init__(self) -> None:
        self._bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        self._caption = ui.label("").classes("text-xs").style("color: var(--im-text-secondary)")
        self._visible(False)

    def _visible(self, shown: bool) -> None:
        self._bar.set_visibility(shown)
        self._caption.set_visibility(shown)

    def show(self, progress: StageUpdate | None) -> None:
        fraction = progress.fraction if progress is not None else None
        if progress is None or fraction is None:
            self._visible(False)
            return
        self._bar.value = fraction
        self._caption.set_text(
            f"{progress.label} {progress.done or 0:,} of {progress.total or 0:,}"
        )
        self._visible(True)


class StageLog:
    """The engine's own stage lines, newest last, folded away by default.

    The lines live on the session rather than in this widget, so a reload during
    a cut rebuilds the panel with what the session has already seen -- the same
    way the phase rows rebuild from the attempt on disk.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._labels: deque[ui.label] = deque()
        with ui.expansion("Details", icon="list").classes("w-full mb-2"):
            self._body = ui.column().classes("gap-0 w-full")
        for line in lines:
            self._draw(line)

    def remember(self, line: str) -> None:
        if remember_stage_line(self._lines, line):
            self._draw(line)

    def _draw(self, line: str) -> None:
        with self._body:
            self._labels.append(
                ui.label(line).classes("text-xs").style("color: var(--im-text-secondary)")
            )
        while len(self._labels) > STAGE_LOG_LINES:
            self._labels.popleft().delete()
