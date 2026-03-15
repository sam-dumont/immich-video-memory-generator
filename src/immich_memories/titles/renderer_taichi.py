"""GPU-accelerated title screen renderer using Taichi.

Cross-platform GPU acceleration (Metal/CUDA/Vulkan/CPU fallback) for title
rendering with gradients, blur, vignette, bokeh particles, and SDF text.
~15-60x faster than PIL renderer. See taichi_kernels.py for GPU kernels,
taichi_particles.py for particle systems, taichi_text.py for text rendering.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .sdf_font import SDFFontAtlas

# Import module for runtime access to compiled kernels.
# Kernels are initially None and compiled lazily by init_taichi().
# Direct `from .taichi_kernels import _func` would capture None at import time,
# so we access them as `taichi_kernels._func` at call time instead.
from . import taichi_kernels
from .taichi_kernels import (
    SDF_AVAILABLE,
    _create_gaussian_kernel,
    _hex_to_rgb,
    is_taichi_available,
)
from .taichi_kernels import (
    TAICHI_AVAILABLE as TAICHI_AVAILABLE,
)
from .taichi_kernels import (
    _render_sdf_text as _render_sdf_text,
)
from .taichi_kernels import (
    init_taichi as init_taichi,
)
from .taichi_particles import TaichiParticlesMixin
from .taichi_text import TaichiTextMixin

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class TaichiTitleConfig:
    """Configuration for Taichi GPU title renderer."""

    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration: float = 3.5

    bg_color1: str = "#FFF5E6"
    bg_color2: str = "#FFE4CC"
    gradient_angle: float = 135.0
    gradient_type: str = "linear"

    gradient_rotation: float = 10.0
    color_pulse_amount: float = 0.03
    vignette_strength: float = 0.3
    vignette_pulse: float = 0.05

    # Noise/grain texture for organic look
    enable_noise: bool = True
    noise_intensity: float = 0.025  # Subtle grain

    # Aurora/mesh gradient (alternative to linear/radial)
    # When gradient_type="aurora", uses multiple soft color blobs
    aurora_colors: list = None  # List of hex colors for blobs, uses gradient colors if None

    enable_bokeh: bool = True
    bokeh_count: int = 15  # Moderate number of circles
    bokeh_size_range: tuple[float, float] = (0.12, 0.28)  # Large soft circles
    bokeh_opacity_range: tuple[float, float] = (0.15, 0.30)  # Visible but soft
    bokeh_drift_speed: float = 0.3
    bokeh_color: tuple[float, float, float] = (1.0, 0.98, 0.92)  # Warm white glow

    # Birthday celebration mode with fireworks
    is_birthday: bool = False
    birthday_particle_count: int = 40
    birthday_colors: list = None  # Will use defaults if None

    # Fireworks settings (used when is_birthday=True)
    fireworks_burst_count: int = 12  # Number of firework bursts
    fireworks_particles_per_burst: int = 100  # Particles per burst
    fireworks_gravity: float = 0.25  # Gravity strength (pixels per frame^2)
    fireworks_friction: float = 0.985  # Velocity decay per frame
    fireworks_fade_speed: float = 0.3  # How fast particles fade (per second)

    blur_radius: int = 20

    text_color: str = "#2D2D2D"
    title_size_ratio: float = 0.10
    subtitle_size_ratio: float = 0.06
    font_family: str = "Helvetica"  # Font family for SDF rendering
    use_sdf_text: bool = True  # Use GPU SDF text (vs PIL fallback)
    enable_shadow: bool = True
    shadow_offset_ratio: float = 0.03
    shadow_opacity: float = 0.2

    fade_in_duration: float = 0.6
    fade_out_duration: float = 1.0
    slide_distance: int = 50
    scale_from: float = 0.85
    stagger_delay: float = 0.12

    # Custom background image (numpy float32 array, overrides gradient)
    # Used for map frames — the map is rendered once, then used as static background
    background_image: np.ndarray | None = None

    _bokeh_particles: np.ndarray = field(default_factory=lambda: np.array([]))
    _bokeh_seed: int = 42


# =============================================================================
# Main Renderer Class
# =============================================================================


class TaichiTitleRenderer(TaichiParticlesMixin, TaichiTextMixin):
    """GPU-accelerated title renderer using Taichi.

    Pre-allocates GPU buffers and compiles kernels on first use.
    Subsequent renders reuse the compiled kernels for maximum performance.
    """

    def __init__(self, config: TaichiTitleConfig | None = None):
        """Initialize renderer with configuration."""
        if not is_taichi_available():
            raise RuntimeError("Taichi not available. Install with: pip install taichi")

        self.config = config or TaichiTitleConfig()
        self.total_frames = int(self.config.fps * self.config.duration)

        h, w = self.config.height, self.config.width
        self.frame_buffer = np.zeros((h, w, 3), dtype=np.float32)
        self.temp_buffer = np.zeros((h, w, 3), dtype=np.float32)
        self.bokeh_buffer = np.zeros((h, w, 4), dtype=np.float32)

        self.blur_kernel = _create_gaussian_kernel(self.config.blur_radius)

        self.color1 = _hex_to_rgb(self.config.bg_color1)
        self.color2 = _hex_to_rgb(self.config.bg_color2)
        self.text_rgb = _hex_to_rgb(self.config.text_color)

        if self.config.enable_bokeh:
            self._init_bokeh_particles()

        self._title_layer: np.ndarray | None = None
        self._subtitle_layer: np.ndarray | None = None
        self._shadow_layer: np.ndarray | None = None
        self._cached_text: tuple[str, str | None] | None = None

        # SDF font atlas (loaded on first text render)
        self._sdf_atlas: SDFFontAtlas | None = None
        self._sdf_atlas_float: np.ndarray | None = None
        self._use_sdf = self.config.use_sdf_text and SDF_AVAILABLE

        if self._use_sdf:
            self._init_sdf_atlas()

        logger.info(
            f"TaichiTitleRenderer initialized: {w}x{h} @ {self.config.fps}fps (SDF: {self._use_sdf})"
        )

    def _compute_animation(self, t: float, progress: float, is_subtitle: bool = False) -> dict:
        """Compute animation values for current time."""
        cfg = self.config

        if is_subtitle:
            t = max(0, t - cfg.stagger_delay)

        fade_in_progress = min(1.0, t / cfg.fade_in_duration) if cfg.fade_in_duration > 0 else 1.0

        fade_out_start = cfg.duration - cfg.fade_out_duration
        if t > fade_out_start:
            fade_out_progress = min(1.0, (t - fade_out_start) / cfg.fade_out_duration)
        else:
            fade_out_progress = 0.0

        def ease_out_cubic(x):
            return 1.0 - (1.0 - x) ** 3

        fade_in_eased = ease_out_cubic(fade_in_progress)
        fade_out_eased = ease_out_cubic(fade_out_progress)

        opacity = fade_in_eased * (1.0 - fade_out_eased)
        y_offset = cfg.slide_distance * (1.0 - fade_in_eased) - cfg.slide_distance * fade_out_eased
        scale = cfg.scale_from + (1.0 - cfg.scale_from) * fade_in_eased

        return {"opacity": opacity, "y_offset": y_offset, "scale": scale, "x_offset": 0.0}

    def render_frame(
        self, frame_number: int, title: str, subtitle: str | None = None
    ) -> np.ndarray:
        """Render a single frame on GPU."""
        t = frame_number / self.config.fps
        progress = frame_number / self.total_frames
        cfg = self.config

        # 1. Generate background (custom image or gradient)
        if cfg.background_image is not None:
            np.copyto(self.frame_buffer, cfg.background_image)
        else:
            self._render_gradient(t, progress, cfg)

        # 2. Apply blur
        if cfg.blur_radius > 0:
            taichi_kernels._gaussian_blur_h(
                self.frame_buffer, self.temp_buffer, self.blur_kernel, cfg.blur_radius
            )
            taichi_kernels._gaussian_blur_v(
                self.temp_buffer, self.frame_buffer, self.blur_kernel, cfg.blur_radius
            )

        # 3. Color pulsing
        brightness_delta = cfg.color_pulse_amount * math.sin(progress * 2 * math.pi)
        saturation_mult = 1.0 + 0.05 * math.sin(progress * 2 * math.pi + math.pi / 2)
        taichi_kernels._apply_color_pulse(self.frame_buffer, brightness_delta, saturation_mult)

        # 4. Vignette
        vignette_strength = cfg.vignette_strength + cfg.vignette_pulse * math.sin(
            progress * 2 * math.pi
        )
        taichi_kernels._apply_vignette(self.frame_buffer, vignette_strength, cfg.width, cfg.height)

        # 5. Bokeh/Fireworks particles
        self._render_particles(progress, cfg)

        # 6. Apply noise/grain texture
        if cfg.enable_noise and cfg.noise_intensity > 0:
            noise_seed = frame_number * 12345 % 1000000
            taichi_kernels._apply_noise_grain(
                self.frame_buffer, cfg.noise_intensity, noise_seed, cfg.width, cfg.height
            )

        # 7. Render text
        self._render_text(t, progress, cfg, title, subtitle)

        return (np.clip(self.frame_buffer, 0, 1) * 255).astype(np.uint8)

    def _render_gradient(self, t: float, progress: float, cfg: TaichiTitleConfig):
        """Render the background gradient."""
        angle_rad = math.radians(cfg.gradient_angle)
        angle_offset = math.radians(cfg.gradient_rotation) * math.sin(progress * 2 * math.pi)
        current_angle = angle_rad + angle_offset

        if cfg.gradient_type == "aurora":
            if not hasattr(self, "_aurora_blobs"):
                self._init_aurora_blobs()
            taichi_kernels._generate_aurora_gradient(
                self.frame_buffer,
                self._aurora_blobs,
                len(self._aurora_blobs),
                cfg.width,
                cfg.height,
                t,
            )
        elif cfg.gradient_type == "radial":
            taichi_kernels._generate_radial_gradient(
                self.frame_buffer,
                self.color1[0],
                self.color1[1],
                self.color1[2],
                self.color2[0],
                self.color2[1],
                self.color2[2],
                0.7,
                cfg.width,
                cfg.height,
            )
        else:
            taichi_kernels._generate_linear_gradient(
                self.frame_buffer,
                self.color1[0],
                self.color1[1],
                self.color1[2],
                self.color2[0],
                self.color2[1],
                self.color2[2],
                current_angle,
                cfg.width,
                cfg.height,
            )

    def _render_particles(self, progress: float, cfg: TaichiTitleConfig):
        """Render bokeh or fireworks particles."""
        if not cfg.enable_bokeh:
            return

        self._update_bokeh_particles(progress)
        self.bokeh_buffer.fill(0)
        if cfg.is_birthday:
            particle_count = cfg.fireworks_burst_count * cfg.fireworks_particles_per_burst
        else:
            particle_count = cfg.bokeh_count
        taichi_kernels._render_bokeh_particles(
            self.bokeh_buffer, self._bokeh_particles, particle_count, cfg.width, cfg.height
        )
        taichi_kernels._composite_rgba_over(
            self.frame_buffer, self.bokeh_buffer, self.temp_buffer, 1.0
        )
        np.copyto(self.frame_buffer, self.temp_buffer)

    def _render_text(
        self,
        t: float,
        progress: float,
        cfg: TaichiTitleConfig,
        title: str,
        subtitle: str | None,
    ):
        """Render title and subtitle text onto the frame."""
        title_anim = self._compute_animation(t, progress, is_subtitle=False)
        title_size = int(cfg.height * cfg.title_size_ratio)
        subtitle_size = int(cfg.height * cfg.subtitle_size_ratio)

        if self._use_sdf:
            self._render_text_sdf(
                cfg, title, subtitle, title_anim, title_size, subtitle_size, t, progress
            )
        else:
            self._render_text_pil(cfg, title, subtitle, title_anim, subtitle_size, t, progress)

    def _render_text_sdf(
        self,
        cfg: TaichiTitleConfig,
        title: str,
        subtitle: str | None,
        title_anim: dict,
        title_size: int,
        subtitle_size: int,
        t: float,
        progress: float,
    ):
        """Render text using GPU SDF kernels."""
        if cfg.enable_shadow:
            self._render_sdf_text_direct(
                title,
                title_size,
                (0.0, 0.0, 0.0),
                title_anim["opacity"] * cfg.shadow_opacity,
                y_offset=title_anim["y_offset"],
                x_offset=title_anim["x_offset"],
                is_shadow=True,
            )
        self._render_sdf_text_direct(
            title,
            title_size,
            self.text_rgb,
            title_anim["opacity"],
            y_offset=title_anim["y_offset"],
            x_offset=title_anim["x_offset"],
        )
        if subtitle:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            subtitle_y_offset = subtitle_anim["y_offset"] + title_size * 0.8
            self._render_sdf_text_direct(
                subtitle,
                subtitle_size,
                self.text_rgb,
                subtitle_anim["opacity"],
                y_offset=subtitle_y_offset,
                x_offset=subtitle_anim["x_offset"],
            )

    def _render_text_pil(
        self,
        cfg: TaichiTitleConfig,
        title: str,
        subtitle: str | None,
        title_anim: dict,
        subtitle_size: int,
        t: float,
        progress: float,
    ):
        """Render text using PIL-based layers."""
        self._render_text_layers(title, subtitle)

        if cfg.enable_shadow and self._shadow_layer is not None:
            shadow_offset = max(2, int(cfg.height * cfg.shadow_offset_ratio))
            taichi_kernels._composite_text_with_offset(
                self.frame_buffer,
                self._shadow_layer,
                self.temp_buffer,
                title_anim["opacity"] * cfg.shadow_opacity,
                title_anim["y_offset"] + shadow_offset,
                title_anim["x_offset"] + shadow_offset,
            )
            np.copyto(self.frame_buffer, self.temp_buffer)

        if self._title_layer is not None:
            taichi_kernels._composite_text_with_offset(
                self.frame_buffer,
                self._title_layer,
                self.temp_buffer,
                title_anim["opacity"],
                title_anim["y_offset"],
                title_anim["x_offset"],
            )
            np.copyto(self.frame_buffer, self.temp_buffer)

        if self._subtitle_layer is not None:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            taichi_kernels._composite_text_with_offset(
                self.frame_buffer,
                self._subtitle_layer,
                self.temp_buffer,
                subtitle_anim["opacity"],
                subtitle_anim["y_offset"],
                subtitle_anim["x_offset"],
            )
            np.copyto(self.frame_buffer, self.temp_buffer)


# Re-export video creation function for backwards compatibility
from .taichi_video import create_title_video_taichi as create_title_video_taichi  # noqa: E402
