"""Tests for the FFmpeg stdin/stderr pipe plumbing.

Writing frames to a subprocess's stdin while its stderr is piped and unread is
a classic deadlock: once the child fills the stderr pipe buffer it blocks on
that write, stops draining stdin, and the producer blocks in turn. Neither side
can progress and the render hangs forever at 0% CPU.

These tests use a real child process rather than a mock, because the deadlock
lives in OS pipe buffering — a fake would not reproduce it.
"""

from __future__ import annotations

import subprocess
import sys
import threading

from immich_memories.titles.ffmpeg_pipe import drain_stderr_tail

# Child that floods stderr *before* reading any stdin. Without a concurrent
# drain the parent's stdin write blocks as soon as the stderr buffer fills.
_NOISY_CHILD = (
    "import sys;"
    "sys.stderr.buffer.write(b'x' * 400000);"
    "sys.stderr.buffer.flush();"
    "data = sys.stdin.buffer.read();"
    "sys.stderr.buffer.write(b'read %d bytes' % len(data));"
    "sys.stderr.buffer.flush()"
)


class TestDrainStderrTail:
    def test_concurrent_drain_prevents_deadlock(self) -> None:
        """The whole point: a noisy child must not be able to stall the writer."""
        process = subprocess.Popen(
            [sys.executable, "-c", _NOISY_CHILD],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tail = bytearray()
        reader = threading.Thread(target=drain_stderr_tail, args=(process.stderr, tail))
        reader.start()

        assert process.stdin is not None
        process.stdin.write(b"f" * 300000)
        process.stdin.close()

        assert process.wait(timeout=30) == 0
        reader.join(timeout=10)
        assert not reader.is_alive()
        assert b"read 300000 bytes" in bytes(tail)

    def test_without_drain_the_writer_actually_deadlocks(self) -> None:
        """Proves the child reproduces the bug, so the test above means something.

        The blocking write is done on a daemon thread: without a stderr drain it
        never returns, so it cannot be awaited on the main thread.
        """
        process = subprocess.Popen(
            [sys.executable, "-c", _NOISY_CHILD],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        finished = threading.Event()

        def _write_without_draining() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(b"f" * 300000)
                process.stdin.flush()
            except OSError:
                pass
            finally:
                finished.set()

        writer = threading.Thread(target=_write_without_draining, daemon=True)
        writer.start()
        try:
            assert not finished.wait(timeout=5.0), (
                "the write completed without a stderr drain, so this child no "
                "longer reproduces the deadlock and the test above proves nothing"
            )
        finally:
            process.kill()
            process.wait(timeout=10)
            finished.wait(timeout=5)

    def test_tail_is_bounded_to_the_newest_bytes(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'a'*50000 + b'TAIL')"],
            stderr=subprocess.PIPE,
        )
        tail = bytearray()
        drain_stderr_tail(process.stderr, tail, limit=1024)
        process.wait(timeout=30)

        assert len(tail) <= 1024
        assert bytes(tail).endswith(b"TAIL")
