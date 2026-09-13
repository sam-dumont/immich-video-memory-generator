"""Feeding FFmpeg's stdin while its stderr is piped must not be able to deadlock.

A real child process is used rather than a mock: the failure lives in OS pipe
buffering, and a mock would happily "pass" a version that hangs in production.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time

import pytest

from immich_memories.processing.ffmpeg_runner import stop_owned_process, write_frames_to_ffmpeg

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
# Never reads stdin and never exits on its own: the shape of a wedged encoder.
_STALLS = "import time; time.sleep(60)"
_DIES_AT_ONCE = "raise SystemExit(3)"


def _endless_frames():
    """A feed that only ends when the child stops taking it."""
    while True:
        yield b"x" * 65536


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


def _spy_on_spawned_processes(monkeypatch) -> list[subprocess.Popen]:
    """Hand the test the real children the helper starts, so it can inspect them after."""
    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    # WHY: not a mock — the real child still runs. The helper owns its process and
    # never hands it back, and "the child is gone" is only answerable on that object.
    def _remember(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", _remember)
    return spawned


def test_a_stalled_child_is_killed_and_reaped_when_the_wait_expires(monkeypatch) -> None:
    """#883: a wait() that times out must still end the child and join the reader."""
    spawned = _spy_on_spawned_processes(monkeypatch)
    threads_before = set(threading.enumerate())

    with pytest.raises(subprocess.TimeoutExpired):
        write_frames_to_ffmpeg([sys.executable, "-c", _STALLS], [b"frame"], wait_timeout=0.3)

    assert spawned[-1].poll() is not None, "the stalled child outlived the timeout"
    assert set(threading.enumerate()) <= threads_before, "a reader thread was left behind"


def test_a_feed_blocked_on_a_full_pipe_is_bounded_by_the_total_deadline(monkeypatch) -> None:
    """#883: the deadline covers the whole operation, not just the wait after the last frame."""
    spawned = _spy_on_spawned_processes(monkeypatch)
    threads_before = set(threading.enumerate())

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        write_frames_to_ffmpeg(
            [sys.executable, "-c", _STALLS],
            _endless_frames(),
            wait_timeout=30,
            total_timeout=0.5,
        )

    assert time.monotonic() - started < 3, "the blocked write outlived its deadline"
    assert spawned[-1].poll() is not None, "the wedged child outlived the deadline"
    assert set(threading.enumerate()) <= threads_before, "a reader thread was left behind"


def test_a_child_that_ignores_sigterm_is_killed_and_reaped() -> None:
    """The escalation is the point of the shared cleanup: FFmpeg gets asked, then made."""
    deaf_to_term = (
        "import signal, sys, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "sys.stdout.write('ready');"
        "sys.stdout.flush();"
        "time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", deaf_to_term], stdout=subprocess.PIPE)
    assert process.stdout is not None
    assert process.stdout.read(5) == b"ready", "the child never installed its handler"

    stop_owned_process(process, terminate_grace=0.3)

    assert process.returncode == -signal.SIGKILL


def test_a_child_that_dies_early_still_surfaces_the_broken_pipe() -> None:
    """The deadline must not swallow a write that failed for its own reasons."""
    with pytest.raises(OSError):
        write_frames_to_ffmpeg(
            [sys.executable, "-c", _DIES_AT_ONCE],
            _endless_frames(),
            wait_timeout=5,
            total_timeout=10,
        )
