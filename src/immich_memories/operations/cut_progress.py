"""Where a run is, as one record both surfaces read.

A run announces its position as a ``StageUpdate``: the phase it is in, the
stage it is on, and, for a pass that counts its work, how far through. The
attempt stores that record, the page draws a bar from its fraction, and the
terminal sets a real total so its estimate works. Neither side parses the
sentence the other shows.

The snapshot file is a side channel for the pictures a pass has most recently
finished: bounded, overwritten, a few hundred bytes however large the library.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from math import ceil
from pathlib import Path
from typing import Any

from immich_memories.security import write_secret_file

PROGRESS_FILE = "stage-progress.private.json"

# The strip a watcher draws is a handful of pictures wide; ids beyond that are
# bytes nobody ever sees.
RECENT_ASSET_LIMIT = 12

# The two phases a cut passes through once its media is loaded: preparing the
# pictures' facts, then editing. Values match ``OperationalPhase``.
ANALYSIS_PHASE = "analysis"
SELECTION_PHASE = "selection"


@dataclass(frozen=True, slots=True)
class StageUpdate:
    """One position in a run: phase, stage, and counts when the stage has them."""

    label: str
    phase: str = SELECTION_PHASE
    done: int | None = None
    total: int | None = None
    recent_asset_ids: tuple[str, ...] = ()
    # The verb a counted stage is shown with: preparation passes prepare,
    # the editorial reads read.
    verb: str = "Preparing"
    remaining_seconds: float | None = None

    @property
    def identity(self) -> tuple[str, str, str, int | None]:
        return self.phase, self.label, self.verb, self.total

    @property
    def remaining_label(self) -> str:
        """The same deliberately rounded stage estimate on the page and terminal."""
        seconds = self.remaining_seconds
        if seconds is None or not self.counted or self.fraction == 1:
            return ""
        amount = f"{ceil(seconds)}s" if seconds < 60 else f"{ceil(seconds / 60)}m"
        return f"~{amount} left in this stage"

    @property
    def counted(self) -> bool:
        return self.done is not None and self.total is not None

    @property
    def stage_label(self) -> str:
        """The sentence a phase row shows for this position."""
        if self.counted:
            return f"{self.verb} {self.label}: {self.done}/{self.total}"
        return self.label

    @property
    def fraction(self) -> float | None:
        """How far through, or None when the stage counts nothing or its total says nothing."""
        if self.done is None or self.total is None or self.total <= 0:
            return None
        return min(1.0, max(0.0, self.done / self.total))

    def as_record(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "label": self.label,
            "done": self.done,
            "total": self.total,
            "verb": self.verb,
            "remaining_seconds": self.remaining_seconds,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | None) -> StageUpdate | None:
        if not isinstance(record, Mapping) or "label" not in record:
            return None
        try:
            done = record.get("done")
            total = record.get("total")
            return cls(
                label=str(record["label"]),
                phase=str(record.get("phase") or SELECTION_PHASE),
                done=None if done is None else int(done),
                total=None if total is None else int(total),
                recent_asset_ids=tuple(str(v) for v in record.get("recent_asset_ids") or ()),
                verb=str(record.get("verb") or "Preparing"),
                remaining_seconds=record.get("remaining_seconds"),
            )
        except (ValueError, TypeError):
            return None


class StageClock:
    """Measure only work observed in this stage; never borrow another pass's rate."""

    def __init__(self) -> None:
        self._previous: StageUpdate | None = None
        self._started = 0.0
        self._baseline = 0

    def measure(self, update: StageUpdate) -> StageUpdate:
        now = time.monotonic()
        previous = self._previous
        if (
            previous is None
            or previous.identity != update.identity
            or (update.done or 0) < (previous.done or 0)
        ):
            self._started = now
            self._baseline = update.done or 0
        self._previous = update
        completed = (update.done or 0) - self._baseline
        remaining = None
        if completed > 0 and update.total and update.done is not None:
            remaining = (now - self._started) * max(0, update.total - update.done) / completed
        return replace(update, remaining_seconds=remaining)


# The run's stage sink, reachable from layers that never see the `on_stage`
# callback: the model gateways and the structure planner announce through it.
# A context variable rather than a global, so two cuts in one process (two web
# sessions) never hear each other; the gateway's sync bridge copies the context
# onto its helper thread.
_STAGE_SINK: ContextVar[Callable[[StageUpdate], None] | None] = ContextVar(
    "stage_sink", default=None
)


@contextmanager
def announcing_stages(on_stage: Callable[[StageUpdate], None] | None) -> Iterator[None]:
    """Route every `announce_stage` inside the block to `on_stage`."""
    token = _STAGE_SINK.set(on_stage)
    try:
        yield
    finally:
        _STAGE_SINK.reset(token)


_last_stage: StageUpdate | None = None


def last_announced_stage() -> StageUpdate | None:
    """Where the run had got to, for an error that carries no message of its own.

    Deliberately a module global, not a context variable: the text requests run on their
    own thread with a copied context, so a value set there never reaches the error
    boundary that has to print something.
    """
    return _last_stage


def announce_stage(update: StageUpdate) -> None:
    """Tell the run where it is, if anyone is listening; never raises."""
    global _last_stage
    _last_stage = update
    sink = _STAGE_SINK.get()
    if sink is None:
        return
    with contextlib.suppress(Exception):
        sink(update)


class StageProgressWriter:
    """Gather finished asset ids cheaply; write only when a stage publishes.

    ``note_asset`` sits on the per-asset path and touches memory only.
    ``publish`` is called on the same throttle the stage label already uses, so
    the write rate follows the run's batch size rather than its library size.
    """

    def __init__(self, directory: Callable[[], Path], limit: int = RECENT_ASSET_LIMIT) -> None:
        self._directory = directory
        self._recent: deque[str] = deque(maxlen=limit)

    def note_asset(self, asset_id: str) -> None:
        self._recent.append(asset_id)

    def publish(self, label: str, done: int, total: int) -> StageUpdate:
        """Write the snapshot and return the record the caller announces."""
        progress = StageUpdate(label, ANALYSIS_PHASE, done, total, tuple(self._recent))
        payload = progress.as_record() | {"recent_asset_ids": list(progress.recent_asset_ids)}
        # A file nobody renders from is not worth failing a cut that is
        # otherwise fine, so a display write never propagates.
        with contextlib.suppress(OSError):
            write_secret_file(self._directory() / PROGRESS_FILE, json.dumps(payload))
        return progress


def read_stage_progress(directory: Path | None) -> StageUpdate | None:
    """The last published snapshot, or None when there is none to read yet."""
    if directory is None:
        return None
    try:
        record = json.loads((directory / PROGRESS_FILE).read_text())
    except (OSError, ValueError):
        return None
    progress = StageUpdate.from_record(record)
    return progress if progress is not None and progress.counted else None
