"""The animated deblur's Gaussian, run at a fraction of what it used to cost.

A content-backed title screen blurs its background with a radius of a tenth of
the frame height: 72 px at 720p, 108 px at 1080p. The separable Gaussian in
kernels.py takes 2r+1 taps per pixel per axis, which at 720p is 802 M
multiply-adds a frame, and the renderer ran it on every frame. Profiled on a
CPU backend that was 85% of a title screen and 70% of a whole CPU-only render
(issue #900).

Two things make it cheap, and neither changes the picture by as much as the
film grain that is laid over it afterwards:

Decimate. A Gaussian with sigma 24 has nothing left above about a fiftieth of a
cycle per pixel, so blurring a quarter-size copy and resampling back up throws
away no signal the blur would have kept. Box-mean down, bilinear up.

Reuse. The deblur curve sits flat at full blur for 70% of an opening title and
86% of an ending, and across that stretch the blurred background barely moves,
so it is blurred once and held. It only *barely* moves, though: the slow-motion
background does drift, and #900's suggestion of holding one buffer for a whole
flat tail freezes it -- measured at up to 128/255 by the end of a 7 s ending, a
plainly different picture. So the held blur is checked against the decimated
picture that filled it and thrown away as soon as the input has moved by more
than REUSE_TOLERANCE. Blurring is an averaging operator, so that bound carries
straight through to the blurred output.

What that check is worth: on a background that keeps moving it holds nothing
and costs nothing (28.8 against 29.2 ms a frame at 720p on two CPU threads); on
one that holds still it skips three quarters of the blurs and takes 8-10% off
the frame. The decimation is where the order of magnitude comes from.

Note: this module does NOT use 'from __future__ import annotations' because
kernel signatures need actual type objects, not string annotations.
"""

from . import kernels
from .gpu_kernel_backend import KERNELS_AVAILABLE, ti
from .kernels import _create_gaussian_kernel

#: How far the decimated picture may drift before the held blur is thrown away,
#: on the 0-1 scale the renderer works in. 0.01 is 2.6/255, well under the film
#: grain the renderer adds on top of this (+-0.025, measured at 4.16/255 of
#: frame-to-frame movement in #900).
REUSE_TOLERANCE = 0.01

#: Linear decimation factor for the blur. Quarter resolution is sixteen times
#: fewer pixels and a quarter of the taps per axis.
DOWNSCALE = 4

#: Below this many taps per axis the decimated Gaussian is a visibly different
#: picture, and the full-resolution one is already cheap.
_MIN_DECIMATED_RADIUS = 4

_blur_kernels_compiled = False
_box_downsample_3 = None
_bilinear_upsample_3 = None
_max_abs_diff_3 = None


def _compile_box_downsample():
    """Build the decimating kernel.

    One kernel per builder, the way kernels.py builds its Catmull-Rom blend:
    the loop nests inside a kernel body count towards the enclosing function,
    and three of them in one builder puts it over the complexity gate.
    """

    @ti.kernel
    def box_downsample_3(
        src: ti.types.ndarray(dtype=ti.f32, ndim=3),
        dst: ti.types.ndarray(dtype=ti.f32, ndim=3),
        factor: ti.i32,
        pad: ti.i32,
    ):
        """Mean of each factor x factor block, into a buffer with a `pad` halo.

        Source reads clamp, so the halo holds the block means of the frame's
        own edge extended outwards -- which is what the full-resolution blur
        convolves against at the frame border. Without it the decimated blur
        clamps against a 4 px block mean instead of the true edge line, and the
        two pictures part company by 40/255 in the outermost radius (#900).

        An exact copy when factor is 1 and pad is 0.
        """
        height = src.shape[0]
        width = src.shape[1]
        weight = 1.0 / ti.cast(factor * factor, ti.f32)
        for y, x in ti.ndrange(dst.shape[0], dst.shape[1]):
            top = (y - pad) * factor
            left = (x - pad) * factor
            for c in ti.static(range(3)):
                total = 0.0
                for dy in range(factor):
                    for dx in range(factor):
                        sy = ti.max(0, ti.min(height - 1, top + dy))
                        sx = ti.max(0, ti.min(width - 1, left + dx))
                        total += src[sy, sx, c]
                dst[y, x, c] = total * weight

    return box_downsample_3


def _compile_bilinear_upsample():
    """Build the resampling kernel that puts the blur back at frame size."""

    @ti.kernel
    def bilinear_upsample_3(
        src: ti.types.ndarray(dtype=ti.f32, ndim=3),
        dst: ti.types.ndarray(dtype=ti.f32, ndim=3),
        factor: ti.f32,
        pad: ti.i32,
    ):
        """Centre-aligned bilinear upscale out of a padded buffer.

        An exact copy when factor is 1 and pad is 0. Nearest-neighbour would
        put the decimation grid back into the picture as 4 px steps; on a
        background this soft they would be the only thing in it with an edge.
        """
        last_y = src.shape[0] - 1
        last_x = src.shape[1] - 1
        for y, x in ti.ndrange(dst.shape[0], dst.shape[1]):
            source_y = (ti.cast(y, ti.f32) + 0.5) / factor - 0.5 + ti.cast(pad, ti.f32)
            source_x = (ti.cast(x, ti.f32) + 0.5) / factor - 0.5 + ti.cast(pad, ti.f32)
            top = ti.cast(ti.floor(source_y), ti.i32)
            left = ti.cast(ti.floor(source_x), ti.i32)
            down_weight = source_y - ti.cast(top, ti.f32)
            right_weight = source_x - ti.cast(left, ti.f32)
            y0 = ti.max(0, ti.min(last_y, top))
            y1 = ti.max(0, ti.min(last_y, top + 1))
            x0 = ti.max(0, ti.min(last_x, left))
            x1 = ti.max(0, ti.min(last_x, left + 1))
            for c in ti.static(range(3)):
                upper = src[y0, x0, c] * (1.0 - right_weight) + src[y0, x1, c] * right_weight
                lower = src[y1, x0, c] * (1.0 - right_weight) + src[y1, x1, c] * right_weight
                dst[y, x, c] = upper * (1.0 - down_weight) + lower * down_weight

    return bilinear_upsample_3


