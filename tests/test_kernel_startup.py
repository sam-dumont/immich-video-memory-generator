"""The title kernels have to start where the app is deployed (#1171).

Two lanes of #956 never got the kernel renderer: a Kubernetes pod, whose root
filesystem is read-only, and a fresh install on an M5 Max, whose first probe ran
out of time. These are the startup conditions each of them hit.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _forget_probed_backends() -> Iterator[None]:
    from immich_memories.titles.kernel_backend_probe import kernel_cache_dir, probe_backend_dispatch

    probe_backend_dispatch.cache_clear()
    kernel_cache_dir.cache_clear()
    yield
    probe_backend_dispatch.cache_clear()
    kernel_cache_dir.cache_clear()


def _read_only(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return path


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root ignores modes")
def test_kernel_cache_leaves_a_read_only_home_for_the_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pod's root filesystem is read-only; the library aborts if it cannot write its cache."""
    from immich_memories.titles.kernel_backend_probe import kernel_cache_dir

    home = _read_only(tmp_path / "home")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # WHY: gettempdir() memoises the first answer; the temp dir is set, not the env.
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    try:
        chosen = kernel_cache_dir()
    finally:
        home.chmod(stat.S_IRWXU)

    assert chosen is not None
    assert chosen.is_relative_to(scratch)
    assert os.access(chosen, os.W_OK)


def test_kernel_cache_lives_with_the_app_data_when_home_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """~/.immich-memories is the one directory every deployment keeps writable."""
    from immich_memories.titles.kernel_backend_probe import kernel_cache_dir

    monkeypatch.setenv("HOME", str(tmp_path))

    assert kernel_cache_dir() == tmp_path / ".immich-memories" / "cache" / "kernels"


_FAKE_LIBRARY = """
import json, os, sys

cpu = object()
i32 = "i32"


class types:
    @staticmethod
    def ndarray(**_kwargs):
        return object()


def init(**kwargs):
    with open(os.environ["FAKE_KERNEL_REPORT"], "w") as report:
        json.dump(
            {
                "app_loaded": sorted(m for m in sys.modules if m.startswith("immich_memories")),
                "cache": kwargs.get("offline_cache_file_path"),
            },
            report,
        )


def kernel(_fn):
    def run(values):
        values[0] += 1

    return run
"""


def test_the_probe_child_loads_the_kernel_library_and_nothing_of_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold first start on a fresh install spent 4.5 of its 8 s importing the app (#1171).

    The child needs the kernel library and numpy. Importing the application to
    reach the library paid for pydantic, the config and the whole titles package
    before the first kernel, on a clock the fresh install could not beat.
    """
    from immich_memories.titles.kernel_backend_probe import (
        KernelProbeOutcome,
        probe_backend_dispatch,
    )

    library = tmp_path / "library"
    (library / "quadrants").mkdir(parents=True)
    (library / "quadrants" / "__init__.py").write_text(_FAKE_LIBRARY)
    report = tmp_path / "report.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    # WHY: the child is a real interpreter; the fake library shadows the real one there.
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(library), *sys.path]))
    monkeypatch.setenv("FAKE_KERNEL_REPORT", str(report))

    result = probe_backend_dispatch("cpu")

    assert result.outcome is KernelProbeOutcome.SUCCESS, result
    seen = json.loads(report.read_text())
    assert seen["app_loaded"] == []
    assert seen["cache"] == str(tmp_path / ".immich-memories" / "cache" / "kernels")


def test_the_probe_budget_outlasts_a_measured_cold_first_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh install's first probe took 23 s on an M5 Max; 10 s declared the GPU dead."""
    from immich_memories.operations import bounded_process
    from immich_memories.titles.kernel_backend_probe import probe_backend_dispatch

    budgets: list[float] = []

    def record(command, *, timeout, **_kwargs):
        budgets.append(timeout)
        Path(command[3]).write_text(json.dumps({"outcome": "success", "detail": None}))
        return subprocess.CompletedProcess(list(command), 0, "", "")

    # WHY: the process boundary; this asks what budget the child is given, not what it does.
    monkeypatch.setattr(bounded_process, "run_bounded_process", record)

    probe_backend_dispatch("metal")

    assert budgets and budgets[0] > 23.0


def test_a_gpu_that_cannot_start_leaves_one_warning_naming_it(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The T1000 pod rendered titles on its processor and the log never said why."""
    import logging
    from unittest.mock import MagicMock

    from immich_memories.titles import rendering_service
    from immich_memories.titles.rendering_service import KernelRenderer, RenderingService

    renderer = KernelRenderer(
        create_video=MagicMock(),
        config_type=MagicMock,
        init_kernels=lambda: "CPU",
        gpu_failures=lambda: ("CUDA: crashed (sigabrt)",),
    )
    config = MagicMock(use_gpu_rendering=True)
    # WHY: loading the kernel renderer spawns probe children and claims a GPU.
    monkeypatch.setattr(rendering_service, "load_kernel_renderer", lambda: renderer)
    with caplog.at_level(logging.INFO):
        service = RenderingService(config)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert service.use_gpu and service.backend == "CPU"
    assert len(warnings) == 1
    assert "CUDA: crashed (sigabrt)" in warnings[0]
    assert "on CPU" in warnings[0]
