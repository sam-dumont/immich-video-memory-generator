"""The crossfade blend: accuracy and dtype coverage (P12).

The blend ran three numpy passes with `casting="unsafe"`, which truncates
toward zero on every element. Measured at 4K that is 37.5 ms a frame against
3.5 ms for one `cv2.addWeighted`, and the truncation costs up to 1.6 levels of
precision that rounding keeps.
"""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.processing.streaming_frame_blender import blend_crossfade


def _exact(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
    return a.astype(np.float64) * (1.0 - alpha) + b.astype(np.float64) * alpha


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_the_blend_rounds_rather_than_truncating(dtype) -> None:
    """Truncating every element loses up to 1.6 levels; rounding keeps 0.5."""
    high = 255 if dtype is np.uint8 else 1023
    rng = np.random.default_rng(0)
    a = rng.integers(0, high, (48, 64, 3), dtype=dtype)
    b = rng.integers(0, high, (48, 64, 3), dtype=dtype)
    out = np.empty_like(a)

    blend_crossfade(a, b, 0.4, out=out)

    assert np.abs(out.astype(np.float64) - _exact(a, b, 0.4)).max() <= 0.5


@pytest.mark.parametrize("alpha,expect_a", [(0.0, True), (1.0, False)])
def test_the_ends_of_the_fade_are_the_source_frames(alpha, expect_a) -> None:
    a = np.full((8, 8, 3), 30, dtype=np.uint8)
    b = np.full((8, 8, 3), 200, dtype=np.uint8)
    out = np.empty_like(a)

    blend_crossfade(a, b, alpha, out=out)

    assert np.array_equal(out, a if expect_a else b)


def test_ten_bit_frames_are_not_clipped_to_eight() -> None:
    """HDR pipes yuv420p10le, so the blend sees uint16 well above 255."""
    a = np.full((8, 8, 3), 900, dtype=np.uint16)
    out = np.empty_like(a)

    blend_crossfade(a, a, 0.5, out=out)

    assert out.max() == 900


def test_a_flat_hdr_buffer_blends_like_an_image() -> None:
    """HDR pre-allocates yuv420p10le as a 1-D uint16 buffer, not a 3-D frame.

    cv2 is Mat-oriented, so this is the shape most likely to be rejected.
    """
    n = 320 * 240 * 3 // 2
    rng = np.random.default_rng(1)
    a = rng.integers(0, 1023, n, dtype=np.uint16)
    b = rng.integers(0, 1023, n, dtype=np.uint16)
    out = np.empty(n, dtype=np.uint16)

    blend_crossfade(a, b, 0.4, out=out)

    assert np.abs(out.astype(np.float64) - _exact(a, b, 0.4)).max() <= 0.5