def _compile_max_abs_diff():
    """Build the kernel that says whether the held blur still stands."""

    @ti.kernel
    def max_abs_diff_3(
        a: ti.types.ndarray(dtype=ti.f32, ndim=3),
        b: ti.types.ndarray(dtype=ti.f32, ndim=3),
        out: ti.types.ndarray(dtype=ti.f32, ndim=1),
    ):
        """Largest per-channel gap between two pictures, into out[0].

        One atomic per row, not per channel: a quarter-resolution 720p frame is
        690k samples, and every one of them hammering the same address turned
        this check into more work than the blur it was meant to save.

        Clears the target itself -- a top-level statement runs before the loop
        below it -- which saves a host-to-device write on every frame.
        """
        out[0] = 0.0
        for y in range(a.shape[0]):
            worst = 0.0
            for x in range(a.shape[1]):
                for c in ti.static(range(3)):
                    worst = ti.max(worst, ti.abs(a[y, x, c] - b[y, x, c]))
            ti.atomic_max(out[0], worst)

    return max_abs_diff_3


def compile_blur_kernels() -> None:
    """Compile the resampling kernels. Must be called AFTER ti.init()."""
    global _blur_kernels_compiled, _box_downsample_3, _bilinear_upsample_3, _max_abs_diff_3  # noqa: PLW0603

    if _blur_kernels_compiled or not KERNELS_AVAILABLE:
        return

    _box_downsample_3 = _compile_box_downsample()
    _bilinear_upsample_3 = _compile_bilinear_upsample()
    _max_abs_diff_3 = _compile_max_abs_diff()
    _blur_kernels_compiled = True


def _decimation_for(height: int, width: int, radius: int, downscale: int) -> int:
    """The factor this frame can actually be decimated by, 1 when it cannot."""
    if downscale <= 1:
        return 1
    if height % downscale or width % downscale:
        return 1
    if radius // downscale < _MIN_DECIMATED_RADIUS:
        return 1
    return downscale


class AnimatedBlur:
    """The blur behind the animated deblur: decimated, and held while it can be.

    Owns its own device buffers, which are a sixteenth of a frame each at the
    default decimation. `apply` blurs a frame buffer in place.
    """

    def __init__(
        self,
        height: int,
        width: int,
        radius: int,
        *,
        downscale: int = DOWNSCALE,
        reuse: bool = True,
    ) -> None:
        compile_blur_kernels()
        self.key = (height, width, radius)
        self.downscale = _decimation_for(height, width, radius, downscale)
        self._reuse = reuse

        self._radius = max(1, round(radius / self.downscale))
        self._kernel = _create_gaussian_kernel(self._radius, sigma=(radius / 3.0) / self.downscale)
        # The halo only earns its keep once the picture is decimated: at full
        # resolution the separable kernels already clamp against the right edge.
        self._pad = self._radius if self.downscale > 1 else 0
        shape = (
            height // self.downscale + 2 * self._pad,
            width // self.downscale + 2 * self._pad,
            3,
        )
        self._small = ti.ndarray(dtype=ti.f32, shape=shape)
        self._scratch = ti.ndarray(dtype=ti.f32, shape=shape)
        self._blurred = ti.ndarray(dtype=ti.f32, shape=shape)
        self._held_input = ti.ndarray(dtype=ti.f32, shape=shape) if reuse else None
        self._holding = False
        self._drift = ti.ndarray(dtype=ti.f32, shape=(1,))

    def apply(self, frame, *, reusable: bool) -> None:
        """Blur `frame` in place, reusing the held result when it still stands.

        `reusable` says the caller will show this blur on its own rather than
        blending it with the sharp frame -- the flat tail of the deblur curve.
        """
        _box_downsample_3(frame, self._small, self.downscale, self._pad)
        if not (reusable and self._reuse and self._held_blur_stands()):
            kernels._gaussian_blur_h(self._small, self._scratch, self._kernel, self._radius)
            kernels._gaussian_blur_v(self._scratch, self._blurred, self._kernel, self._radius)
            self._hold(reusable and self._reuse)
        _bilinear_upsample_3(self._blurred, frame, float(self.downscale), self._pad)

    def _held_blur_stands(self) -> bool:
        """Whether the picture has moved since the held blur was taken."""
        if not self._holding:
            return False
        _max_abs_diff_3(self._small, self._held_input, self._drift)
        return float(self._drift.to_numpy()[0]) <= REUSE_TOLERANCE

    def _hold(self, keep: bool) -> None:
        """Keep this frame's decimated picture as what the held blur stands for.

        By swapping the two buffers rather than copying one into the other:
        the next frame decimates into whichever is now spare.
        """
        self._holding = keep
        if keep:
            self._small, self._held_input = self._held_input, self._small
