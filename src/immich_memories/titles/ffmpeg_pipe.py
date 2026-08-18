"""Shared plumbing for feeding raw frames to FFmpeg over a pipe.

Writing frames to FFmpeg's stdin while its stderr is piped and unread
deadlocks: once FFmpeg fills the stderr pipe buffer it blocks on that write,
stops draining stdin, and the producer blocks in turn. The render then hangs
forever at 0% CPU with no output — indistinguishable from a crash.

Draining stderr concurrently for the whole process lifetime is the fix. Only
the newest bytes are kept, because FFmpeg can emit unbounded progress output
and the tail is wanted for diagnostics, not archival.
"""

from __future__ import annotations

import subprocess
import threading

STDERR_TAIL_BYTES = 8192
_READ_CHUNK_BYTES = 65536


def drain_stderr_tail(stream, tail: bytearray, *, limit: int = STDERR_TAIL_BYTES) -> None:
    """Drain a byte stream to EOF, retaining only its newest `limit` bytes."""
    if stream is None:
        return
    while chunk := stream.read(_READ_CHUNK_BYTES):
        tail.extend(chunk)
        if len(tail) > limit:
            del tail[:-limit]


class StderrDrain:
    """Drains `process.stderr` on a background thread for the process lifetime.

    Explicit start/stop rather than a context manager, because every caller's
    frame loop already owns its function body.
    """

    def __init__(
        self,
        process: subprocess.Popen,
        *,
        limit: int = STDERR_TAIL_BYTES,
        join_timeout: float = 10.0,
    ) -> None:
        self._process = process
        self._limit = limit
        self._join_timeout = join_timeout
        self._reader: threading.Thread | None = None
        self.tail = bytearray()

    def start(self) -> StderrDrain:
        self._reader = threading.Thread(
            target=drain_stderr_tail,
            args=(self._process.stderr, self.tail),
            kwargs={"limit": self._limit},
            daemon=True,
        )
        self._reader.start()
        return self

    def stop(self) -> str:
        """Join the reader and return the decoded tail."""
        if self._reader is not None:
            self._reader.join(timeout=self._join_timeout)
            self._reader = None
        return stderr_text(self.tail)


def stderr_text(tail: bytearray) -> str:
    """Decode a drained tail for an error message."""
    return bytes(tail).decode(errors="replace").strip()
