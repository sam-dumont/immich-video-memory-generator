"""The log has to distinguish a GPU from the kernel library's CPU fallback.

init_kernels() returns the string "CPU" when Metal, CUDA and Vulkan all fail
to start. The service logged "GPU rendering enabled: CPU", so a container
rendering titles on the processor looked identical to one using the card —
and titles are the most expensive stage in the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.titles.rendering_service import KernelRenderer, RenderingService


@pytest.fixture
def config() -> MagicMock:
    cfg = MagicMock()
    cfg.use_gpu_rendering = True
    return cfg


def _renderer_reporting(backend: str | None) -> KernelRenderer:
    """A loaded kernel renderer whose init_kernels() answers with `backend`."""
    return KernelRenderer(
        create_video=lambda *args, **kwargs: Path("unused"),  # noqa: ARG005
        config_type=MagicMock,
        init_kernels=lambda: backend,
    )


@pytest.mark.parametrize("backend", ["Metal", "CUDA", "Vulkan"])
def test_a_real_gpu_is_reported_as_one(backend: str, config, caplog) -> None:
    # WHY: loading the kernel renderer is the boundary — it claims a GPU and, on
    # a processor without AVX, kills the interpreter. This asserts the log line.
    with (
        patch(
            "immich_memories.titles.rendering_service.load_kernel_renderer",
            return_value=_renderer_reporting(backend),
        ),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert service.backend == backend
    assert f"on GPU: {backend}" in caplog.text
    assert "CPU" not in caplog.text


def test_the_cpu_fallback_says_so_and_warns(config, caplog) -> None:
    # WHY: same boundary; a machine with working Metal can never reach this case.
    with (
        patch(
            "immich_memories.titles.rendering_service.load_kernel_renderer",
            return_value=_renderer_reporting("CPU"),
        ),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert service.use_gpu, "the GPU renderer is still the right one: it does the deblur"
    assert service.backend == "CPU"
    assert "on CPU" in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records), (
        "a silent CPU fallback is the bug"
    )


def test_a_cpu_that_cannot_run_a_kernel_falls_to_pil_with_its_reason(config, caplog) -> None:
    """The no-AVX case (#910): no renderer is loaded at all, and the log says why.

    "Kernel library unavailable" on its own sent a NAS user looking for a missing
    package that was installed and imported fine. Nothing is patched at the
    rendering-service boundary here: the real `load_kernel_renderer` has to
    answer None off the probe alone, without reaching the import behind it.
    """
    crash = (
        "kernel backend crashed on this CPU: illegal instruction; "
        "titles fall back to the PIL renderer"
    )
    # WHY: the probe spawns a child interpreter; this is the answer a Celeron J4125 gives.
    with (
        patch(
            "immich_memories.titles.kernel_backend_probe.kernel_dispatch_failure",
            return_value=crash,
        ),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert not service.use_gpu
    assert service.backend is None
    assert "illegal instruction" in caplog.text
