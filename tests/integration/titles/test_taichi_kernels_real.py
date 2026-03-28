"""Real Taichi kernel execution tests on CPU backend.

Runs actual GPU kernels via Taichi's CPU backend to verify correctness
of gradient generation, Gaussian blur, and float32→uint8 finalization.

Run: make test-integration-titles
"""

from __future__ import annotations

import os

import numpy as np
import pytest

# WHY: Must set CPU backend BEFORE importing Taichi modules.
# Taichi initializes once per process — this ensures it uses CPU.
os.environ["IMMICH_FORCE_CPU"] = "1"

# WHY: Kernels are compiled lazily inside init_taichi(). A direct
# `from .taichi_kernels import _generate_linear_gradient` captures None
# at import time. We import the MODULE and access kernels at call time.
from immich_memories.titles import taichi_kernels  # noqa: E402
from immich_memories.titles.taichi_kernels import (  # noqa: E402
    TAICHI_AVAILABLE,
    GPUBuffers,
    _create_gaussian_kernel,
    init_taichi,
)

requires_taichi = pytest.mark.skipif(not TAICHI_AVAILABLE, reason="Taichi not installed")
pytestmark = [pytest.mark.integration, requires_taichi]

W, H = 160, 90


@pytest.fixture(scope="module")
def _taichi_cpu():
    """Initialize Taichi with CPU backend once per module."""
    backend = init_taichi()
    assert backend is not None, "Taichi failed to initialize on CPU"
    return backend


class TestLinearGradientKernel:
    def test_fills_frame_with_non_uniform_color(self, _taichi_cpu):
        """Linear gradient should produce smoothly varying pixels, not a flat fill."""
        frame = np.zeros((H, W, 3), dtype=np.float32)
        taichi_kernels._generate_linear_gradient(
            frame,
            0.1,
            0.1,
            0.3,
            0.9,
            0.8,
            0.5,
            0.785,
            W,
            H,
        )

        assert frame.std() > 0.05, "Gradient should have spatial variation"
        assert frame.min() >= 0.0
        assert frame.max() <= 1.0

    def test_different_angles_produce_different_output(self, _taichi_cpu):
        """Two gradients with different angles should differ."""
        frame_a = np.zeros((H, W, 3), dtype=np.float32)
        frame_b = np.zeros((H, W, 3), dtype=np.float32)
        taichi_kernels._generate_linear_gradient(
            frame_a,
            0.1,
            0.1,
            0.3,
            0.9,
            0.8,
            0.5,
            0.0,
            W,
            H,
        )
        taichi_kernels._generate_linear_gradient(
            frame_b,
            0.1,
            0.1,
            0.3,
            0.9,
            0.8,
            0.5,
            1.57,
            W,
            H,
        )
        diff = np.abs(frame_a - frame_b).mean()
        assert diff > 0.01, "Different angles should produce visibly different frames"


class TestGaussianBlurKernel:
    def test_blur_reduces_noise(self, _taichi_cpu):
        """Gaussian blur on a noisy image should reduce standard deviation."""
        rng = np.random.default_rng(42)
        noisy = rng.uniform(0.2, 0.8, (H, W, 3)).astype(np.float32)
        original_std = noisy.std()

        kernel = _create_gaussian_kernel(radius=5, sigma=2.0)
        temp = np.zeros_like(noisy)
        output = np.zeros_like(noisy)

        taichi_kernels._gaussian_blur_h(noisy, temp, kernel, 5)
        taichi_kernels._gaussian_blur_v(temp, output, kernel, 5)

        blurred_std = output.std()
        assert blurred_std < original_std * 0.8, (
            f"Blur should reduce noise: {blurred_std:.4f} vs original {original_std:.4f}"
        )

    def test_blur_preserves_value_range(self, _taichi_cpu):
        """Blur output should stay within the input value range."""
        frame = np.full((H, W, 3), 0.5, dtype=np.float32)
        frame[H // 2, W // 2, :] = 1.0

        kernel = _create_gaussian_kernel(radius=3)
        temp = np.zeros_like(frame)
        output = np.zeros_like(frame)

        taichi_kernels._gaussian_blur_h(frame, temp, kernel, 3)
        taichi_kernels._gaussian_blur_v(temp, output, kernel, 3)

        assert output.min() >= 0.0
        assert output.max() <= 1.0


class TestFinalizeToU8:
    def test_converts_float32_to_uint8(self, _taichi_cpu):
        """Finalize kernel should map [0.0, 1.0] float32 to [0, 255] uint8."""
        frame = np.zeros((H, W, 3), dtype=np.float32)
        frame[0, 0, :] = 0.0
        frame[0, 1, :] = 0.5
        frame[0, 2, :] = 1.0

        output = np.zeros((H, W, 3), dtype=np.uint8)
        taichi_kernels._finalize_to_output_u8(frame, output, 255.0)

        assert output[0, 0, 0] == 0, "Black should map to 0"
        assert 126 <= output[0, 1, 0] <= 128, f"Mid-gray mapped to {output[0, 1, 0]}"
        assert output[0, 2, 0] == 255, "White should map to 255"

    def test_clamps_out_of_range_values(self, _taichi_cpu):
        """Values outside [0, 1] should be clamped before conversion."""
        frame = np.zeros((H, W, 3), dtype=np.float32)
        frame[0, 0, :] = -0.5
        frame[0, 1, :] = 1.5

        output = np.zeros((H, W, 3), dtype=np.uint8)
        taichi_kernels._finalize_to_output_u8(frame, output, 255.0)

        assert output[0, 0, 0] == 0, "Negative should clamp to 0"
        assert output[0, 1, 0] == 255, "Over-1.0 should clamp to 255"


class TestGPUBuffers:
    def test_allocates_correct_shapes(self, _taichi_cpu):
        """GPUBuffers should allocate frame, temp, bokeh, and output buffers."""
        gpu = GPUBuffers(H, W)
        assert gpu.h == H
        assert gpu.w == W
        assert gpu.hdr is False

    def test_load_and_read_roundtrip(self, _taichi_cpu):
        """Load a numpy background, finalize, and read back output."""
        gpu = GPUBuffers(H, W)

        bg = np.full((H, W, 3), 0.6, dtype=np.float32)
        gpu.load_background(bg)

        taichi_kernels._finalize_to_output_u8(gpu.frame, gpu.output, 255.0)
        result = gpu.read_output()

        assert result.shape == (H, W, 3)
        assert result.dtype == np.uint8
        # 0.6 * 255 + 0.5 = 153.5 -> 153 or 154
        assert 152 <= result[H // 2, W // 2, 0] <= 154
