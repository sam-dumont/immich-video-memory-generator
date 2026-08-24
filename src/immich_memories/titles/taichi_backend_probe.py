"""Taichi backend availability: which arch can actually dispatch a kernel here.

Owns the `taichi` import guard, so importing this module (directly or via
taichi_kernels) is what sets the banner-suppression env vars before Taichi's
C++ runtime loads. Nothing here knows about title kernels; taichi_kernels
drives the selection loop with `_candidate_backends` and `_backend_dispatches`.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import contextlib
import logging
import os
import queue
import sys
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

logger = logging.getLogger(__name__)

# WHY: Taichi's C++ runtime prints to stdout, corrupting Rich Live display.
# ENABLE_TAICHI_HEADER_PRINT="0" — suppresses import-time version banner (taichi#8334)
# TI_LOG_LEVEL — belt-and-suspenders for C++ log messages
# These must be set before `import taichi` below, and before any sibling module
# that imports Taichi is loaded.
# The main fix is verbose=False on ti.init() calls (see init_taichi and _check_taichi)
os.environ.setdefault("ENABLE_TAICHI_HEADER_PRINT", "0")
os.environ.setdefault("TI_LOG_LEVEL", "error")

try:
    import taichi as ti

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    ti = None

_TAICHI_PROBE_TIMEOUT_SECONDS = 10.0
_TAICHI_PROBE_STOP_TIMEOUT_SECONDS = 1.0


class TaichiProbeOutcome(StrEnum):
    """Bounded outcomes from an isolated backend dispatch probe."""

    SUCCESS = "success"
    DISPATCH_FAILED = "dispatch_failed"
    CHILD_CRASHED = "child_crashed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class TaichiProbeResult:
    """Small picklable result sent from the probe child to its parent."""

    outcome: TaichiProbeOutcome
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

    WHY: Taichi's C++ runtime prints "[Taichi] Starting on arch=metal"
    directly to file descriptor 1, bypassing Python's sys.stdout, all
    env vars (TI_LOG_LEVEL, ENABLE_TAICHI_HEADER_PRINT), and all Python
    API flags (verbose=False, log_level). The ONLY way to suppress it is
    to redirect the raw OS file descriptors during the call.
    """
    with _silence_output_fds():
        ti.init(**kwargs)


def _taichi_probe_worker(backend_name: str, result_queue) -> None:
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
            result = TaichiProbeResult(
                TaichiProbeOutcome.DISPATCH_FAILED,
                "unexpected_kernel_result",
            )
        else:
            result = TaichiProbeResult(TaichiProbeOutcome.SUCCESS)
    except Exception as exc:
        result = TaichiProbeResult(
            TaichiProbeOutcome.DISPATCH_FAILED,
            type(exc).__name__,
        )
    result_queue.put(result)


def _stop_probe_process(process) -> None:
    """Stop a stuck probe, escalating from terminate to kill."""
    process.terminate()
    process.join(_TAICHI_PROBE_STOP_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_TAICHI_PROBE_STOP_TIMEOUT_SECONDS)


def _probe_taichi_backend(
    backend_name: str,
    timeout: float = _TAICHI_PROBE_TIMEOUT_SECONDS,
) -> TaichiProbeResult:
    """Probe one non-CPU backend without changing parent Taichi state."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_taichi_probe_worker,
        args=(backend_name, result_queue),
        name=f"taichi-{backend_name}-probe",
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        process.join(timeout)
        if process.is_alive():
            _stop_probe_process(process)
            return TaichiProbeResult(TaichiProbeOutcome.TIMED_OUT)
        try:
            result = result_queue.get(timeout=0.25)
        except queue.Empty:
            return TaichiProbeResult(
                TaichiProbeOutcome.CHILD_CRASHED,
                f"exitcode={process.exitcode}",
            )
        if not isinstance(result, TaichiProbeResult):
            return TaichiProbeResult(TaichiProbeOutcome.CHILD_CRASHED, "invalid_result")
        return result
    except (OSError, RuntimeError, TypeError) as exc:
        return TaichiProbeResult(TaichiProbeOutcome.CHILD_CRASHED, type(exc).__name__)
    finally:
        if started and process.is_alive():
            _stop_probe_process(process)
        result_queue.close()
        result_queue.join_thread()
        if started and not process.is_alive():
            process.close()


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
    probe = _probe_taichi_backend(probe_name)
    if probe.outcome is TaichiProbeOutcome.SUCCESS:
        return True
    logger.debug(
        "Taichi %s dispatch probe failed (%s: %s)",
        name,
        probe.outcome.value,
        probe.detail or "no detail",
    )
    return False
