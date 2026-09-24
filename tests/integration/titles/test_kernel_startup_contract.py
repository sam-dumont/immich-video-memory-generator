"""The title renderer starts on this machine's GPU, cold, within its budget (#1171).

Both GPU lanes of #956 shipped PIL titles: the M5 Max's first probe ran out of
time and the T1000 pod aborted writing its kernel cache. Nothing failed, because
falling back is by design, so only a test that demands the GPU backend can see it.

Runs in a child interpreter with an empty HOME: the kernel library initialises
once per process, and a warm kernel cache would hide the cold start a fresh
install pays.

Run: make test-integration-titles
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from immich_memories.titles.kernel_backend_probe import (
    _PROBE_TIMEOUT_SECONDS,
    kernel_library_installed,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not kernel_library_installed(), reason="no kernel library wheel here"),
]

_START = """
import json, logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
from immich_memories.titles.rendering_service import load_kernel_renderer
renderer = load_kernel_renderer()
backend = renderer.init_kernels() if renderer else None
failures = list(renderer.gpu_failures()) if renderer else []
print(json.dumps({"backend": backend, "failures": failures}))
"""


def _expected_gpu() -> str | None:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Metal"
    if platform.system() == "Linux" and shutil.which("nvidia-smi"):
        return "CUDA"
    return None


def _no_gpu_device() -> bool:
    """A Linux box with no card passed in: a container on Docker Desktop, or a bare CI runner."""
    return (
        platform.system() == "Linux"
        and shutil.which("nvidia-smi") is None
        and not Path("/dev/dri").exists()
    )


def _cold_start(home: Path) -> tuple[dict, str, float]:
    """Start the title renderer in a fresh interpreter: where it landed, its log, and how long."""
    env = {k: v for k, v in os.environ.items() if k != "IMMICH_FORCE_CPU"}
    env["HOME"] = str(home)

    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", _START],
        env=env,
        capture_output=True,
        text=True,
        timeout=4 * _PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1]), completed.stderr, elapsed


def test_the_title_renderer_starts_on_the_gpu_within_its_budget(tmp_path: Path) -> None:
    expected = _expected_gpu()
    if expected is None:
        pytest.skip("no GPU this test knows how to demand here")

    started_on, log, elapsed = _cold_start(tmp_path)

    assert started_on["backend"] == expected, (started_on, log[-2000:])
    assert elapsed < _PROBE_TIMEOUT_SECONDS, f"cold start took {elapsed:.1f}s"


@pytest.mark.skipif(not _no_gpu_device(), reason="only a Linux box with no GPU passed in")
def test_a_container_without_a_gpu_says_its_titles_run_on_the_cpu(tmp_path: Path) -> None:
    """The kernel library starts on the CPU when asked for a device it cannot find.

    It says so only in a warning, and the probe used to take the working kernel
    as proof: a Docker container with no card logged "on the CUDA backend" and
    rendered on its processor (#1202). Here the kernels still run, on the CPU,
    and every GPU backend passed over is named with its reason.
    """
    started_on, log, elapsed = _cold_start(tmp_path)

    assert started_on["backend"] == "CPU", (started_on, log[-2000:])
    assert [failure.split(":")[0] for failure in started_on["failures"]] == ["CUDA", "Vulkan"]
    assert elapsed < _PROBE_TIMEOUT_SECONDS, f"cold start took {elapsed:.1f}s"
