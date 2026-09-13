"""Contracts for isolated Taichi backend dispatch probing."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.operations import bounded_process


class _RecordedRun:
    """Stand in for the bounded process runner and record what the probe asked for."""

    def __init__(
        self,
        *,
        payload: dict[str, object] | str | None,
        returncode: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self.payload = payload
        self.returncode = returncode
        self.error = error
        self.command: list[str] | None = None
        self.timeout: float | None = None

    def __call__(self, command, *, timeout, **_kwargs):
        self.command = list(command)
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        if self.payload is not None:
            written = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
            Path(command[3]).write_text(written)
        return subprocess.CompletedProcess(list(command), self.returncode, "", "")


def _install_runner(monkeypatch: pytest.MonkeyPatch, run: _RecordedRun) -> _RecordedRun:
    # WHY: the runner is the process boundary — the one thing the probe owns that
    # a unit test cannot let loose, since a real child would claim the GPU.
    monkeypatch.setattr(bounded_process, "run_bounded_process", run)
    return run


class _FakeTaichi:
    """Stand-in for the `taichi` module inside the probe child.

    The worker only ever touches four things on it: an arch attribute, init(),
    the @kernel decorator, and the ndarray type annotation.
    """

    def __init__(self, *, effect, init_error: Exception | None = None) -> None:
        self.metal = object()
        self.cuda = object()
        self.vulkan = object()
        self.cpu = object()
        self.i32 = "i32"
        self.types = SimpleNamespace(ndarray=lambda **kwargs: object())  # noqa: ARG005
        self._effect = effect
        self._init_error = init_error
        self.init_calls: list[dict[str, object]] = []

    def init(self, **kwargs: object) -> None:
        if self._init_error is not None:
            raise self._init_error
        self.init_calls.append(kwargs)

    def kernel(self, _fn):
        return self._effect


def _run_worker(monkeypatch: pytest.MonkeyPatch, fake_ti: _FakeTaichi) -> object:
    from immich_memories.titles import kernel_backend_probe

    # WHY: the worker's whole job is driving the Taichi runtime — the one
    # external boundary here. A real ti.init() would claim the GPU in-process,
    # which is exactly what running the probe in a child process avoids.
    monkeypatch.setattr(kernel_backend_probe, "ti", fake_ti)
    return kernel_backend_probe._probe_worker("metal")


def test_worker_reports_success_when_the_kernel_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    def increments(values) -> None:
        values[0] += 1

    fake_ti = _FakeTaichi(effect=increments)

    result = _run_worker(monkeypatch, fake_ti)

    assert result.outcome is KernelProbeOutcome.SUCCESS
    assert fake_ti.init_calls == [{"arch": fake_ti.metal, "offline_cache": True}]


def test_worker_rejects_a_backend_that_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend that accepts the launch but leaves memory untouched is a failure.

    This is the case the probe exists for: init() succeeding proves nothing,
    only the written-back value does.
    """
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    result = _run_worker(monkeypatch, _FakeTaichi(effect=lambda _values: None))

    assert result.outcome is KernelProbeOutcome.DISPATCH_FAILED
    assert result.detail == "unexpected_kernel_result"


def test_worker_names_the_exception_a_failing_backend_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    fake_ti = _FakeTaichi(effect=lambda _values: None, init_error=RuntimeError("no device"))

    result = _run_worker(monkeypatch, fake_ti)

    assert result.outcome is KernelProbeOutcome.DISPATCH_FAILED
    assert result.detail == "RuntimeError"


def test_non_apple_hosts_try_cuda_then_vulkan_then_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles import kernel_backend_probe

    fake_ti = _FakeTaichi(effect=lambda _values: None)
    # WHY: the arch objects are Taichi runtime singletons; identity is what
    # init_kernels passes through to ti.init().
    monkeypatch.setattr(kernel_backend_probe, "ti", fake_ti)

    candidates = kernel_backend_probe._candidate_backends(force_cpu=False, operating_system="Linux")

    assert candidates == [
        (fake_ti.cuda, "CUDA", "cuda"),
        (fake_ti.vulkan, "Vulkan", "vulkan"),
        (fake_ti.cpu, "CPU", None),
    ]


