"""Tests for frame blending (numpy fallback path)."""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.processing.transition_blend import (
    _blend_frames_numpy,
    blend_frames_batch_gpu,
    blend_frames_gpu,
)


class TestBlendFramesNumpy:
    """Tests for the CPU numpy blending fallback."""

    def test_alpha_zero_returns_frame_a(self):
        """Alpha 0.0 returns frame_a unchanged."""
        a = np.full((2, 2, 3), 100, dtype=np.uint8)
        b = np.full((2, 2, 3), 200, dtype=np.uint8)
        result = _blend_frames_numpy(a, b, alpha=0.0, dtype=np.dtype(np.uint8), max_val=255.0)
        np.testing.assert_array_equal(result, a)

    def test_alpha_one_returns_frame_b(self):
        """Alpha 1.0 returns frame_b unchanged."""
        a = np.full((2, 2, 3), 100, dtype=np.uint8)
        b = np.full((2, 2, 3), 200, dtype=np.uint8)
        result = _blend_frames_numpy(a, b, alpha=1.0, dtype=np.dtype(np.uint8), max_val=255.0)
        np.testing.assert_array_equal(result, b)

    def test_alpha_half_averages(self):
        """Alpha 0.5 produces the average of the two frames."""
        a = np.full((2, 2, 3), 100, dtype=np.uint8)
        b = np.full((2, 2, 3), 200, dtype=np.uint8)
        result = _blend_frames_numpy(a, b, alpha=0.5, dtype=np.dtype(np.uint8), max_val=255.0)
        np.testing.assert_array_equal(result, np.full((2, 2, 3), 150, dtype=np.uint8))

    def test_uint16_blending(self):
        """Blending works for uint16 frames."""
        a = np.full((2, 2, 3), 1000, dtype=np.uint16)
        b = np.full((2, 2, 3), 3000, dtype=np.uint16)
        result = _blend_frames_numpy(a, b, alpha=0.5, dtype=np.dtype(np.uint16), max_val=65535.0)
        np.testing.assert_array_equal(result, np.full((2, 2, 3), 2000, dtype=np.uint16))

    def test_float32_passthrough(self):
        """Float32 frames are returned without clipping to max_val."""
        a = np.full((2, 2, 3), 0.2, dtype=np.float32)
        b = np.full((2, 2, 3), 0.8, dtype=np.float32)
        result = _blend_frames_numpy(a, b, alpha=0.5, dtype=np.dtype(np.float32), max_val=1.0)
        np.testing.assert_allclose(result, np.full((2, 2, 3), 0.5, dtype=np.float32))

    def test_preserves_dtype(self):
        """Output dtype matches input dtype."""
        for dtype in [np.uint8, np.uint16, np.float32]:
            a = np.zeros((2, 2, 3), dtype=dtype)
            b = np.ones((2, 2, 3), dtype=dtype)
            result = _blend_frames_numpy(a, b, alpha=0.5, dtype=np.dtype(dtype), max_val=255.0)
            assert result.dtype == dtype

    def test_clips_overflow(self):
        """Values are clipped to [0, max_val] for integer dtypes."""
        a = np.full((2, 2, 3), 250, dtype=np.uint8)
        b = np.full((2, 2, 3), 250, dtype=np.uint8)
        result = _blend_frames_numpy(a, b, alpha=0.5, dtype=np.dtype(np.uint8), max_val=255.0)
        assert result.max() <= 255


class TestBlendFramesGpuValidation:
    """Tests for input validation in blend_frames_gpu."""

    def test_shape_mismatch_raises(self):
        """Mismatched frame shapes raise ValueError."""
        a = np.zeros((2, 2, 3), dtype=np.uint8)
        b = np.zeros((3, 3, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="shapes must match"):
            blend_frames_gpu(a, b, alpha=0.5)

    def test_dtype_mismatch_raises(self):
        """Mismatched frame dtypes raise ValueError."""
        a = np.zeros((2, 2, 3), dtype=np.uint8)
        b = np.zeros((2, 2, 3), dtype=np.uint16)
        with pytest.raises(ValueError, match="dtypes must match"):
            blend_frames_gpu(a, b, alpha=0.5)

    def test_unsupported_dtype_raises(self):
        """Unsupported dtype raises ValueError."""
        a = np.zeros((2, 2, 3), dtype=np.int32)
        b = np.zeros((2, 2, 3), dtype=np.int32)
        with pytest.raises(ValueError, match="Unsupported dtype"):
            blend_frames_gpu(a, b, alpha=0.5)

    def test_falls_back_to_numpy(self):
        """Without GPU, blend_frames_gpu falls back to numpy and produces correct output."""
        a = np.full((4, 4, 3), 50, dtype=np.uint8)
        b = np.full((4, 4, 3), 150, dtype=np.uint8)
        result = blend_frames_gpu(a, b, alpha=0.5)
        np.testing.assert_array_equal(result, np.full((4, 4, 3), 100, dtype=np.uint8))


class TestBlendFramesBatchGpu:
    """Tests for batch blending."""

    def test_mismatched_lengths_raises(self):
        """Mismatched list lengths raise ValueError."""
        a = [np.zeros((2, 2, 3), dtype=np.uint8)]
        b = [np.zeros((2, 2, 3), dtype=np.uint8), np.zeros((2, 2, 3), dtype=np.uint8)]
        with pytest.raises(ValueError, match="same length"):
            blend_frames_batch_gpu(a, b, [0.5, 0.5])

    def test_batch_produces_correct_count(self):
        """Batch returns one result per input pair."""
        frames = [np.full((2, 2, 3), i * 50, dtype=np.uint8) for i in range(3)]
        results = blend_frames_batch_gpu(frames, frames, [0.5] * 3)
        assert len(results) == 3

    def test_empty_batch(self):
        """Empty input lists return empty output."""
        results = blend_frames_batch_gpu([], [], [])
        assert results == []
