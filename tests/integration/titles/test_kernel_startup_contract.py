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


def test_the_title_renderer_starts_on_the_gpu_within_its_budget(tmp_path: Path) -> None:
    expected = _expected_gpu()
    if expected is None:
        pytest.skip("no GPU this test knows how to demand here")
    env = {k: v for k, v in os.environ.items() if k != "IMMICH_FORCE_CPU"}
    env["HOME"] = str(tmp_path)

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
    started_on = json.loads(completed.stdout.strip().splitlines()[-1])
    assert started_on["backend"] == expected, (started_on, completed.stderr[-2000:])
    assert elapsed < _PROBE_TIMEOUT_SECONDS, f"cold start took {elapsed:.1f}s"
