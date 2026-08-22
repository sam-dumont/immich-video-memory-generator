"""The log has to distinguish a GPU from Taichi's CPU fallback.

init_taichi() returns the string "CPU" when Metal, CUDA and Vulkan all fail
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
    # WHY: Taichi and the GPU it finds are the boundary — this asserts the log line.
    with (
        patch("immich_memories.titles.rendering_service.TAICHI_AVAILABLE", True),
        patch("immich_memories.titles.rendering_service.init_taichi", return_value=backend),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert service.backend == backend
    assert f"on GPU: {backend}" in caplog.text
    assert "CPU" not in caplog.text


def test_the_cpu_fallback_says_so_and_warns(config, caplog) -> None:
    # WHY: same boundary; a machine with working Metal can never reach this case.
    with (
        patch("immich_memories.titles.rendering_service.TAICHI_AVAILABLE", True),
        patch("immich_memories.titles.rendering_service.init_taichi", return_value="CPU"),
        caplog.at_level(logging.INFO),
    ):
        service = RenderingService(config)

    assert service.use_gpu, "the Taichi renderer is still the right one — it does the deblur"
    assert service.backend == "CPU"
    assert "on CPU" in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records), (
        "a silent CPU fallback is the bug"
    )
