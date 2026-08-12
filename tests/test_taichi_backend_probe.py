"""Contracts for isolated Taichi backend dispatch probing."""

from __future__ import annotations

import multiprocessing
import platform
import queue
from types import SimpleNamespace

import pytest


class _FakeQueue:
    def __init__(self, *items: object) -> None:
        self.items = list(items)
        self.closed = False
        self.joined = False

    def get(self, timeout: float) -> object:  # noqa: ARG002
        if not self.items:
            raise queue.Empty
        return self.items.pop(0)

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class _FakeProcess:
    def __init__(self, *, alive: bool = False, exitcode: int | None = 0) -> None:
        self.alive = alive
        self.exitcode = exitcode
        self.started = False
        self.terminated = False
        self.killed = False
        self.closed = False
        self.join_timeouts: list[float] = []

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float) -> None:
        self.join_timeouts.append(timeout)

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.alive = False
        self.exitcode = -9

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, result_queue: _FakeQueue, process: _FakeProcess) -> None:
        self.result_queue = result_queue
        self.process = process
        self.process_kwargs: dict[str, object] | None = None

    def Queue(self, maxsize: int) -> _FakeQueue:  # noqa: N802
        assert maxsize == 1
        return self.result_queue

    def Process(self, **kwargs: object) -> _FakeProcess:  # noqa: N802
        self.process_kwargs = kwargs
        return self.process


def _install_context(
    monkeypatch: pytest.MonkeyPatch,
    result_queue: _FakeQueue,
    process: _FakeProcess,
) -> _FakeContext:
    context = _FakeContext(result_queue, process)

    def get_context(method: str) -> _FakeContext:
        assert method == "spawn"
        return context

    monkeypatch.setattr(multiprocessing, "get_context", get_context)
    return context


def test_probe_returns_success_and_closes_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        TaichiProbeResult,
        _probe_taichi_backend,
        _taichi_probe_worker,
    )

    result_queue = _FakeQueue(TaichiProbeResult(TaichiProbeOutcome.SUCCESS))
    process = _FakeProcess()
    context = _install_context(monkeypatch, result_queue, process)

    result = _probe_taichi_backend("metal", timeout=2.5)

    assert result.outcome is TaichiProbeOutcome.SUCCESS
    assert process.started
    assert process.join_timeouts == [2.5]
    assert process.closed
    assert result_queue.closed
    assert result_queue.joined
    assert context.process_kwargs == {
        "target": _taichi_probe_worker,
        "args": ("metal", result_queue),
        "name": "taichi-metal-probe",
        "daemon": True,
    }


def test_probe_preserves_dispatch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        TaichiProbeResult,
        _probe_taichi_backend,
    )

    result_queue = _FakeQueue(TaichiProbeResult(TaichiProbeOutcome.DISPATCH_FAILED, "RuntimeError"))
    process = _FakeProcess()
    _install_context(monkeypatch, result_queue, process)

    result = _probe_taichi_backend("cuda")

    assert result == TaichiProbeResult(TaichiProbeOutcome.DISPATCH_FAILED, "RuntimeError")
    assert process.closed
    assert result_queue.closed
    assert result_queue.joined


def test_probe_reports_child_crash_without_a_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        _probe_taichi_backend,
    )

    result_queue = _FakeQueue()
    process = _FakeProcess(exitcode=7)
    _install_context(monkeypatch, result_queue, process)

    result = _probe_taichi_backend("vulkan")

    assert result.outcome is TaichiProbeOutcome.CHILD_CRASHED
    assert result.detail == "exitcode=7"
    assert process.closed
    assert result_queue.closed
    assert result_queue.joined


def test_probe_terminates_then_kills_a_hung_child(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        _probe_taichi_backend,
    )

    result_queue = _FakeQueue()
    process = _FakeProcess(alive=True, exitcode=None)
    _install_context(monkeypatch, result_queue, process)

    result = _probe_taichi_backend("metal", timeout=4.0)

    assert result.outcome is TaichiProbeOutcome.TIMED_OUT
    assert process.terminated
    assert process.killed
    assert process.join_timeouts == [4.0, 1.0, 1.0]
    assert process.closed
    assert result_queue.closed
    assert result_queue.joined


def _prepare_parent_init(monkeypatch: pytest.MonkeyPatch):
    from immich_memories.titles import taichi_kernels

    fake_ti = SimpleNamespace(metal=object(), cpu=object())
    monkeypatch.setattr(taichi_kernels, "TAICHI_AVAILABLE", True)
    monkeypatch.setattr(taichi_kernels, "ti", fake_ti)
    monkeypatch.setattr(taichi_kernels, "_taichi_initialized", False)
    monkeypatch.setattr(taichi_kernels, "_taichi_backend", None)
    monkeypatch.setattr(taichi_kernels, "SDF_AVAILABLE", False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.delenv("IMMICH_FORCE_CPU", raising=False)
    return taichi_kernels, fake_ti


def test_successful_gpu_probe_initializes_parent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        TaichiProbeResult,
    )

    taichi_kernels, fake_ti = _prepare_parent_init(monkeypatch)
    probes: list[str] = []
    parent_inits: list[dict[str, object]] = []
    compile_calls: list[bool] = []
    monkeypatch.setattr(
        taichi_kernels,
        "_probe_taichi_backend",
        lambda backend: probes.append(backend) or TaichiProbeResult(TaichiProbeOutcome.SUCCESS),
    )
    monkeypatch.setattr(
        taichi_kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(taichi_kernels, "_compile_kernels", lambda: compile_calls.append(True))

    assert taichi_kernels.init_taichi() == "Metal"
    assert taichi_kernels.init_taichi() == "Metal"

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
    from immich_memories.titles.taichi_kernels import (
        TaichiProbeOutcome,
        TaichiProbeResult,
    )

    taichi_kernels, fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    monkeypatch.setattr(
        taichi_kernels,
        "_probe_taichi_backend",
        lambda _backend: TaichiProbeResult(TaichiProbeOutcome(outcome)),
    )
    monkeypatch.setattr(
        taichi_kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(taichi_kernels, "_compile_kernels", lambda: None)

    assert taichi_kernels.init_taichi() == "CPU"
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]


def test_forced_cpu_never_spawns_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    taichi_kernels, fake_ti = _prepare_parent_init(monkeypatch)
    parent_inits: list[dict[str, object]] = []
    monkeypatch.setenv("IMMICH_FORCE_CPU", "true")
    monkeypatch.setattr(
        taichi_kernels,
        "_probe_taichi_backend",
        lambda _backend: pytest.fail("CPU fallback must not spawn a child"),
    )
    monkeypatch.setattr(
        taichi_kernels, "_silent_init", lambda **kwargs: parent_inits.append(kwargs)
    )
    monkeypatch.setattr(taichi_kernels, "_compile_kernels", lambda: None)

    assert taichi_kernels.init_taichi() == "CPU"
    assert parent_inits == [{"arch": fake_ti.cpu, "offline_cache": True}]
