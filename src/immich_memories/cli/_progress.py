"""Progress callback factories for CLI rendering modes.

Three modes for the same callback signature (phase, pct, msg):
- Interactive (Rich): multi-task Progress bar with ETA
- Quiet (structured log): key=value lines for cron/scripting
"""

from __future__ import annotations

import time
from collections.abc import Callable


def make_quiet_progress_callback(
    log_fn: Callable[[str], None] | None = None,
    min_interval: float = 2.0,
) -> Callable[[str, float, str], None]:
    """Create a progress callback that emits structured log lines.

    Suitable for --quiet mode, cron jobs, and non-interactive terminals.
    Throttles output to avoid spamming logs.
    """
    import logging

    logger = logging.getLogger("immich_memories.progress")
    _log = log_fn or logger.info
    last_report = [0.0]  # Mutable container for closure
    last_phase = [""]

    def _callback(phase: str, pct: float, msg: str) -> None:
        now = time.time()
        # Always report phase changes; throttle within-phase updates
        if phase == last_phase[0] and (now - last_report[0]) < min_interval:
            return
        last_report[0] = now
        last_phase[0] = phase
        _log(f"phase={phase} pct={int(pct * 100)} msg={msg}")

    return _callback
