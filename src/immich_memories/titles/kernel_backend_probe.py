"""Kernel backend availability: which arch can actually dispatch a kernel here.

The library itself is imported once, in gpu_kernel_backend.py; this module only
asks which arch on this machine can actually run a kernel. Nothing here knows
about title kernels: kernels.py drives the selection loop with
`_candidate_backends` and `_backend_dispatches`.

Note: This module does NOT use 'from __future__ import annotations'
because kernel signatures need actual type objects, not string annotations.
"""

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np

# WHY absolute, in a module that otherwise uses relative imports: the dispatch
# probe runs this file as its own program in a child interpreter, where it is
# `__main__` with no package and a relative import cannot resolve.
from immich_memories.titles.gpu_kernel_backend import KERNEL_LIBRARY, ti

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 10.0


class KernelProbeOutcome(StrEnum):
    """Bounded outcomes from an isolated backend dispatch probe."""

    SUCCESS = "success"
    DISPATCH_FAILED = "dispatch_failed"
    CHILD_CRASHED = "child_crashed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class KernelProbeResult:
    """Small serialisable result the probe child hands back to its parent."""

    outcome: KernelProbeOutcome
    detail: str | None = None


@contextlib.contextmanager
def _silence_output_fds():
    """Temporarily redirect process stdout/stderr to the null device."""
    sys.stdout.flush()
    sys.stderr.flush()
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull_fd)


def _silent_init(**kwargs) -> None:
    """Call ti.init() with stdout/stderr silenced at the OS file descriptor level.

    WHY: the C++ runtime prints "Starting on arch=metal" directly to file
    descriptor 1, bypassing Python's sys.stdout, the quiet-mode env vars and
    every Python API flag (verbose=False, log_level). The ONLY way to suppress
    it is to redirect the raw OS file descriptors during the call.
    """
    with _silence_output_fds():
        ti.init(**kwargs)


def _probe_worker(backend_name: str) -> KernelProbeResult:
    """Initialize one backend and dispatch a real kernel inside a child process."""
    try:
        with _silence_output_fds():
            backend = getattr(ti, backend_name)
            ti.init(arch=backend, offline_cache=True)

            @ti.kernel
            def increment(values: ti.types.ndarray(dtype=ti.i32, ndim=1)):
                for index in values:
                    values[index] += 1

            values = np.zeros(1, dtype=np.int32)
            increment(values)
        if values[0] != 1:
            return KernelProbeResult(
                KernelProbeOutcome.DISPATCH_FAILED,
                "unexpected_kernel_result",
            )
    except Exception as exc:
        return KernelProbeResult(
            KernelProbeOutcome.DISPATCH_FAILED,
            type(exc).__name__,
        )
    return KernelProbeResult(KernelProbeOutcome.SUCCESS)


def _read_probe_result(result_path: Path) -> KernelProbeResult | None:
    """Read the child's answer, or None when it wrote something we cannot trust."""
    try:
        payload = json.loads(result_path.read_text())
        return KernelProbeResult(KernelProbeOutcome(payload["outcome"]), payload["detail"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _probe_backend(
    backend_name: str,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> KernelProbeResult:
    """Probe one non-CPU backend without disturbing the parent runtime.

    The child runs this file as its own program. A `multiprocessing` spawn child
    would instead re-import the parent's `__main__` with the parent's `sys.argv`
    restored, so under any launcher whose module body is not guarded by
    `if __name__ == "__main__"` the probe re-ran the whole application and printed
    that second run's failure into the terminal of the run in progress (#846).
    Its streams are captured for the same reason: a probe child must never write
    into its parent's output.
    """
    # WHY function-local: only the parent needs the bounded runner. The child runs
    # this file by path and must not depend on the package around it.
    from immich_memories.operations.bounded_process import run_bounded_process

    with tempfile.TemporaryDirectory(prefix="immich-kernel-probe-") as directory:
        result_path = Path(directory) / "result.json"
        command = [sys.executable, str(Path(__file__).resolve()), backend_name, str(result_path)]
        try:
            completed = run_bounded_process(command, timeout=timeout)
        except subprocess.TimeoutExpired:
            return KernelProbeResult(KernelProbeOutcome.TIMED_OUT)
        except OSError as exc:
            return KernelProbeResult(KernelProbeOutcome.CHILD_CRASHED, type(exc).__name__)
        if completed.returncode or not result_path.is_file():
            logger.debug(
                "Kernel %s probe child exited %s: %s",
                backend_name,
                completed.returncode,
                (completed.stderr or completed.stdout or "").strip()[-2000:],
            )
            return KernelProbeResult(
                KernelProbeOutcome.CHILD_CRASHED,
                f"exitcode={completed.returncode}",
            )
        result = _read_probe_result(result_path)
        if result is None:
            return KernelProbeResult(KernelProbeOutcome.CHILD_CRASHED, "invalid_result")
        return result


def _candidate_backends(
    *, force_cpu: bool, operating_system: str
) -> list[tuple[object, str, str | None]]:
    """Return parent architecture objects and child-safe probe names in priority order."""
    if force_cpu:
        return [(ti.cpu, "CPU", None)]
    if operating_system == "Darwin":
        return [(ti.metal, "Metal", "metal"), (ti.cpu, "CPU", None)]
    return [
        (ti.cuda, "CUDA", "cuda"),
        (ti.vulkan, "Vulkan", "vulkan"),
        (ti.cpu, "CPU", None),
    ]


def _backend_dispatches(name: str, probe_name: str | None) -> bool:
    """Prove a GPU backend can dispatch, while allowing CPU to bypass the probe."""
    if probe_name is None:
        return True
    probe = _probe_backend(probe_name)
    if probe.outcome is KernelProbeOutcome.SUCCESS:
        return True
    logger.debug(
        "%s %s dispatch probe failed (%s: %s)",
        KERNEL_LIBRARY,
        name,
        probe.outcome.value,
        probe.detail or "no detail",
    )
    return False


if __name__ == "__main__":
    _result = _probe_worker(sys.argv[1])
    Path(sys.argv[2]).write_text(
        json.dumps({"outcome": _result.outcome.value, "detail": _result.detail})
    )
