"""The animated deblur's Gaussian, and the two things that make it cheap.

Unit tests: they run the real kernels on the CPU backend over small numpy
arrays, the way tests/test_gpu_buffers.py does, so no GPU and no FFmpeg.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _kernels():
    from immich_memories.titles.kernels import init_kernels

    if init_kernels() is None:
        pytest.skip("no kernel backend on this platform")


def _picture(height: int, width: int, seed: int = 7) -> np.ndarray:
    """A smooth picture with one bright blob, which a blur has to spread."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    base = 0.3 + 0.2 * np.sin(x / 11.0) * np.cos(y / 9.0)
    blob = np.exp(-(((x - width * 0.6) ** 2 + (y - height * 0.4) ** 2) / (2 * (width / 8) ** 2)))
    frame = np.clip(base + 0.5 * blob, 0.0, 1.0).astype(np.float32)
    return np.repeat(frame[:, :, None], 3, axis=2) * rng.uniform(0.9, 1.0, (1, 1, 3)).astype(
        np.float32
    )


def _blur_calls(monkeypatch) -> list[int]:
    """Count the separable Gaussian's launches without changing what it does."""
    from immich_memories.titles import kernels

    calls = [0]
    original = kernels._gaussian_blur_h

    def counted(*args):
        calls[0] += 1
        original(*args)

    # WHY: the kernel library is the boundary here. The optimisation's whole
    # contract is "launch this fewer times", and nothing else in the output
    # says whether it did.
    monkeypatch.setattr(kernels, "_gaussian_blur_h", counted)
    return calls


class TestFlatTailReuse:
    def test_a_still_picture_is_blurred_once(self, monkeypatch) -> None:
        from immich_memories.titles.kernel_blur import AnimatedBlur

        calls = _blur_calls(monkeypatch)
        blur = AnimatedBlur(48, 64, 6)
        frame = _picture(48, 64)

        for _ in range(10):
            work = frame.copy()
            blur.apply(work, reusable=True)

        assert calls[0] == 1

    def test_a_moved_picture_is_blurred_again(self, monkeypatch) -> None:
        from immich_memories.titles.kernel_blur import AnimatedBlur

        calls = _blur_calls(monkeypatch)
        blur = AnimatedBlur(48, 64, 6)

        blur.apply(_picture(48, 64), reusable=True)
        blur.apply(_picture(48, 64, seed=99), reusable=True)

        assert calls[0] == 2

    def test_the_deblur_ramp_never_reuses(self, monkeypatch) -> None:
        """Off the flat tail the background is blended with the sharp frame,
        so every frame needs its own blur however still the picture is."""
        from immich_memories.titles.kernel_blur import AnimatedBlur

        calls = _blur_calls(monkeypatch)
        blur = AnimatedBlur(48, 64, 6)
        frame = _picture(48, 64)

        for _ in range(4):
            blur.apply(frame.copy(), reusable=False)

        assert calls[0] == 4

    def test_reuse_stays_inside_the_tolerance_it_promises(self) -> None:
        """A reused blur may differ from a fresh one, but only by the amount
        the cache check let the input drift."""
        from immich_memories.titles.kernel_blur import REUSE_TOLERANCE, AnimatedBlur

        frame = _picture(48, 64)
        drifted = np.clip(frame + REUSE_TOLERANCE * 0.5, 0.0, 1.0)

        reused = drifted.copy()
        blur = AnimatedBlur(48, 64, 6)
        blur.apply(frame.copy(), reusable=True)
        blur.apply(reused, reusable=True)

        fresh = drifted.copy()
        AnimatedBlur(48, 64, 6).apply(fresh, reusable=False)

        assert np.abs(reused - fresh).max() <= REUSE_TOLERANCE


class TestDecimatedBlur:
    def test_the_decimated_blur_is_the_full_resolution_one(self) -> None:
        """A 4x decimated Gaussian is the same picture: at these radii the blur
        has nothing left above an eighth of a cycle per pixel.

        The frame is 640x360 with a radius of a tenth of its height, which is
        the shape a content-backed title actually blurs. What is left is the
        outermost radius of the frame, where the two blurs clamp against
        different edge pixels -- the full-resolution one replicates the true
        edge, the decimated one replicates a 4 px block mean of it.
        """
        from immich_memories.titles.kernel_blur import AnimatedBlur

        frame = _picture(360, 640)
        quarter = frame.copy()
        full = frame.copy()

        AnimatedBlur(360, 640, 36, downscale=4).apply(quarter, reusable=False)
        AnimatedBlur(360, 640, 36, downscale=1).apply(full, reusable=False)

        difference = np.abs(quarter - full)
        # The renderer's own film grain is +-0.025 on this scale.
        assert difference.max() < 0.025
        assert difference[36:-36, 36:-36].max() < 0.005

    def test_downscale_one_is_the_untouched_separable_blur(self) -> None:
        """The reference path the pixel tests measure against has to be the
        blur the renderer used to run, to the bit."""
        from immich_memories.titles import kernels
        from immich_memories.titles.kernel_blur import AnimatedBlur
        from immich_memories.titles.kernels import _create_gaussian_kernel

        frame = _picture(48, 64)
        through_the_class = frame.copy()
        AnimatedBlur(48, 64, 6, downscale=1).apply(through_the_class, reusable=False)

        by_hand = frame.copy()
        scratch = np.zeros_like(by_hand)
        kernel = _create_gaussian_kernel(6)
        kernels._gaussian_blur_h(by_hand, scratch, kernel, 6)
        kernels._gaussian_blur_v(scratch, by_hand, kernel, 6)

        np.testing.assert_array_equal(through_the_class, by_hand)

    def test_an_indivisible_frame_falls_back_to_full_resolution(self) -> None:
        """Decimation needs whole blocks; 101 rows do not make quarters."""
        from immich_memories.titles.kernel_blur import AnimatedBlur

        assert AnimatedBlur(101, 64, 32, downscale=4).downscale == 1

    def test_a_small_radius_is_not_worth_decimating(self) -> None:
        """Below a handful of taps the decimated Gaussian is a different
        picture, and the full-resolution one is already cheap."""
        from immich_memories.titles.kernel_blur import AnimatedBlur

        assert AnimatedBlur(180, 320, 4, downscale=4).downscale == 1
