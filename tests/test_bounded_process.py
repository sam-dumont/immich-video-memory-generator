"""Scheduled work owns its descendants and has a bounded shutdown path."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.operations.bounded_process import ProcessCancelled, run_bounded_process


def test_macos_zombie_group_is_reaped_before_retrying_permission_error():
    from immich_memories.operations.bounded_process import _group_exists

    process = MagicMock(pid=54321)
    process.poll.side_effect = [None, -15]
    # WHY: os.killpg is a real syscall; scripted to raise PermissionError then ProcessLookupError
    with patch(
        "immich_memories.operations.bounded_process.os.killpg",
        side_effect=[PermissionError(), ProcessLookupError()],
    ) as probe:
        assert not _group_exists(process)
    assert process.poll.call_count == probe.call_count == 2


def test_success_preserves_exit_status_and_both_output_streams():
    result = run_bounded_process(
        [sys.executable, "-c", "import sys;print('out');print('err',file=sys.stderr);sys.exit(7)"],
        timeout=5,
    )
    assert result.returncode == 7
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


@pytest.mark.skipif(os.name != "posix", reason="process-group ownership is POSIX")
@pytest.mark.parametrize("parent_ignores_term", [True, False])
def test_timeout_kills_term_ignoring_descendant_and_retains_logs(tmp_path, parent_ignores_term):
    lock = tmp_path / "descendant.lock"
    code = """
import fcntl,signal,subprocess,sys,time
child = '''import fcntl,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
lock=open(sys.argv[1],'w')
fcntl.flock(lock,fcntl.LOCK_EX)
print('descendant ready',flush=True)
time.sleep(60)
'''
if sys.argv[2]=='1': signal.signal(signal.SIGTERM,signal.SIG_IGN)
subprocess.Popen([sys.executable,'-c',child,sys.argv[1]])
print('parent ready',flush=True)
print('parent diagnostic',file=sys.stderr,flush=True)
time.sleep(60)
"""
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_bounded_process(
            [sys.executable, "-c", code, str(lock), str(int(parent_ignores_term))],
            timeout=1,
            terminate_grace_seconds=0.1,
            kill_grace_seconds=1,
        )
    assert "parent ready" in caught.value.stdout
    assert "descendant ready" in caught.value.stdout
    assert "parent diagnostic" in caught.value.stderr
    # A surviving grandchild would still hold this lease, even if the parent died.
    with lock.open("a") as handle:
        deadline = time.monotonic() + 1
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AssertionError("descendant survived the scheduler timeout") from None
                time.sleep(0.01)


def test_shutdown_check_cancels_child_before_generation_deadline():
    checks = []

    def cancelled():
        checks.append(True)
        return len(checks) > 1

    with pytest.raises(ProcessCancelled):
        run_bounded_process(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            timeout=60,
            cancel_check=cancelled,
            poll_interval_seconds=0.01,
            terminate_grace_seconds=0.1,
            kill_grace_seconds=1,
        )
    assert len(checks) == 2


def test_keyboard_interrupt_still_terminates_owned_group():
    process = MagicMock(pid=54321, returncode=0)
    process.wait.side_effect = [KeyboardInterrupt(), 0]
    # WHY: Popen/killpg are real syscalls; replaced to check termination survives an interrupt
    with (
        # WHY: subprocess.Popen is the real process spawn call; replaced with a scripted MagicMock
        patch("immich_memories.operations.bounded_process.subprocess.Popen", return_value=process),
        # WHY: os.killpg sends real OS signals; captured to verify SIGTERM then SIGKILL order
        patch("immich_memories.operations.bounded_process.os.killpg") as kill,
        pytest.raises(KeyboardInterrupt),
    ):
        run_bounded_process(
            ["synthetic"], timeout=10, terminate_grace_seconds=0, kill_grace_seconds=0.1
        )
    signals = [call.args for call in kill.call_args_list]
    assert (54321, signal.SIGTERM) in signals
    assert (54321, signal.SIGKILL) in signals
    assert process.wait.call_args.kwargs["timeout"] <= 0.1


def test_reap_after_kill_remains_bounded_even_if_child_does_not_exit(caplog):
    process = MagicMock(pid=54321, returncode=None)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 10)
    # WHY: process.wait always times out below; Popen/killpg patched so no real process is spawned
    with (
        # WHY: subprocess.Popen is replaced with the scripted MagicMock so wait() can time out
        patch("immich_memories.operations.bounded_process.subprocess.Popen", return_value=process),
        # WHY: os.killpg sends real OS signals; patched since only the retry count is checked
        patch("immich_memories.operations.bounded_process.os.killpg"),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        run_bounded_process(
            ["synthetic"], timeout=10, terminate_grace_seconds=0, kill_grace_seconds=0.1
        )
    assert process.wait.call_count == 2
    assert process.wait.call_args.kwargs["timeout"] == 0.1
    assert "did not exit within the bounded wait" in caplog.text


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_nonfinite_or_nonpositive_deadline_never_spawns(timeout):
    # WHY: pairs with pytest.raises below; Popen is patched to prove invalid timeouts never spawn
    with (
        # WHY: subprocess.Popen is the real spawn call; captured to assert it's never invoked
        patch("immich_memories.operations.bounded_process.subprocess.Popen") as spawn,
        pytest.raises(ValueError),
    ):
        run_bounded_process(["synthetic"], timeout=timeout)
    spawn.assert_not_called()
