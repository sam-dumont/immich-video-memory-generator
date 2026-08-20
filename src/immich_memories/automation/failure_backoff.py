"""Keep a repeatedly-failing candidate out of the nightly slot for a while.

Every automation attempt already records its outcome against a memory key, but
selection only ever read the *completed* ones. A candidate that cannot render
therefore stayed permanently eligible and won again every night -- one real log
shows the same monthly candidate launched nine nights in a row, each attempt
consuming the whole nightly run and producing nothing.

The backoff grows with the streak and is capped, so a candidate broken by
something temporary (a missing asset that gets re-uploaded, a server that was
down) always comes back rather than being written off forever.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from immich_memories.automation.state_store import FailureStreak

if TYPE_CHECKING:
    from immich_memories.automation.candidates import MemoryCandidate

logger = logging.getLogger(__name__)

# One failure is noise. Two in a row is usually a memory that tonight will not fix.
_MIN_FAILURES = 2
_WINDOW_BY_STREAK: dict[int, timedelta] = {
    2: timedelta(hours=24),
    3: timedelta(days=3),
}
_MAX_WINDOW = timedelta(days=7)


def backoff_window(failures: int) -> timedelta | None:
    """How long a candidate waits after this many consecutive failures."""
    if failures < _MIN_FAILURES:
        return None
    return _WINDOW_BY_STREAK.get(failures, _MAX_WINDOW)


def suppressed_keys(
    streaks: dict[str, FailureStreak],
    now: datetime,
) -> dict[str, str]:
    """Memory keys still inside their backoff window, mapped to a reason.

    A streak with no recorded failure time is never suppressed: incomplete
    telemetry should not strand a candidate.
    """
    suppressed: dict[str, str] = {}
    for key, streak in streaks.items():
        window = backoff_window(streak.count)
        if window is None or streak.last_failed_at is None:
            continue
        if now - streak.last_failed_at < window:
            suppressed[key] = f"failed {streak.count}x, retrying after {_describe(window)}"
    return suppressed


def _describe(window: timedelta) -> str:
    hours = int(window.total_seconds() // 3600)
    return f"{hours}h" if hours < 48 else f"{hours // 24}d"


def drop_backed_off(
    candidates: list[MemoryCandidate],
    streaks: dict[str, FailureStreak],
    now: datetime,
) -> tuple[list[MemoryCandidate], dict[str, str]]:
    """Filter out candidates that are waiting out a failure streak.

    Returns the survivors and the suppressions, so the caller can tell an
    operator why a memory they expected is missing.
    """
    suppressed = suppressed_keys(streaks, now)
    for key, reason in sorted(suppressed.items()):
        logger.info("Skipping candidate %s: %s", key, reason)
    return [c for c in candidates if c.memory_key not in suppressed], suppressed
