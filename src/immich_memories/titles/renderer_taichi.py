"""GPU-accelerated title screen renderer using Taichi.

Cross-platform GPU acceleration (Metal/CUDA/Vulkan/CPU fallback) for title
rendering with gradients, blur, vignette, bokeh particles, and SDF text.
~15-60x faster than PIL renderer. See taichi_kernels.py for GPU kernels.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

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
    _get_system_font,
    _hex_to_rgb,
    find_font,
    get_cached_atlas,
    is_taichi_available,
    layout_text,
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
    # Used for map frames -- the map is rendered once, then used as static background
    background_image: np.ndarray | None = None

    _bokeh_particles: np.ndarray = field(default_factory=lambda: np.array([]))
    _bokeh_seed: int = 42


def _split_text_for_rendering(draw, text: str, font, max_width: float) -> list[str]:
    """Split text into lines using pixel widths, preferring comma boundaries."""

    def _measure(t: str) -> int:
        bbox = draw.textbbox((0, 0), t, font=font)
        return bbox[2] - bbox[0]

    if _measure(text) <= max_width:
        return [text]

    # Try comma split first
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        parts[0] += ","
        if all(_measure(p) <= max_width for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if _measure(test) > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


def split_title_lines(text: str, max_chars: int) -> list[str]:
    """Split title text into lines, preferring comma boundaries.

    Args:
        text: Title text to split.
        max_chars: Approximate max characters per line.

    Returns:
        List of lines.
    """
    if len(text) <= max_chars:
        return [text]

    # Prefer splitting at comma
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        # Keep comma on first part for visual continuity
        parts[0] += ","
        if all(len(p) <= max_chars for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


# =============================================================================
# Main Renderer Class
# =============================================================================


class TaichiTitleRenderer:
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

    # =========================================================================
    # Particle Methods (from TaichiParticlesMixin)
    # =========================================================================

    def _init_bokeh_particles(self):
        """Initialize bokeh particle positions, properties, and colors."""
        rng = np.random.RandomState(self.config._bokeh_seed)
        cfg = self.config
        min_dim = min(cfg.width, cfg.height)

        # Birthday mode: create fireworks burst particles
        if cfg.is_birthday:
            self._init_fireworks_particles(rng)
            return

        # Regular bokeh mode
        n = cfg.bokeh_count

        # Particle array: x, y, size, opacity, angle, r, g, b
        particles = np.zeros((n, 8), dtype=np.float32)
        for i in range(n):
            particles[i, 0] = rng.uniform(0, cfg.width)
            particles[i, 1] = rng.uniform(0, cfg.height)
            size_frac = rng.uniform(*cfg.bokeh_size_range)
            particles[i, 2] = size_frac * min_dim
            particles[i, 3] = rng.uniform(*cfg.bokeh_opacity_range)
            particles[i, 4] = rng.uniform(0, 2 * np.pi)

            # Warm bokeh color
            color = cfg.bokeh_color
            particles[i, 5] = color[0]  # R
            particles[i, 6] = color[1]  # G
            particles[i, 7] = color[2]  # B

        self._bokeh_particles = particles
        self._bokeh_particles_base = particles.copy()

    def _init_fireworks_particles(self, rng: np.random.RandomState):
        """Initialize fireworks burst particles for birthday mode."""
        cfg = self.config

        firework_colors = cfg.birthday_colors or [
            (1.0, 0.85, 0.2),
            (1.0, 0.3, 0.5),
            (0.3, 0.8, 1.0),
            (1.0, 0.5, 0.2),
            (0.6, 0.3, 1.0),
            (0.2, 1.0, 0.5),
            (1.0, 1.0, 0.4),
        ]

        num_bursts = cfg.fireworks_burst_count
        particles_per_burst = cfg.fireworks_particles_per_burst
        total_particles = num_bursts * particles_per_burst

        # Particle array: x, y, vx, vy, size, opacity, r, g, b, birth_time
        particles = np.zeros((total_particles, 10), dtype=np.float32)

        burst_centers = []
        burst_times = []
        for b in range(num_bursts):
            cols, rows = 4, 3
            col = b % cols
            row = b // cols
            base_x = cfg.width * (0.15 + col * 0.7 / (cols - 1))
            base_y = cfg.height * (0.15 + row * 0.5 / max(1, rows - 1))
            cx = base_x + rng.uniform(-cfg.width * 0.08, cfg.width * 0.08)
            cy = base_y + rng.uniform(-cfg.height * 0.08, cfg.height * 0.08)
            burst_centers.append((cx, cy))
            burst_time = b * (0.5 / max(1, num_bursts - 1))
            burst_times.append(burst_time)

        for b in range(num_bursts):
            cx, cy = burst_centers[b]
            birth_time = burst_times[b]
            base_color = firework_colors[b % len(firework_colors)]

            for p in range(particles_per_burst):
                idx = b * particles_per_burst + p
                particles[idx, 0] = cx
                particles[idx, 1] = cy
                angle = rng.uniform(0, 2 * np.pi)
                speed = abs(rng.normal(0, 1)) * min(cfg.width, cfg.height) * 0.25
                particles[idx, 2] = np.cos(angle) * speed
                particles[idx, 3] = np.sin(angle) * speed
                min_dim = min(cfg.width, cfg.height)
                particles[idx, 4] = rng.uniform(4, 16) * (min_dim / 1080)
                particles[idx, 5] = rng.uniform(0.7, 1.0)
                r = min(1.0, base_color[0] + rng.uniform(-0.1, 0.1))
                g = min(1.0, base_color[1] + rng.uniform(-0.1, 0.1))
                b_col = min(1.0, base_color[2] + rng.uniform(-0.1, 0.1))
                particles[idx, 6] = max(0, r)
                particles[idx, 7] = max(0, g)
                particles[idx, 8] = max(0, b_col)
                particles[idx, 9] = birth_time

        self._fireworks_particles = particles
        self._fireworks_base = particles.copy()
        self._bokeh_particles = np.zeros((total_particles, 8), dtype=np.float32)
        self._bokeh_particles_base = self._bokeh_particles.copy()

    def _init_aurora_blobs(self):
        """Initialize aurora gradient color blobs."""
        cfg = self.config
        rng = np.random.RandomState(42)

        if cfg.aurora_colors:
            colors = [_hex_to_rgb(c) for c in cfg.aurora_colors]
        else:
            c1 = self.color1
            c2 = self.color2
            colors = [
                c1,
                c2,
                ((c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2, (c1[2] + c2[2]) / 2),
                (min(1, c1[0] * 1.1), c1[1] * 0.9, c1[2] * 0.95),
                (c2[0] * 0.95, min(1, c2[1] * 1.05), c2[2] * 0.9),
            ]

        num_blobs = len(colors)
        blobs = np.zeros((num_blobs, 6), dtype=np.float32)

        for i, color in enumerate(colors):
            blobs[i, 0] = rng.uniform(cfg.width * 0.1, cfg.width * 0.9)
            blobs[i, 1] = rng.uniform(cfg.height * 0.1, cfg.height * 0.9)
            blobs[i, 2] = rng.uniform(cfg.width * 0.4, cfg.width * 0.8)
            blobs[i, 3] = color[0]
            blobs[i, 4] = color[1]
            blobs[i, 5] = color[2]

        self._aurora_blobs = blobs

    def _update_bokeh_particles(self, progress: float):
        """Update bokeh particle positions for current frame."""
        if not self.config.enable_bokeh:
            return

        cfg = self.config

        if cfg.is_birthday:
            self._update_fireworks_particles(progress)
            return

        min_dim = min(cfg.width, cfg.height)
        drift_speed = cfg.bokeh_drift_speed
        n = cfg.bokeh_count
        drift = progress * min_dim * drift_speed

        for i in range(n):
            angle = self._bokeh_particles_base[i, 4]
            base_x = self._bokeh_particles_base[i, 0]
            base_y = self._bokeh_particles_base[i, 1]

            new_x = (base_x + np.cos(angle) * drift) % cfg.width
            new_y = (base_y + np.sin(angle) * drift) % cfg.height

            self._bokeh_particles[i, 0] = new_x
            self._bokeh_particles[i, 1] = new_y

            base_opacity = self._bokeh_particles_base[i, 3]
            pulse = np.sin(progress * 2 * np.pi + i * 0.5) * 0.3 + 0.7
            self._bokeh_particles[i, 3] = base_opacity * pulse

    def _update_fireworks_particles(self, progress: float):
        """Update fireworks particles with physics simulation."""
        cfg = self.config
        n = len(self._fireworks_particles)
        gravity = cfg.fireworks_gravity
        friction = cfg.fireworks_friction

        progress * cfg.duration

        for i in range(n):
            base = self._fireworks_base[i]
            birth_time = base[9]

            if progress < birth_time:
                self._bokeh_particles[i, 3] = 0.0
                continue

            particle_age = (progress - birth_time) / (1.0 - birth_time + 0.001)
            age_seconds = (progress - birth_time) * cfg.duration

            vx0 = base[2]
            vy0 = base[3]

            friction_factor = friction ** (age_seconds * 30)

            vx0 * friction_factor
            vy0 * friction_factor + gravity * age_seconds * 60

            x = base[0] + vx0 * age_seconds * (1 + friction_factor) / 2
            y = (
                base[1]
                + vy0 * age_seconds * (1 + friction_factor) / 2
                + 0.5 * gravity * (age_seconds * 60) ** 2
            )

            base_opacity = base[5]
            fade = max(0.0, 1.0 - particle_age * 1.5)
            opacity = base_opacity * fade

            self._bokeh_particles[i, 0] = x
            self._bokeh_particles[i, 1] = y
            self._bokeh_particles[i, 2] = base[4] * (1.0 + particle_age * 0.5)
            self._bokeh_particles[i, 3] = opacity
            self._bokeh_particles[i, 4] = 0
            self._bokeh_particles[i, 5] = base[6]
            self._bokeh_particles[i, 6] = base[7]
            self._bokeh_particles[i, 7] = base[8]

    # =========================================================================
    # Text Methods (from TaichiTextMixin)
    # =========================================================================

    def _init_sdf_atlas(self):
        """Initialize SDF font atlas for GPU text rendering."""
        if not SDF_AVAILABLE or not find_font:
            logger.warning("SDF font support not available")
            self._use_sdf = False
            return

        font_path = find_font(self.config.font_family)
        if not font_path:
            logger.warning(f"Font '{self.config.font_family}' not found, using fallback")
            font_path = find_font("Helvetica")

        if not font_path:
            logger.warning("No fonts found, falling back to PIL")
            self._use_sdf = False
            return

        atlas_size = 128
        self._sdf_atlas = get_cached_atlas(font_path, atlas_size)
        self._sdf_atlas_float = self._sdf_atlas.texture.astype(np.float32) / 255.0
        logger.info(f"SDF atlas loaded: {self._sdf_atlas.texture.shape}")

    def _render_sdf_text_direct(
        self,
        text: str,
        font_size: int,
        color: tuple[float, float, float],
        opacity: float,
        y_offset: float = 0.0,
        x_offset: float = 0.0,
        is_shadow: bool = False,
    ):
        """Render text directly onto frame buffer using SDF GPU kernel."""
        if not self._use_sdf or self._sdf_atlas is None or taichi_kernels._render_sdf_text is None:
            return

        scale = font_size / self._sdf_atlas.font_size
        glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        safe_width = self.config.width * 0.8
        if text_width > safe_width:
            width_scale = safe_width / text_width
            scale = scale * width_scale
            glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        center_x = (self.config.width - text_width) / 2
        center_y = (self.config.height - text_height) / 2 + self._sdf_atlas.ascender * scale / 2

        shadow_offset = 0.0
        if is_shadow:
            shadow_offset = max(2, int(self.config.height * self.config.shadow_offset_ratio))

        smoothing = max(0.05, min(0.2, 0.15 / scale))

        taichi_kernels._render_sdf_text(
            self.frame_buffer,
            self._sdf_atlas_float,
            glyph_data,
            len(glyph_data),
            color[0],
            color[1],
            color[2],
            opacity,
            scale,
            center_x + x_offset + shadow_offset,
            center_y + y_offset + shadow_offset,
            smoothing,
        )

    def _render_text_layer(
        self,
        text: str,
        font_size: int,
        color: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Render text to RGBA numpy array using PIL."""
        w, h = self.config.width, self.config.height
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_path = _get_system_font(self.config.font_family)

        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        safe_width = w * 0.88
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width > safe_width:
            self._draw_multiline_centered(draw, text, font, font_size, safe_width, w, h, color)
        else:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (w - tw) // 2
            y = (h - th) // 2
            draw.text((x, y), text, font=font, fill=color)

        return np.array(img, dtype=np.float32) / 255.0

    @staticmethod
    def _draw_multiline_centered(
        draw,
        text: str,
        font,
        font_size: int,
        max_width: float,
        width: int,
        height: int,
        color: tuple[int, int, int, int],
    ) -> None:
        """Word-wrap text with comma-aware splitting and draw centered."""
        lines = _split_text_for_rendering(draw, text, font, max_width)
        line_height = int(font_size * 1.2)
        total_h = line_height * len(lines)
        start_y = (height - total_h) // 2
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            x = (width - lw) // 2
            y = start_y + i * line_height
            draw.text((x, y), line, font=font, fill=color)

    def _render_text_layers(self, title: str, subtitle: str | None):
        """Pre-render text layers (cached)."""
        if self._cached_text == (title, subtitle):
            return

        title_size = int(self.config.height * self.config.title_size_ratio)
        subtitle_size = int(self.config.height * self.config.subtitle_size_ratio)

        tr, tg, tb = self.text_rgb
        text_rgba = (int(tr * 255), int(tg * 255), int(tb * 255), 255)
        shadow_rgba = (0, 0, 0, int(self.config.shadow_opacity * 255))

        self._title_layer = self._render_text_layer(title, title_size, text_rgba)

        if self.config.enable_shadow:
            self._shadow_layer = self._render_text_layer(title, title_size, shadow_rgba)

        if subtitle:
            self._subtitle_layer = self._render_text_layer(subtitle, subtitle_size, text_rgba)
        else:
            self._subtitle_layer = None

        self._cached_text = (title, subtitle)
