"""Taichi GPU kernel definitions, initialization, and helper functions.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    from .sdf_font import find_font, get_cached_atlas, init_sdf_kernels, layout_text

    SDF_AVAILABLE = True
except ImportError:
    SDF_AVAILABLE = False
    find_font = None
    get_cached_atlas = None
    init_sdf_kernels = None
    layout_text = None

try:
    import taichi as ti

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    ti = None  # type: ignore

_taichi_initialized = False
_taichi_backend = None
_kernels_compiled = False


def init_taichi() -> str | None:
    """Initialize Taichi with the best available GPU backend."""
    global _taichi_initialized, _taichi_backend
    if not TAICHI_AVAILABLE:
        logger.warning("Taichi not installed. Install with: pip install taichi")
        return None
    if _taichi_initialized:
        return _taichi_backend

    import platform

    if platform.system() == "Darwin":
        backends = [(ti.metal, "Metal"), (ti.cpu, "CPU")]
    else:
        backends = [(ti.cuda, "CUDA"), (ti.vulkan, "Vulkan"), (ti.cpu, "CPU")]

    last_error = None
    for backend, name in backends:
        try:
            ti.init(arch=backend, offline_cache=True)
            logger.info(f"Taichi initialized with {name} backend")
            _compile_kernels()
            if SDF_AVAILABLE and init_sdf_kernels:
                init_sdf_kernels()
            _taichi_initialized = True
            _taichi_backend = name
            return name
        except Exception as e:
            last_error = e
            logger.debug(f"Failed to init Taichi with {name}: {e}")
            continue

    logger.error(f"Failed to initialize Taichi with any backend. Last error: {last_error}")
    return None


def is_taichi_available() -> bool:
    """Check if Taichi is available and can be initialized."""
    if not TAICHI_AVAILABLE:
        return False
    return init_taichi() is not None


# Compiled kernel references (populated by _compile_kernels)
_generate_linear_gradient = None
_generate_radial_gradient = None
_gaussian_blur_h = None
_gaussian_blur_v = None
_apply_vignette = None
_render_bokeh_particles = None
_apply_noise_grain = None
_generate_aurora_gradient = None
_composite_rgba_over = None
_composite_text_with_offset = None
_apply_color_pulse = None
_render_sdf_text = None


def _compile_kernels():
    """Compile all Taichi kernels. Must be called AFTER ti.init()."""
    global _kernels_compiled
    global _generate_linear_gradient, _generate_radial_gradient
    global _gaussian_blur_h, _gaussian_blur_v
    global _apply_vignette, _render_bokeh_particles
    global _apply_noise_grain, _generate_aurora_gradient
    global _composite_rgba_over, _composite_text_with_offset
    global _apply_color_pulse, _render_sdf_text

    if _kernels_compiled or not TAICHI_AVAILABLE:
        return

    @ti.kernel
    def generate_linear_gradient(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        c0_r: ti.f32,
        c0_g: ti.f32,
        c0_b: ti.f32,
        c1_r: ti.f32,
        c1_g: ti.f32,
        c1_b: ti.f32,
        angle: ti.f32,
        width: ti.i32,
        height: ti.i32,
    ):
        """Generate linear gradient."""
        cos_a = ti.cos(angle)
        sin_a = ti.sin(angle)

        for y, x in ti.ndrange(height, width):
            nx = (x - width * 0.5) / width
            ny = (y - height * 0.5) / height
            t = nx * cos_a + ny * sin_a + 0.5
            t = ti.max(0.0, ti.min(1.0, t))
            output[y, x, 0] = c0_r * (1.0 - t) + c1_r * t
            output[y, x, 1] = c0_g * (1.0 - t) + c1_g * t
            output[y, x, 2] = c0_b * (1.0 - t) + c1_b * t

    @ti.kernel
    def generate_radial_gradient(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        c0_r: ti.f32,
        c0_g: ti.f32,
        c0_b: ti.f32,
        c1_r: ti.f32,
        c1_g: ti.f32,
        c1_b: ti.f32,
        radius_ratio: ti.f32,
        width: ti.i32,
        height: ti.i32,
    ):
        """Generate radial gradient."""
        cx = width * 0.5
        cy = height * 0.5
        max_dist = ti.sqrt(cx * cx + cy * cy) * radius_ratio

        for y, x in ti.ndrange(height, width):
            dx = x - cx
            dy = y - cy
            dist = ti.sqrt(dx * dx + dy * dy)
            t = dist / max_dist
            t = ti.min(1.0, t)
            t = 1.0 - (1.0 - t) * (1.0 - t)
            output[y, x, 0] = c0_r * (1.0 - t) + c1_r * t
            output[y, x, 1] = c0_g * (1.0 - t) + c1_g * t
            output[y, x, 2] = c0_b * (1.0 - t) + c1_b * t

    @ti.kernel
    def gaussian_blur_h(
        input_arr: ti.types.ndarray(dtype=ti.f32, ndim=3),
        output_arr: ti.types.ndarray(dtype=ti.f32, ndim=3),
        kernel: ti.types.ndarray(dtype=ti.f32, ndim=1),
        radius: ti.i32,
    ):
        """Horizontal pass of separable Gaussian blur."""
        height = input_arr.shape[0]
        width = input_arr.shape[1]

        for y, x in ti.ndrange(height, width):
            for c in ti.static(range(3)):
                acc = 0.0
                for k in range(-radius, radius + 1):
                    sx = ti.max(0, ti.min(width - 1, x + k))
                    acc += input_arr[y, sx, c] * kernel[k + radius]
                output_arr[y, x, c] = acc

    @ti.kernel
    def gaussian_blur_v(
        input_arr: ti.types.ndarray(dtype=ti.f32, ndim=3),
        output_arr: ti.types.ndarray(dtype=ti.f32, ndim=3),
        kernel: ti.types.ndarray(dtype=ti.f32, ndim=1),
        radius: ti.i32,
    ):
        """Vertical pass of separable Gaussian blur."""
        height = input_arr.shape[0]
        width = input_arr.shape[1]

        for y, x in ti.ndrange(height, width):
            for c in ti.static(range(3)):
                acc = 0.0
                for k in range(-radius, radius + 1):
                    sy = ti.max(0, ti.min(height - 1, y + k))
                    acc += input_arr[sy, x, c] * kernel[k + radius]
                output_arr[y, x, c] = acc

    @ti.kernel
    def apply_vignette(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        strength: ti.f32,
        width: ti.i32,
        height: ti.i32,
    ):
        """Apply elliptical vignette (fades to white at edges)."""
        cx = width * 0.5
        cy = height * 0.5
        for y, x in ti.ndrange(height, width):
            dx = (x - cx) / cx
            dy = (y - cy) / cy
            dist = ti.sqrt(dx * dx + dy * dy)
            vignette_factor = ti.min(1.0, strength * dist * dist)
            for c in ti.static(range(3)):
                output[y, x, c] = output[y, x, c] + (1.0 - output[y, x, c]) * vignette_factor

    @ti.kernel
    def render_bokeh_particles(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),  # (h, w, 4) RGBA
        particles: ti.types.ndarray(
            dtype=ti.f32, ndim=2
        ),  # (n, 7): x, y, size, opacity, angle, r, g, b
        num_particles: ti.i32,
        width: ti.i32,
        height: ti.i32,
    ):
        """Render soft colored bokeh circles."""
        for y, x in ti.ndrange(height, width):
            r_acc = 0.0
            g_acc = 0.0
            b_acc = 0.0
            alpha_acc = 0.0
            for p in range(num_particles):
                px = particles[p, 0]
                py = particles[p, 1]
                size = particles[p, 2]
                opacity = particles[p, 3]
                pr = particles[p, 5]
                pg = particles[p, 6]
                pb = particles[p, 7]
                dx = float(x) - px
                dy = float(y) - py
                dist = ti.sqrt(dx * dx + dy * dy)
                if dist < size:
                    falloff = 1.0 - (dist / size)
                    contrib = opacity * falloff * falloff
                    r_acc += pr * contrib
                    g_acc += pg * contrib
                    b_acc += pb * contrib
                    alpha_acc += contrib
            # Normalize colors and set alpha
            if alpha_acc > 0.001:
                output[y, x, 0] = ti.min(1.0, r_acc / alpha_acc)
                output[y, x, 1] = ti.min(1.0, g_acc / alpha_acc)
                output[y, x, 2] = ti.min(1.0, b_acc / alpha_acc)
            else:
                output[y, x, 0] = 0.0
                output[y, x, 1] = 0.0
                output[y, x, 2] = 0.0
            output[y, x, 3] = ti.min(1.0, alpha_acc)

    @ti.kernel
    def apply_noise_grain(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        intensity: ti.f32,
        seed: ti.i32,
        width: ti.i32,
        height: ti.i32,
    ):
        """Apply film grain/noise texture."""
        for y, x in ti.ndrange(height, width):
            hash_val = (x * 374761393 + y * 668265263 + seed) ^ (seed * 1013904223)
            hash_val = (hash_val ^ (hash_val >> 13)) * 1274126177
            hash_val = hash_val ^ (hash_val >> 16)
            noise = (float(hash_val & 0xFFFF) / 32768.0) - 1.0
            for c in ti.static(range(3)):
                val = output[y, x, c] + noise * intensity
                output[y, x, c] = ti.max(0.0, ti.min(1.0, val))

    @ti.kernel
    def generate_aurora_gradient(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        colors: ti.types.ndarray(dtype=ti.f32, ndim=2),  # (num_blobs, 6): cx, cy, radius, r, g, b
        num_blobs: ti.i32,
        width: ti.i32,
        height: ti.i32,
        time: ti.f32,
    ):
        """Generate aurora/mesh gradient with multiple soft color blobs."""
        for y, x in ti.ndrange(height, width):
            r_acc = 0.0
            g_acc = 0.0
            b_acc = 0.0
            weight_sum = 0.0
            for b in range(num_blobs):
                cx = colors[b, 0] + ti.sin(time * 0.5 + b * 1.5) * width * 0.05
                cy = colors[b, 1] + ti.cos(time * 0.4 + b * 1.2) * height * 0.05
                radius = colors[b, 2]
                dx = (float(x) - cx) / radius
                dy = (float(y) - cy) / radius
                dist_sq = dx * dx + dy * dy
                weight = ti.exp(-dist_sq * 0.5)
                weight_sum += weight
                r_acc += colors[b, 3] * weight
                g_acc += colors[b, 4] * weight
                b_acc += colors[b, 5] * weight
            if weight_sum > 0.001:
                output[y, x, 0] = r_acc / weight_sum
                output[y, x, 1] = g_acc / weight_sum
                output[y, x, 2] = b_acc / weight_sum
            else:
                output[y, x, 0] = colors[0, 3]
                output[y, x, 1] = colors[0, 4]
                output[y, x, 2] = colors[0, 5]

    @ti.kernel
    def composite_rgba_over(
        bg: ti.types.ndarray(dtype=ti.f32, ndim=3),
        fg: ti.types.ndarray(dtype=ti.f32, ndim=3),  # (h, w, 4) RGBA
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        opacity: ti.f32,
    ):
        """Composite RGBA foreground over RGB background."""
        height = bg.shape[0]
        width = bg.shape[1]
        for y, x in ti.ndrange(height, width):
            alpha = fg[y, x, 3] * opacity
            for c in ti.static(range(3)):
                output[y, x, c] = bg[y, x, c] * (1.0 - alpha) + fg[y, x, c] * alpha

    @ti.kernel
    def composite_text_with_offset(
        bg: ti.types.ndarray(dtype=ti.f32, ndim=3),
        text_layer: ti.types.ndarray(dtype=ti.f32, ndim=3),  # (h, w, 4) RGBA
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        opacity: ti.f32,
        y_offset: ti.f32,
        x_offset: ti.f32,
    ):
        """Composite text layer with position offset animation."""
        height = bg.shape[0]
        width = bg.shape[1]
        text_height = text_layer.shape[0]
        text_width = text_layer.shape[1]
        for y, x in ti.ndrange(height, width):
            src_y = int(y - y_offset)
            src_x = int(x - x_offset)
            if 0 <= src_y < text_height and 0 <= src_x < text_width:
                alpha = text_layer[src_y, src_x, 3] * opacity
                for c in ti.static(range(3)):
                    fg = text_layer[src_y, src_x, c]
                    output[y, x, c] = bg[y, x, c] * (1.0 - alpha) + fg * alpha
            else:
                for c in ti.static(range(3)):
                    output[y, x, c] = bg[y, x, c]

    @ti.kernel
    def apply_color_pulse(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),
        brightness_delta: ti.f32,
        saturation_mult: ti.f32,
    ):
        """Apply color pulsing effect."""
        height = output.shape[0]
        width = output.shape[1]
        for y, x in ti.ndrange(height, width):
            r = output[y, x, 0]
            g = output[y, x, 1]
            b = output[y, x, 2]
            r = ti.max(0.0, ti.min(1.0, r + brightness_delta))
            g = ti.max(0.0, ti.min(1.0, g + brightness_delta))
            b = ti.max(0.0, ti.min(1.0, b + brightness_delta))
            gray = 0.299 * r + 0.587 * g + 0.114 * b
            r = gray + (r - gray) * saturation_mult
            g = gray + (g - gray) * saturation_mult
            b = gray + (b - gray) * saturation_mult
            output[y, x, 0] = ti.max(0.0, ti.min(1.0, r))
            output[y, x, 1] = ti.max(0.0, ti.min(1.0, g))
            output[y, x, 2] = ti.max(0.0, ti.min(1.0, b))

    @ti.kernel
    def render_sdf_text(
        output: ti.types.ndarray(dtype=ti.f32, ndim=3),  # RGB frame buffer
        atlas: ti.types.ndarray(dtype=ti.f32, ndim=2),  # SDF atlas texture
        glyph_data: ti.types.ndarray(dtype=ti.f32, ndim=2),  # [n, 8] glyph params
        num_glyphs: ti.i32,
        color_r: ti.f32,
        color_g: ti.f32,
        color_b: ti.f32,
        opacity: ti.f32,
        scale: ti.f32,
        offset_x: ti.f32,
        offset_y: ti.f32,
        smoothing: ti.f32,
    ):
        """Render SDF text directly onto frame buffer."""
        height = output.shape[0]
        width = output.shape[1]
        atlas_h = atlas.shape[0]
        atlas_w = atlas.shape[1]

        for y, x in ti.ndrange(height, width):
            total_alpha = 0.0
            for g in range(num_glyphs):
                screen_x = glyph_data[g, 0] + offset_x
                screen_y = glyph_data[g, 1] + offset_y
                a_x = glyph_data[g, 2]
                a_y = glyph_data[g, 3]
                a_w = glyph_data[g, 4]
                a_h = glyph_data[g, 5]
                bearing_x = glyph_data[g, 6]
                bearing_y = glyph_data[g, 7]

                glyph_x = (float(x) - screen_x - bearing_x * scale) / scale
                glyph_y = (float(y) - screen_y + bearing_y * scale) / scale
                if 0.0 <= glyph_x < a_w and 0.0 <= glyph_y < a_h:
                    ax = int(a_x + glyph_x)
                    ay = int(a_y + glyph_y)
                    if 0 <= ax < atlas_w and 0 <= ay < atlas_h:
                        sdf = atlas[ay, ax]
                        edge = 0.5
                        alpha = (sdf - edge + smoothing) / (2.0 * smoothing)
                        alpha = ti.max(0.0, ti.min(1.0, alpha))
                        total_alpha = ti.max(total_alpha, alpha)

            final_alpha = total_alpha * opacity
            if final_alpha > 0.001:
                output[y, x, 0] = output[y, x, 0] * (1.0 - final_alpha) + color_r * final_alpha
                output[y, x, 1] = output[y, x, 1] * (1.0 - final_alpha) + color_g * final_alpha
                output[y, x, 2] = output[y, x, 2] * (1.0 - final_alpha) + color_b * final_alpha

    _generate_linear_gradient = generate_linear_gradient
    _generate_radial_gradient = generate_radial_gradient
    _gaussian_blur_h = gaussian_blur_h
    _gaussian_blur_v = gaussian_blur_v
    _apply_vignette = apply_vignette
    _render_bokeh_particles = render_bokeh_particles
    _apply_noise_grain = apply_noise_grain
    _generate_aurora_gradient = generate_aurora_gradient
    _composite_rgba_over = composite_rgba_over
    _composite_text_with_offset = composite_text_with_offset
    _apply_color_pulse = apply_color_pulse
    _render_sdf_text = render_sdf_text

    _kernels_compiled = True
    logger.debug("Taichi kernels compiled")


def _create_gaussian_kernel(radius: int, sigma: float | None = None) -> np.ndarray:
    """Create 1D Gaussian kernel for separable blur."""
    if radius == 0:
        return np.array([1.0], dtype=np.float32)
    if sigma is None:
        sigma = radius / 3.0
    x = np.arange(2 * radius + 1) - radius
    kernel = np.exp(-(x**2) / (2 * sigma**2))
    kernel /= kernel.sum()
    return kernel.astype(np.float32)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert hex color to normalized RGB floats."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


_OFL_FONTS = {"Montserrat", "Outfit", "Raleway", "Quicksand"}
_SYSTEM_FONTS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def _get_system_font(font_family: str = "Helvetica") -> str:
    """Get a reliable font path, preferring app-cached OFL fonts."""
    cache_dir = Path.home() / ".immich-memories" / "fonts"
    family_clean = font_family.replace(" ", "")
    family_dir = cache_dir / family_clean
    if not family_dir.exists() and family_clean in _OFL_FONTS:
        try:
            from immich_memories.titles.fonts import download_font

            download_font(family_clean)
        except Exception:
            pass
    if family_dir.exists():
        for w in ["Bold", "SemiBold", "Medium", "Regular"]:
            candidate = family_dir / f"{family_clean}-{w}.ttf"
            if candidate.exists():
                return str(candidate)
    for path in _SYSTEM_FONTS:
        if Path(path).exists():
            return path
    return "Arial"
