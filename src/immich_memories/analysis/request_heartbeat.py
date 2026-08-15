"""Heartbeat logging for long-running synchronous HTTP requests.

Local vision-model requests are configured with a long read timeout because a
35B model doing local inference can genuinely take minutes. Without this
heartbeat, a stuck request produces zero log output for the entire wait —
indistinguishable from a hung or crashed process. `RequestHeartbeat` logs a
periodic line while the wrapped block is running and stops as soon as it
exits, whether it returns normally or raises.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType

logger = logging.getLogger(__name__)


class RequestHeartbeat:
    """Context manager that logs a periodic line while a blocking call runs.

    Backed by a single background thread parked on a `threading.Event`
    rather than a self-rescheduling `threading.Timer`: `__exit__` only has
    to set the event and `join()` once, so there is no window where a timer
    callback re-arms itself after cancellation has already been requested.
    """

    def __init__(
        self,
        description: str,
        interval_seconds: float = 60.0,
        warn_after_occurrences: int = 3,
    ) -> None:
        self._description = description
        self._interval_seconds = interval_seconds
        self._warn_after_occurrences = warn_after_occurrences
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> RequestHeartbeat:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="llm-request-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        occurrences = 0
        while not self._stop.wait(self._interval_seconds):
            occurrences += 1
            elapsed_seconds = int(occurrences * self._interval_seconds)
            level = logging.WARNING if occurrences >= self._warn_after_occurrences else logging.INFO
            logger.log(
                level,
                "%s still outstanding after %ds",
                self._description,
                elapsed_seconds,
            )
