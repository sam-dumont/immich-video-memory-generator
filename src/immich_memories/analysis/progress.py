"""Elapsed time for one editorial run, as the CLI and UI displays read it."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PipelineProgress:
    """When the run started, and how long it has been going."""

    start_time: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time


class ProgressTracker:
    """The clock the stage reporter reads while the planner works."""

    def __init__(self) -> None:
        self.progress = PipelineProgress()

    def start(self) -> None:
        self.progress = PipelineProgress(start_time=time.time())

    def finish(self) -> None:
        """Kept as the run's closing call; the elapsed clock keeps counting after it."""

    def format_elapsed(self) -> str:
        """Elapsed time as "5m 30s" or "2h 15m"."""
        seconds = self.progress.elapsed_seconds
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            minutes, secs = int(seconds // 60), int(seconds % 60)
            return f"{minutes}m {secs}s" if secs > 0 else f"{minutes}m"
        hours, minutes = int(seconds // 3600), int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
