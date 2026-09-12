"""What a long stage is working on right now, for a surface watching it work.

A run publishes its position as a sentence -- "Preparing previews: 352/10793"
-- which is all a phase row needs and all a progress bar cannot use. This is the
same position as numbers, plus the handful of assets the pass has most recently
finished, so a watcher can draw a real bar and show the pictures going past.

It is a snapshot rather than a log: the file is overwritten and the asset window
is bounded, so a run over a hundred thousand pictures writes the same few
hundred bytes as a run over ten, and a reader that polls it pays the same cost
either way.
"""

from __future__ import annotations

import contextlib
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from immich_memories.security import write_secret_file

PROGRESS_FILE = "stage-progress.private.json"

# The strip a watcher draws is a handful of pictures wide; ids beyond that are
# bytes nobody ever sees.
RECENT_ASSET_LIMIT = 12


@dataclass(frozen=True, slots=True)
class StageProgress:
    """One long stage, as numbers rather than as a sentence."""

    label: str
    done: int
    total: int
    recent_asset_ids: tuple[str, ...] = ()

    @property
    def stage_label(self) -> str:
        """The sentence a run publishes for this position, as the phase row shows it.

        One formatter for both sides: a reader can tell whether the numbers it
        holds still describe the stage the attempt says it is on, which is what
        stops a full bar sitting under a row that has moved on.
        """
        return f"Preparing {self.label}: {self.done}/{self.total}"

    @property
    def fraction(self) -> float | None:
        """How far through, or None when the total cannot say."""
        if self.total <= 0:
            return None
        return min(1.0, max(0.0, self.done / self.total))


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

    def publish(self, label: str, done: int, total: int) -> StageProgress:
        """Write the snapshot and return it, so the caller can label its stage from it."""
        progress = StageProgress(label, done, total, tuple(self._recent))
        payload = {
            "label": progress.label,
            "done": progress.done,
            "total": progress.total,
            "recent_asset_ids": list(progress.recent_asset_ids),
        }
        # A file nobody renders from is not worth failing a cut that is
        # otherwise fine, so a display write never propagates.
        with contextlib.suppress(OSError):
            write_secret_file(self._directory() / PROGRESS_FILE, json.dumps(payload))
        return progress


def read_stage_progress(directory: Path | None) -> StageProgress | None:
    """The live stage's numbers, or None when there are none to read yet."""
    if directory is None:
        return None
    try:
        record = json.loads((directory / PROGRESS_FILE).read_text())
        return StageProgress(
            label=str(record["label"]),
            done=int(record["done"]),
            total=int(record["total"]),
            recent_asset_ids=tuple(str(value) for value in record.get("recent_asset_ids") or ()),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
