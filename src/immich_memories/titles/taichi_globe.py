"""Taichi GPU kernel for equirectangular → sphere projection.

Projects a flat equirectangular map texture onto a 3D sphere,
rendering Earth curvature for animated trip map transitions.

Note: No 'from __future__ import annotations' — Taichi needs real types.
"""

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

_kernel_compiled = False
_project_globe = None


def _compile_globe_kernel():
    """Compile the globe projection kernel on first use."""
    global _kernel_compiled, _project_globe  # noqa: PLW0603
    if _kernel_compiled:
        return

    try:
        import taichi as ti

        @ti.kernel
        def _globe_kernel(
            output: ti.types.ndarray(dtype=ti.f32, ndim=3),
            texture: ti.types.ndarray(dtype=ti.f32, ndim=3),
            cam_lat: ti.f32,
            cam_lon: ti.f32,
            cam_dist: ti.f32,
            tex_h: ti.i32,
            tex_w: ti.i32,
            out_w: ti.i32,
            out_h: ti.i32,
            fov: ti.f32,
        ):
            """Project equirectangular texture onto sphere for each pixel."""
            pi = math.pi

            for py, px in ti.ndrange(out_h, out_w):
                # Normalized screen coords
                aspect = ti.cast(out_h, ti.f32) / ti.cast(out_w, ti.f32)
                u = (ti.cast(px, ti.f32) - out_w * 0.5) / (out_w * 0.5) * fov
                v = (ti.cast(py, ti.f32) - out_h * 0.5) / (out_h * 0.5) * fov * aspect

                # Ray direction in camera space
                ray_x = u
                ray_y = -v
                ray_z = -1.0
                ray_len = ti.sqrt(ray_x * ray_x + ray_y * ray_y + ray_z * ray_z)
                ray_x /= ray_len
                ray_y /= ray_len
                ray_z /= ray_len

                # Camera position on sphere surface (looking at origin)
                cos_lat = ti.cos(cam_lat)
                sin_lat = ti.sin(cam_lat)
                cos_lon = ti.cos(cam_lon)
                sin_lon = ti.sin(cam_lon)

                cam_x = cam_dist * cos_lat * sin_lon
                cam_y = cam_dist * sin_lat
                cam_z = cam_dist * cos_lat * cos_lon

                # View matrix: forward, right, up
                fwd_x = -cam_x / cam_dist
                fwd_y = -cam_y / cam_dist
                fwd_z = -cam_z / cam_dist

                right_x = fwd_z
                right_y = 0.0
                right_z = -fwd_x
                right_len = ti.sqrt(right_x * right_x + right_z * right_z)
                if right_len > 1e-6:
                    right_x /= right_len
                    right_z /= right_len

                up_x = fwd_y * right_z - fwd_z * right_y
                up_y = fwd_z * right_x - fwd_x * right_z
                up_z = fwd_x * right_y - fwd_y * right_x

                # Transform ray to world space (columns: right, up, -forward)
                wr_x = ray_x * right_x + ray_y * up_x - ray_z * fwd_x
                wr_y = ray_x * right_y + ray_y * up_y - ray_z * fwd_y
                wr_z = ray_x * right_z + ray_y * up_z - ray_z * fwd_z

                # Ray-sphere intersection (unit sphere at origin)
                a = wr_x * wr_x + wr_y * wr_y + wr_z * wr_z
                b = 2.0 * (cam_x * wr_x + cam_y * wr_y + cam_z * wr_z)
                c = cam_x * cam_x + cam_y * cam_y + cam_z * cam_z - 1.0
                disc = b * b - 4.0 * a * c

                if disc >= 0.0:
                    t_hit = (-b - ti.sqrt(disc)) / (2.0 * a)
                    if t_hit > 0.0:
                        hx = cam_x + t_hit * wr_x
                        hy = cam_y + t_hit * wr_y
                        hz = cam_z + t_hit * wr_z

                        # Sphere coords → lat/lon
                        hit_lat = ti.asin(ti.max(-1.0, ti.min(1.0, hy)))
                        hit_lon = ti.atan2(hx, hz)

                        # Equirectangular UV mapping
                        tex_u = (hit_lon + pi) / (2.0 * pi)
                        tex_v = (pi * 0.5 - hit_lat) / pi

                        # Bilinear texture sampling
                        sx = tex_u * (tex_w - 1)
                        sy = tex_v * (tex_h - 1)
                        ix = ti.cast(sx, ti.i32)
                        iy = ti.cast(sy, ti.i32)
                        ix = ti.max(0, ti.min(ix, tex_w - 2))
                        iy = ti.max(0, ti.min(iy, tex_h - 2))
                        fx = sx - ti.cast(ix, ti.f32)
                        fy = sy - ti.cast(iy, ti.f32)

                        for ch in ti.static(range(3)):
                            v00 = texture[iy, ix, ch]
                            v10 = texture[iy, ix + 1, ch]
                            v01 = texture[iy + 1, ix, ch]
                            v11 = texture[iy + 1, ix + 1, ch]
                            val = (
                                v00 * (1.0 - fx) * (1.0 - fy)
                                + v10 * fx * (1.0 - fy)
                                + v01 * (1.0 - fx) * fy
                                + v11 * fx * fy
                            )
                            output[py, px, ch] = val

                        # Limb darkening (atmosphere edge glow)
                        dot_val = hx * (cam_x - hx) + hy * (cam_y - hy) + hz * (cam_z - hz)
                        norm_len = ti.sqrt(
                            (cam_x - hx) ** 2 + (cam_y - hy) ** 2 + (cam_z - hz) ** 2
                        )
                        if norm_len > 1e-6:
                            cos_angle = dot_val / norm_len
                            limb = ti.max(0.0, 1.0 - cos_angle)
                            darken = 1.0 - 0.3 * limb * limb
                            for ch in ti.static(range(3)):
                                output[py, px, ch] *= darken
                    else:
                        for ch in ti.static(range(3)):
                            output[py, px, ch] = 0.02
                else:
                    # Miss — dark space background
                    output[py, px, 0] = 0.01
                    output[py, px, 1] = 0.01
                    output[py, px, 2] = 0.03

        _project_globe = _globe_kernel
        _kernel_compiled = True
        logger.info("Globe projection kernel compiled")

    except ImportError:
        logger.warning("Taichi not available, globe projection disabled")
        _kernel_compiled = True


def project_globe_frame(
    output: np.ndarray,
    texture: np.ndarray,
    cam_lat: float,
    cam_lon: float,
    cam_distance: float,
    width: int,
    height: int,
    fov: float = 0.8,
) -> None:
    """Project equirectangular texture onto sphere into output buffer.

    Args:
        output: Output RGB buffer (height, width, 3) float32.
        texture: Equirectangular map (tex_h, tex_w, 3) float32.
        cam_lat: Camera latitude in radians.
        cam_lon: Camera longitude in radians.
        cam_distance: Camera distance from sphere center (>1.0).
        width: Output width.
        height: Output height.
        fov: Field of view multiplier (smaller = more zoomed in).
    """
    _compile_globe_kernel()
    if _project_globe is None:
        return

    tex_h, tex_w = texture.shape[:2]
    _project_globe(
        output,
        texture,
        cam_lat,
        cam_lon,
        cam_distance,
        tex_h,
        tex_w,
        width,
        height,
        fov,
    )
