"""The slow-mo blend belongs on the device, and must land in the same place.

The background of a content-backed title interpolates between a handful of
source frames — 15 of them serve 105 output frames. That blend ran in numpy,
ten passes over 25 MB arrays per output frame, and the f32 result was uploaded
every frame: 14.9ms per frame against 3.3ms for a plain gradient. The sources
never change, so they belong on the device.
"""

from __future__ import annotations

import numpy as np
import pytest

ti_kernels = pytest.importorskip("immich_memories.titles.taichi_kernels")


@pytest.fixture(scope="module")
def taichi_ready() -> bool:
    if not ti_kernels.init_taichi():
        pytest.skip("Taichi has no working backend here")
    return True


def _numpy_catmull_rom(sources: list[np.ndarray], window: tuple, t: float) -> np.ndarray:
    """The interpolation the GPU kernel replaces, kept here as the reference."""
    p0, p1, p2, p3 = (sources[i].astype(np.float32) / 255.0 for i in window)
    out = 0.5 * (
        2.0 * p1
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (t * t)
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (t * t * t)
    )
    return np.clip(out, 0.0, 1.0)


def test_the_device_blend_matches_the_one_it_replaces(taichi_ready: bool) -> None:
    rng = np.random.default_rng(7)
    height, width, count = 64, 96, 8
    sources = [rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8) for _ in range(count)]
    gpu = ti_kernels.GPUBuffers(height, width)
    assert gpu.load_sources(sources)

    worst = 0.0
    for idx in range(1, count - 2):
        for t in (0.0, 0.25, 0.5, 0.9):
            window = (idx - 1, idx, idx + 1, idx + 2)
            gpu.blend_sources(window, t)
            reference = _numpy_catmull_rom(sources, window, t)
            worst = max(worst, float(np.abs(gpu.frame.to_numpy() - reference).max()))

    assert worst < 1e-5, f"device blend drifted from the reference by {worst}"


def test_sources_of_the_wrong_shape_are_refused(taichi_ready: bool) -> None:
    """A refusal keeps the numpy path; a wrong-shaped upload would corrupt frames."""
    gpu = ti_kernels.GPUBuffers(64, 96)

    assert not gpu.load_sources([])
    assert not gpu.load_sources([np.zeros((10, 10, 3), dtype=np.uint8)])
    assert not gpu.load_sources([np.zeros((64, 96, 3), dtype=np.float32)])
