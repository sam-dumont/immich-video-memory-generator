"""Run a scheduled generation with bounded ownership of its child process group."""

from __future__ import annotations

import contextlib
import logging
import math
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence

logger = logging.getLogger(__name__)


class ProcessCancelled(RuntimeError):
    """The scheduler asked its currently running generation to stop."""

    stdout: str | None = None
    stderr: str | None = None


def _kill_owned_group(process, sig):
    process.poll()
    try:
        os.killpg(process.pid, sig)
    except PermissionError:
        # macOS can return EPERM for a group whose only member just became an
        # unreaped zombie. Reap it and retry; do not suppress a real permission error.
        if process.poll() is None:
            raise
        os.killpg(process.pid, sig)


def _group_exists(process: subprocess.Popen) -> bool:
    if os.name != "posix":
        return process.poll() is None
    try:
        _kill_owned_group(process, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A cancellation can land before the child reaches setsid(), and macOS
        # answers EPERM rather than ESRCH for a group id nothing owns yet. The
        # direct child is the honest answer to "is there still work to stop".
        return process.poll() is None
    return True


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        if os.name != "posix":
            if process.poll() is None:
                process.terminate() if sig == signal.SIGTERM else process.kill()
            return
        try:
            _kill_owned_group(process, sig)
        except PermissionError:
            # The group does not exist yet (see _group_exists). The direct child
            # is ours either way, so it still receives the signal.
            if process.poll() is None:
                process.send_signal(sig)


def _stop_group(process, *, terminate_grace_seconds, kill_grace_seconds):
    # The leader may already have exited while one of its descendants continues.
    # Signal the session's original group even when process.poll() is non-None.
    _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + terminate_grace_seconds
    try:
        while _group_exists(process) and time.monotonic() < deadline:
            process.poll()  # Reap the direct child while descendants finish their cleanup.
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    finally:
        # A second Ctrl-C during the graceful wait must still stop the descendants.
        if _group_exists(process):
            _signal_group(process, getattr(signal, "SIGKILL", 9))
        try:
            process.wait(timeout=kill_grace_seconds)
        except subprocess.TimeoutExpired:
            # Even an uninterruptible kernel wait must not hang the scheduler forever.
            logger.error("Generation child did not exit within the bounded wait after SIGKILL")


def _wait(process, command, *, timeout, cancel_check, poll_interval_seconds):
    deadline = time.monotonic() + timeout
    while True:
        if cancel_check is not None and cancel_check():
            raise ProcessCancelled("Generation cancelled by scheduler shutdown")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        interval = min(remaining, poll_interval_seconds) if cancel_check is not None else remaining
        try:
            return process.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            if cancel_check is None:
                raise subprocess.TimeoutExpired(command, timeout) from None


def _validate_deadlines(
    *,
    timeout: float,
    poll_interval_seconds: float,
    terminate_grace_seconds: float,
    kill_grace_seconds: float,
) -> None:
    for value in (timeout, poll_interval_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("process deadlines and polling intervals must be finite and positive")
    for value in (terminate_grace_seconds, kill_grace_seconds):
        if not math.isfinite(value) or value < 0:
            raise ValueError("shutdown grace must be finite and nonnegative")


def _carry_captured_output(error: BaseException, out: str, err: str) -> None:
    """Give the failure the text its streams held, raising the timeout it rebuilds.

    TimeoutExpired only accepts text through its constructor: its attributes are
    declared as bytes even though the process ran with text pipes.
    """
    if isinstance(error, subprocess.TimeoutExpired):
        raise subprocess.TimeoutExpired(error.cmd, error.timeout, output=out, stderr=err) from error
    if isinstance(error, ProcessCancelled):
        error.stdout = out
        error.stderr = err


def run_bounded_process(
    command: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    terminate_grace_seconds: float = 5,
    kill_grace_seconds: float = 5,
    cancel_check: Callable[[], bool] | None = None,
    poll_interval_seconds: float = 0.2,
) -> subprocess.CompletedProcess[str]:
    """Capture output and stop the owned POSIX group on timeout or interruption.

    Temporary files avoid communicate()/pipe waits on surviving descendants.
    Cleanup adds at most the TERM grace plus the bounded KILL/reap wait. On
    non-POSIX platforms the fallback owns the direct child only.
    """
    _validate_deadlines(
        timeout=timeout,
        poll_interval_seconds=poll_interval_seconds,
        terminate_grace_seconds=terminate_grace_seconds,
        kill_grace_seconds=kill_grace_seconds,
    )
    if cancel_check is not None and cancel_check():
        raise ProcessCancelled("Generation cancelled before process launch")
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr,
    ):
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=stdout,
            stderr=stderr,
            env=env,
            start_new_session=os.name == "posix",
        )
        try:
            returncode = _wait(
                process,
                command,
                timeout=timeout,
                cancel_check=cancel_check,
                poll_interval_seconds=poll_interval_seconds,
            )
        except BaseException as error:
            _stop_group(
                process,
                terminate_grace_seconds=terminate_grace_seconds,
                kill_grace_seconds=kill_grace_seconds,
            )
            stdout.seek(0)
            stderr.seek(0)
            _carry_captured_output(error, stdout.read(), stderr.read())
            raise
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, returncode, stdout.read(), stderr.read())
