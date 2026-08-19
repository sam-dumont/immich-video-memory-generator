"""Feeding FFmpeg's stdin while its stderr is piped must not be able to deadlock.

A real child process is used rather than a mock: the failure lives in OS pipe
buffering, and a mock would happily "pass" a version that hangs in production.
"""

from __future__ import annotations

import subprocess
import sys
import threading

from immich_memories.processing.ffmpeg_runner import write_frames_to_ffmpeg

# Floods stderr *before* reading any stdin, so an undrained parent blocks as
# soon as the stderr buffer fills.
_NOISY = (
    "import sys;"
    "sys.stderr.buffer.write(b'x' * 400000);"
    "sys.stderr.buffer.flush();"
    "data = sys.stdin.buffer.read();"
    "sys.stderr.buffer.write(b'read %d bytes' % len(data));"
    "sys.stderr.buffer.flush()"
)
_EXIT_2 = "import sys; sys.stderr.buffer.write(b'boom'); sys.stdin.buffer.read(); sys.exit(2)"


def test_a_noisy_child_cannot_stall_the_writer() -> None:
    code, tail = write_frames_to_ffmpeg(
        [sys.executable, "-c", _NOISY],
        (b"f" * 100000 for _ in range(3)),
        wait_timeout=30,
    )

    assert code == 0
    assert "read 300000 bytes" in tail


def test_the_exit_code_and_stderr_tail_are_reported() -> None:
    code, tail = write_frames_to_ffmpeg([sys.executable, "-c", _EXIT_2], [b"data"], wait_timeout=30)

    assert code == 2
    assert "boom" in tail


def test_a_failing_frame_iterator_still_closes_the_process() -> None:
    """A render that dies mid-loop must not leave FFmpeg holding the pipe."""

    def _explode():
        yield b"first"
        raise ValueError("frame source failed")

    try:
        write_frames_to_ffmpeg([sys.executable, "-c", _NOISY], _explode(), wait_timeout=30)
    except ValueError:
        pass
    else:  # pragma: no cover - the iterator raises by construction
        raise AssertionError("the iterator's error must propagate")


def test_the_bare_pattern_really_does_deadlock() -> None:
    """Proves the child reproduces the bug, so the tests above mean something."""
    process = subprocess.Popen(
        [sys.executable, "-c", _NOISY], stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )
    finished = threading.Event()

    def _write_undrained() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(b"f" * 300000)
            process.stdin.flush()
        except OSError:
            pass
        finally:
            finished.set()

    threading.Thread(target=_write_undrained, daemon=True).start()
    try:
        assert not finished.wait(timeout=5.0), "child did not reproduce the deadlock"
    finally:
        process.kill()
        process.wait(timeout=10)
