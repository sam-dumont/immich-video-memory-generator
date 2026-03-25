"""Tests for GPU buffer kernels and GPUBuffers class (issue #164).

Validates new kernels that replace CPU-side numpy operations with GPU-side
equivalents. All tests run with both GPU and CPU Taichi backends.
"""

from __future__ import annotations

import numpy as np
import pytest


def _init():
    from immich_memories.titles.taichi_kernels import init_taichi

    init_taichi()


class TestCopyField3:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_copies_3channel_array(self):
        from immich_memories.titles.taichi_kernels import _copy_field_3

        src = np.random.default_rng(42).random((4, 6, 3)).astype(np.float32)
        dst = np.zeros_like(src)
        _copy_field_3(src, dst)
        np.testing.assert_array_equal(dst, src)


class TestZeroField4:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_zeros_4channel_array(self):
        from immich_memories.titles.taichi_kernels import _zero_field_4

        arr = np.ones((4, 6, 4), dtype=np.float32)
        _zero_field_4(arr)
        np.testing.assert_array_equal(arr, 0.0)


class TestBlendFields:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_blend_50_50(self):
        from immich_memories.titles.taichi_kernels import _blend_fields

        a = np.full((4, 6, 3), 0.0, dtype=np.float32)
        b = np.full((4, 6, 3), 1.0, dtype=np.float32)
        _blend_fields(a, b, 0.5)
        np.testing.assert_allclose(a, 0.5, atol=1e-6)

    def test_blend_fully_a(self):
        from immich_memories.titles.taichi_kernels import _blend_fields

        a = np.full((4, 6, 3), 0.2, dtype=np.float32)
        b = np.full((4, 6, 3), 0.8, dtype=np.float32)
        _blend_fields(a, b, 0.0)
        np.testing.assert_allclose(a, 0.2, atol=1e-6)


class TestFinalizeToOutput:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_clips_and_scales_to_uint8(self):
        from immich_memories.titles.taichi_kernels import _finalize_to_output

        frame = np.array([[[0.0, 0.5, 1.0], [-0.1, 1.2, 0.75]]], dtype=np.float32)
        output = np.zeros_like(frame, dtype=np.uint8)
        _finalize_to_output(frame, output, 255.0)
        # WHY: atol=1 for cross-backend floating point rounding differences
        expected = np.array([[[0, 128, 255], [0, 255, 191]]], dtype=np.uint8)
        np.testing.assert_allclose(output, expected, atol=1)

    def test_scales_to_uint16_hdr(self):
        from immich_memories.titles.taichi_kernels import _finalize_to_output

        frame = np.array([[[0.0, 0.5, 1.0]]], dtype=np.float32)
        output = np.zeros_like(frame, dtype=np.uint16)
        _finalize_to_output(frame, output, 65535.0)
        expected = np.array([[[0, 32768, 65535]]], dtype=np.uint16)
        np.testing.assert_allclose(output, expected, atol=1)


class TestFusedVignetteNoise:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_matches_sequential_within_tolerance(self):
        """Fused result should match sequential vignette then noise."""
        from immich_memories.titles.taichi_kernels import (
            _apply_noise_grain,
            _apply_vignette,
            _apply_vignette_and_noise,
        )

        rng = np.random.default_rng(42)
        frame = rng.random((64, 64, 3)).astype(np.float32) * 0.8

        seq = frame.copy()
        _apply_vignette(seq, 0.3, 64, 64)
        _apply_noise_grain(seq, 0.025, 12345, 64, 64)

        fused = frame.copy()
        _apply_vignette_and_noise(fused, 0.3, 0.025, 12345, 64, 64)

        np.testing.assert_allclose(fused, seq, atol=1e-5)

    def test_noise_disabled(self):
        """When noise_intensity=0, result matches vignette-only."""
        from immich_memories.titles.taichi_kernels import (
            _apply_vignette,
            _apply_vignette_and_noise,
        )

        frame = np.full((16, 16, 3), 0.5, dtype=np.float32)

        vig_only = frame.copy()
        _apply_vignette(vig_only, 0.3, 16, 16)

        fused = frame.copy()
        _apply_vignette_and_noise(fused, 0.3, 0.0, 0, 16, 16)

        np.testing.assert_allclose(fused, vig_only, atol=1e-6)


class TestGPUBuffers:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _init()

    def test_allocates_correct_shapes(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=64, w=128, hdr=False)
        assert gpu.frame.shape == (64, 128, 3)
        assert gpu.temp.shape == (64, 128, 3)
        assert gpu.bokeh.shape == (64, 128, 4)
        assert gpu.output.shape == (64, 128, 3)

    def test_load_background_roundtrips(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=4, w=6, hdr=False)
        bg = np.random.default_rng(42).random((4, 6, 3)).astype(np.float32)
        gpu.load_background(bg)
        result = gpu.frame.to_numpy()
        np.testing.assert_allclose(result, bg, atol=1e-6)

    def test_read_output_returns_uint8(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=4, w=6, hdr=False)
        result = gpu.read_output()
        assert result.dtype == np.uint8
        assert result.shape == (4, 6, 3)

    def test_hdr_output_is_uint16(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=4, w=6, hdr=True)
        result = gpu.read_output()
        assert result.dtype == np.uint16

    def test_ensure_sharp_creates_buffer(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=8, w=12, hdr=False)
        assert gpu.sharp is None
        gpu.ensure_sharp()
        assert gpu.sharp is not None
        assert gpu.sharp.shape == (8, 12, 3)

    def test_ensure_sharp_idempotent(self):
        from immich_memories.titles.taichi_kernels import GPUBuffers

        gpu = GPUBuffers(h=8, w=12, hdr=False)
        gpu.ensure_sharp()
        first = gpu.sharp
        gpu.ensure_sharp()
        assert gpu.sharp is first