def test_the_probe_runs_its_own_program_instead_of_re_running_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child must be this file, never a re-import of the caller's __main__.

    A `multiprocessing` spawn child re-imports the parent's `__main__` with the
    parent's `sys.argv` restored, which ran the whole CLI a second time inside a
    generation and printed that run's error into the terminal (#846).
    """
    from immich_memories.titles import kernel_backend_probe
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    run = _install_runner(monkeypatch, _RecordedRun(payload={"outcome": "success", "detail": None}))

    result = _probe_backend("metal", timeout=2.5)

    assert result.outcome is KernelProbeOutcome.SUCCESS
    assert run.timeout == 2.5
    assert run.command is not None
    assert run.command[:3] == [
        sys.executable,
        str(Path(kernel_backend_probe.__file__).resolve()),
        "metal",
    ]


def test_probe_preserves_dispatch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        KernelProbeResult,
        _probe_backend,
    )

    _install_runner(
        monkeypatch,
        _RecordedRun(payload={"outcome": "dispatch_failed", "detail": "RuntimeError"}),
    )

    result = _probe_backend("cuda")

    assert result == KernelProbeResult(KernelProbeOutcome.DISPATCH_FAILED, "RuntimeError")


def test_probe_reports_child_crash_without_a_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(monkeypatch, _RecordedRun(payload=None, returncode=7))

    result = _probe_backend("vulkan")

    assert result.outcome is KernelProbeOutcome.CHILD_CRASHED
    assert result.detail == "exitcode=7"


def test_probe_distrusts_a_result_it_did_not_recognise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything but a probe outcome means the child was not ours to trust."""
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(monkeypatch, _RecordedRun(payload="something else entirely"))

    result = _probe_backend("metal")

    assert result.outcome is KernelProbeOutcome.CHILD_CRASHED
    assert result.detail == "invalid_result"


def test_probe_reports_a_child_that_outlived_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(
        monkeypatch,
        _RecordedRun(payload=None, error=subprocess.TimeoutExpired("probe", 4.0)),
    )

    result = _probe_backend("metal", timeout=4.0)

    assert result.outcome is KernelProbeOutcome.TIMED_OUT


def test_probe_survives_an_interpreter_it_cannot_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(monkeypatch, _RecordedRun(payload=None, error=OSError("no interpreter")))

    result = _probe_backend("metal")

    assert result.outcome is KernelProbeOutcome.CHILD_CRASHED
    assert result.detail == "OSError"


def _prepare_parent_init(monkeypatch: pytest.MonkeyPatch):
    """Stub both namespaces init_kernels() spans: the kernels module and the probe.

    `init_kernels` resolves KERNELS_AVAILABLE and the module-level init flags in
    `kernels`, but `_candidate_backends` and `_backend_dispatches` read
    `ti` and `_probe_backend` from `kernel_backend_probe`. Patching one
    module for both would leave the real Taichi arch objects in play.
    """
    from immich_memories.titles import kernel_backend_probe, kernels

    fake_ti = SimpleNamespace(metal=object(), cpu=object())
    monkeypatch.setattr(kernels, "KERNELS_AVAILABLE", True)
    monkeypatch.setattr(kernel_backend_probe, "ti", fake_ti)
    monkeypatch.setattr(kernels, "_kernels_initialized", False)
    monkeypatch.setattr(kernels, "_kernel_arch", None)
    monkeypatch.setattr(kernels, "SDF_AVAILABLE", False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.delenv("IMMICH_FORCE_CPU", raising=False)
    return kernels, kernel_backend_probe, fake_ti


def test_successful_gpu_probe_initializes_parent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        KernelProbeResult,
    )

    kernels, probe_module, fake_ti = _prepare_parent_init(monkeypatch)
    probes: list[str] = []
    parent_inits: list[dict[str, object]] = []
    compile_calls: list[bool] = []
    monkeypatch.setattr(
        probe_module,
        "_probe_backend",
        lambda backend: probes.append(backend) or KernelProbeResult(KernelProbeOutcome.SUCCESS),
    )
    monkeypatch.setattr(
        kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: compile_calls.append(True))

    assert kernels.init_kernels() == "Metal"
    assert kernels.init_kernels() == "Metal"

    assert probes == ["metal"]
    assert parent_inits == [{"arch": fake_ti.metal, "offline_cache": True}]
    assert compile_calls == [True]


@pytest.mark.parametrize(
    "outcome",
    [
        "dispatch_failed",
        "child_crashed",
        "timed_out",
    ],
)
def test_failed_gpu_probe_skips_parent_gpu_init(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        KernelProbeResult,
    )

    kernels, probe_module, fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    monkeypatch.setattr(
        probe_module,
        "_probe_backend",
        lambda _backend: KernelProbeResult(KernelProbeOutcome(outcome)),
    )
    monkeypatch.setattr(
        kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: None)

    assert kernels.init_kernels() == "CPU"
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]


def test_forced_cpu_never_spawns_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    kernels, probe_module, fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    monkeypatch.setenv("IMMICH_FORCE_CPU", "true")
    monkeypatch.setattr(
        probe_module,
        "_probe_backend",
        lambda _backend: pytest.fail("CPU fallback must not spawn a child"),
    )
    monkeypatch.setattr(
        kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: None)

    assert kernels.init_kernels() == "CPU"
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]
