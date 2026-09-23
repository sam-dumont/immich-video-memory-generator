"""Contracts for isolated kernel backend dispatch probing."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.operations import bounded_process


@pytest.fixture(autouse=True)
def _forget_probed_backends(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """The probe answer is cached for the life of a process, which here is the session.

    So is the kernel cache directory, which lives under HOME: a test must not
    create one in the home of whoever runs the suite.
    """
    from immich_memories.titles.kernel_backend_probe import kernel_cache_dir, probe_backend_dispatch

    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    probe_backend_dispatch.cache_clear()
    kernel_cache_dir.cache_clear()
    yield
    probe_backend_dispatch.cache_clear()
    kernel_cache_dir.cache_clear()


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


class _FakeKernelLibrary:
    """Stand-in for the kernel library inside the probe child.

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


def _run_worker(monkeypatch: pytest.MonkeyPatch, fake_ti: _FakeKernelLibrary) -> object:
    from immich_memories.titles import kernel_backend_probe

    # WHY: the worker's whole job is driving the kernel runtime — the one
    # external boundary here. A real ti.init() would claim the GPU in-process,
    # which is exactly what running the probe in a child process avoids.
    monkeypatch.setattr(kernel_backend_probe, "ti", fake_ti)
    return kernel_backend_probe._probe_worker("metal")


def test_worker_reports_success_when_the_kernel_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    monkeypatch.setenv("HOME", str(tmp_path))

    def increments(values) -> None:
        values[0] += 1

    fake_ti = _FakeKernelLibrary(effect=increments)

    result = _run_worker(monkeypatch, fake_ti)

    assert result.outcome is KernelProbeOutcome.SUCCESS
    assert fake_ti.init_calls == [
        {
            "arch": fake_ti.metal,
            "offline_cache": True,
            "offline_cache_file_path": str(tmp_path / ".immich-memories" / "cache" / "kernels"),
        }
    ]


def test_worker_rejects_a_backend_that_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend that accepts the launch but leaves memory untouched is a failure.

    This is the case the probe exists for: init() succeeding proves nothing,
    only the written-back value does.
    """
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    result = _run_worker(monkeypatch, _FakeKernelLibrary(effect=lambda _values: None))

    assert result.outcome is KernelProbeOutcome.DISPATCH_FAILED
    assert result.detail == "unexpected_kernel_result"


def test_worker_names_the_exception_a_failing_backend_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome

    fake_ti = _FakeKernelLibrary(effect=lambda _values: None, init_error=RuntimeError("no device"))

    result = _run_worker(monkeypatch, fake_ti)

    assert result.outcome is KernelProbeOutcome.DISPATCH_FAILED
    assert result.detail == "RuntimeError"


def test_non_apple_hosts_try_cuda_then_vulkan_then_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles import kernel_backend_probe

    fake_ti = _FakeKernelLibrary(effect=lambda _values: None)
    # WHY: the arch objects are kernel runtime singletons; identity is what
    # init_kernels passes through to ti.init().
    monkeypatch.setattr(kernel_backend_probe, "ti", fake_ti)

    candidates = kernel_backend_probe._candidate_backends(force_cpu=False, operating_system="Linux")

    assert candidates == [
        (fake_ti.cuda, "CUDA", "cuda"),
        (fake_ti.vulkan, "Vulkan", "vulkan"),
        (fake_ti.cpu, "CPU", "cpu"),
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


class _ScriptedRun:
    """A runner whose answers differ per attempt, recording how many were asked."""

    def __init__(self, *steps: dict[str, object]) -> None:
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, command, *, timeout, **_kwargs):
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if step.get("error") is not None:
            raise step["error"]
        payload = step.get("payload")
        if payload is not None:
            written = payload if isinstance(payload, str) else json.dumps(payload)
            Path(command[3]).write_text(written)  # type: ignore[index]
        return subprocess.CompletedProcess(
            list(command), int(step.get("returncode", 0)), "", str(step.get("stderr", ""))
        )


def test_a_timed_out_probe_retries_once_before_standing_down(monkeypatch):
    """The first child may just be cold-starting the native library (#1014)."""
    import time

    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    timeout_error = subprocess.TimeoutExpired("probe", 10.0)
    timeout_error.stderr = "warm-up output"
    run = _install_runner(
        monkeypatch,
        _ScriptedRun(
            {"error": timeout_error},
            {"payload": {"outcome": "success", "detail": None}},
        ),
    )

    started = time.monotonic()
    result = _probe_backend("cpu", timeout=10.0)

    assert run.calls == 2, "one timeout must not be the final answer"
    assert time.monotonic() - started < 5
    assert result.outcome is KernelProbeOutcome.SUCCESS


def test_a_second_timeout_still_stands_down_and_says_why(monkeypatch):
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    def timed_out(_cmd, _timeout):
        error = subprocess.TimeoutExpired("probe", 10.0)
        error.stderr = "compiling kernel: stuck\n"
        return error

    run = _install_runner(
        monkeypatch,
        _ScriptedRun(
            {"error": timed_out("probe", 10.0)},
            {"error": timed_out("probe", 10.0)},
        ),
    )

    result = _probe_backend("cpu", timeout=10.0)

    assert run.calls == 2, "retry once, not forever"
    assert result.outcome is KernelProbeOutcome.TIMED_OUT
    assert result.detail is not None and "stuck" in result.detail, (
        "the child's own output is the only clue to why it never finished"
    )


def test_a_crashed_child_names_its_own_last_words(monkeypatch):
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(
        monkeypatch,
        _ScriptedRun(
            {"payload": None, "returncode": 134, "stderr": "terminate called after throwing"}
        ),
    )

    result = _probe_backend("cpu", timeout=10.0)

    assert result.outcome is KernelProbeOutcome.CHILD_SIGNALLED
    assert result.detail == "SIGABRT"
    assert result.stderr is not None and "terminate called" in result.stderr, (
        "the child's own output is the only clue to why it aborted"
    )


def test_a_crash_is_not_retried(monkeypatch):
    from immich_memories.titles.kernel_backend_probe import _probe_backend

    run = _install_runner(
        monkeypatch,
        _ScriptedRun({"payload": None, "returncode": 1, "stderr": "boom"}),
    )

    _probe_backend("cpu", timeout=10.0)

    assert run.calls == 1, "a crash is an answer, not a cold start"


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
    module for both would leave the real arch objects in play.
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
    monkeypatch.setattr(kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs))
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
        lambda backend: KernelProbeResult(
            KernelProbeOutcome.SUCCESS if backend == "cpu" else KernelProbeOutcome(outcome)
        ),
    )
    monkeypatch.setattr(kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs))
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: None)

    assert kernels.init_kernels() == "CPU"
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]


