"""The log has to distinguish a GPU from the kernel library's CPU fallback.

init_kernels() returns the string "CPU" when Metal, CUDA and Vulkan all fail
to start. The service logged "GPU rendering enabled: CPU", so a container
rendering titles on the processor looked identical to one using the card —
and titles are the most expensive stage in the pipeline.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.titles.rendering_service import RenderingService


@pytest.fixture
def config() -> MagicMock:
    cfg = MagicMock()
    cfg.use_gpu_rendering = True
    return cfg


@pytest.mark.parametrize("backend", ["Metal", "CUDA", "Vulkan"])
def test_a_real_gpu_is_reported_as_one(backend: str, config, caplog) -> None:
    # WHY: the kernel library and the GPU it finds are the boundary; this asserts the log line.
    with (
        patch("immich_memories.titles.rendering_service.KERNELS_AVAILABLE", True),
        patch("immich_memories.titles.rendering_service.init_kernels", return_value=backend),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert service.backend == backend
    assert f"on GPU: {backend}" in caplog.text
    assert "CPU" not in caplog.text


def test_the_cpu_fallback_says_so_and_warns(config, caplog) -> None:
    # WHY: same boundary; a machine with working Metal can never reach this case.
    with (
        patch("immich_memories.titles.rendering_service.KERNELS_AVAILABLE", True),
        patch("immich_memories.titles.rendering_service.init_kernels", return_value="CPU"),
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
    """The no-AVX case (#910): no backend at all, and the log has to say why.

    "Kernel library unavailable" on its own sent a NAS user looking for a missing
    package that was installed and imported fine.
    """
    crash = (
        "kernel backend crashed on this CPU: illegal instruction; "
        "titles fall back to the PIL renderer"
    )
    # WHY: the kernel library boundary again, answering as a Celeron J4125 does.
    with (
        patch("immich_memories.titles.rendering_service.KERNELS_AVAILABLE", True),
        patch("immich_memories.titles.rendering_service.init_kernels", return_value=None),
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
