"""Kernel backend availability: which arch can actually dispatch a kernel here.

This module is the gate in front of the kernel library, so it is the one module
about kernels that anything may import. It never loads the library at import
time: loading it runs native code, and on a CPU without AVX that load is already
enough to kill the interpreter with SIGILL (#910), before any code can choose
the PIL renderer instead. Only the probe child pays that price; `ti` is filled
in by `_kernel_library()` once something past the gate needs the real thing.

Nothing here knows about title kernels: kernels.py drives the selection loop
with `_candidate_backends` and `_backend_dispatches`.

Note: This module does NOT use 'from __future__ import annotations'
because kernel signatures need actual type objects, not string annotations.
"""

import contextlib
import functools
import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 10.0

CPU_PROBE_NAME = "cpu"

KERNEL_LIBRARY = "quadrants"

# The loaded library, or None until something past the gate asks for it.
ti: Any = None

_PIL_FALLBACK = "titles fall back to the PIL renderer"


def kernel_library_installed() -> bool:
    """Whether a kernel-library wheel exists here, asked without loading one.

    `find_spec` finds the package without executing it, which is the whole
    point: the execution is the part a processor without AVX cannot survive.
    """
    return importlib.util.find_spec(KERNEL_LIBRARY) is not None


def _kernel_library():
    """The kernel library itself, imported on first use rather than at import.

    Only reached past the gate: inside a probe child, or once
    `kernel_dispatch_failure()` has come back None.
    """
    global ti
    if ti is None:
        # WHY absolute, in a module that otherwise uses relative imports: the
        # dispatch probe runs this file as its own program in a child
        # interpreter, where it is `__main__` with no package and a relative
        # import cannot resolve.
        from immich_memories.titles.gpu_kernel_backend import ti as library

        ti = library
    return ti


class KernelProbeOutcome(StrEnum):
    """Bounded outcomes from an isolated backend dispatch probe."""

    SUCCESS = "success"
    DISPATCH_FAILED = "dispatch_failed"
    CHILD_CRASHED = "child_crashed"
    CHILD_SIGNALLED = "child_signalled"
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
        _kernel_library().init(**kwargs)


def _probe_worker(backend_name: str) -> KernelProbeResult:
    """Initialize one backend and dispatch a real kernel inside a child process."""
    try:
        with _silence_output_fds():
            library = _kernel_library()
            backend = getattr(library, backend_name)
            library.init(arch=backend, offline_cache=True)

            @library.kernel
            def increment(values: library.types.ndarray(dtype=library.i32, ndim=1)):
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


def _terminating_signal(returncode: int) -> signal.Signals | None:
    """The signal that killed the child, under either convention for reporting one.

    POSIX `wait` gives a negative return code; a shell, a container runtime or an
    init wrapper between us and the child gives 128+n instead. A kernel library
    compiling its first kernel for an instruction set the CPU does not have dies
    by SIGILL, which reaches us as -4 or 132 depending on what was in the way.
    """
    if returncode < 0:
        number = -returncode
    elif 128 < returncode < 192:
        number = returncode - 128
    else:
        return None
    try:
        return signal.Signals(number)
    except ValueError:
        return None


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
            if killed_by := _terminating_signal(completed.returncode):
                return KernelProbeResult(KernelProbeOutcome.CHILD_SIGNALLED, killed_by.name)
            return KernelProbeResult(
                KernelProbeOutcome.CHILD_CRASHED,
                f"exitcode={completed.returncode}",
            )
        result = _read_probe_result(result_path)
        if result is None:
            return KernelProbeResult(KernelProbeOutcome.CHILD_CRASHED, "invalid_result")
        return result


@functools.cache
def probe_backend_dispatch(backend_name: str) -> KernelProbeResult:
    """Answer once per process whether one backend can dispatch a kernel here.

    Cached because the child costs the better part of a second and its answer
    cannot change while the process lives: `init_kernels()` and `preflight` ask
    the same question and must not each pay for it (#855).
    """
    return _probe_backend(backend_name)


def kernel_dispatch_failure() -> str | None:
    """One line saying why title kernels cannot run here, or None when they can.

    Asked of the CPU backend because it is the floor under every other one: the
    library generates code for this processor whatever arch it targets, so a CPU
    that cannot execute a kernel has no working kernel renderer of any kind.
    """
    if not kernel_library_installed():
        return f"{KERNEL_LIBRARY} is not installed on this platform; {_PIL_FALLBACK}"
    result = probe_backend_dispatch(CPU_PROBE_NAME)
    if result.outcome is KernelProbeOutcome.SUCCESS:
        return None
    if result.outcome is KernelProbeOutcome.CHILD_SIGNALLED:
        return (
            f"kernel backend crashed on this CPU: {_signal_wording(result.detail)}; {_PIL_FALLBACK}"
        )
    if result.outcome is KernelProbeOutcome.TIMED_OUT:
        return (
            f"kernel backend did not start within {_PROBE_TIMEOUT_SECONDS:.0f}s on this machine; "
            f"{_PIL_FALLBACK}"
        )
    return (
        f"kernel backend could not dispatch here ({result.detail or 'no detail'}); {_PIL_FALLBACK}"
    )


def _signal_wording(detail: str | None) -> str:
    """Say what the signal means, for a reader who does not read signal names."""
    if detail == signal.SIGILL.name:
        return "illegal instruction"
    return detail.lower() if detail else "a fatal signal"


def _candidate_backends(*, force_cpu: bool, operating_system: str) -> list[tuple[object, str, str]]:
    """Return parent architecture objects and child-safe probe names in priority order."""
    library = _kernel_library()
    if force_cpu:
        return [(library.cpu, "CPU", CPU_PROBE_NAME)]
    if operating_system == "Darwin":
        return [(library.metal, "Metal", "metal"), (library.cpu, "CPU", CPU_PROBE_NAME)]
    return [
        (library.cuda, "CUDA", "cuda"),
        (library.vulkan, "Vulkan", "vulkan"),
        (library.cpu, "CPU", CPU_PROBE_NAME),
    ]


def _backend_dispatches(name: str, probe_name: str) -> bool:
    """Prove a backend can dispatch a kernel, CPU included.

    CPU used to be waved through on the grounds that a processor is always
    there. It is, but the library's generated code is not always something it
    can execute: on a CPU without AVX the first kernel it compiles dies by
    SIGILL, and waving CPU through put that death in the parent process, hours
    into a run, instead of in a probe child (#910).
    """
    probe = probe_backend_dispatch(probe_name)
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