def test_forced_cpu_still_proves_the_cpu_can_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """IMMICH_FORCE_CPU chooses the arch, it does not vouch for the processor."""
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        KernelProbeResult,
    )

    kernels, probe_module, fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    probes: list[str] = []
    monkeypatch.setenv("IMMICH_FORCE_CPU", "true")
    monkeypatch.setattr(
        probe_module,
        "_probe_backend",
        lambda backend: probes.append(backend) or KernelProbeResult(KernelProbeOutcome.SUCCESS),
    )
    monkeypatch.setattr(kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs))
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: None)

    assert kernels.init_kernels() == "CPU"
    assert probes == ["cpu"]
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]


@pytest.mark.parametrize("returncode", [132, -4])
def test_probe_names_the_signal_that_killed_the_child(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    """SIGILL is what a CPU without AVX does to the first kernel the library compiles.

    It arrives as -4 straight from `wait`, or as 132 when a shell or a container
    init sits between us and the child. Both are the same death.
    """
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        _probe_backend,
    )

    _install_runner(monkeypatch, _RecordedRun(payload=None, returncode=returncode))

    result = _probe_backend("cpu")

    assert result.outcome is KernelProbeOutcome.CHILD_SIGNALLED
    assert result.detail == "SIGILL"


def test_a_cpu_that_kills_the_probe_child_leaves_no_backend_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-AVX case: the CPU backend is probed like any other, and it loses.

    Before this, CPU was reported as working without ever running a kernel, so
    `init_kernels()` returned "CPU" and the SIGILL landed on the render instead
    of on a probe child.
    """
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        KernelProbeResult,
    )

    kernels, probe_module, _fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    monkeypatch.setattr(
        probe_module,
        "_probe_backend",
        lambda _backend: KernelProbeResult(KernelProbeOutcome.CHILD_SIGNALLED, "SIGILL"),
    )
    monkeypatch.setattr(kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs))
    monkeypatch.setattr(kernels, "_compile_kernels", lambda: None)

    assert kernels.init_kernels() is None
    assert parent_inits == []


def test_the_reason_names_the_illegal_instruction_and_the_renderer_that_takes_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import kernel_dispatch_failure

    _install_runner(monkeypatch, _RecordedRun(payload=None, returncode=132))

    assert kernel_dispatch_failure() == (
        "kernel backend crashed on this CPU: illegal instruction; "
        "titles fall back to the PIL renderer"
    )


def test_a_cpu_that_dispatches_has_no_reason_to_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.kernel_backend_probe import kernel_dispatch_failure

    _install_runner(monkeypatch, _RecordedRun(payload={"outcome": "success", "detail": None}))

    assert kernel_dispatch_failure() is None


def test_the_probe_child_is_spawned_once_for_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """#855: the child is expensive, and a second one would re-enter the CLI."""
    from immich_memories.titles.kernel_backend_probe import (
        kernel_dispatch_failure,
        probe_backend_dispatch,
    )

    spawns: list[list[str]] = []

    def record(command, *, timeout, **_kwargs):  # noqa: ARG001
        spawns.append(list(command))
        Path(command[3]).write_text(json.dumps({"outcome": "success", "detail": None}))
        return subprocess.CompletedProcess(list(command), 0, "", "")

    # WHY: the process boundary again, counted rather than stubbed out.
    monkeypatch.setattr(bounded_process, "run_bounded_process", record)

    assert kernel_dispatch_failure() is None
    assert probe_backend_dispatch("cpu").outcome.value == "success"

    assert len(spawns) == 1


def test_a_probe_that_times_out_says_so_without_naming_a_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.titles.kernel_backend_probe import kernel_dispatch_failure

    _install_runner(
        monkeypatch,
        _RecordedRun(payload=None, error=subprocess.TimeoutExpired("probe", 30.0)),
    )

    reason = kernel_dispatch_failure()

    assert reason is not None
    assert reason.startswith("kernel backend did not start within 30s")
    assert reason.endswith("titles fall back to the PIL renderer")
