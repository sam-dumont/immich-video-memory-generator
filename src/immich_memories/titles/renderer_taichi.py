"""GPU-accelerated title screen renderer using Taichi.

Cross-platform GPU acceleration (Metal/CUDA/Vulkan/CPU fallback) for title
rendering with gradients, blur, vignette, bokeh particles, and SDF text.
~15-60x faster than PIL renderer. See taichi_kernels.py for GPU kernels,
taichi_particles.py for particle state, taichi_text.py for text.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Import module for runtime access to compiled kernels.
# Kernels are initially None and compiled lazily by init_taichi().
# Direct `from .taichi_kernels import _func` would capture None at import time,
# so we access them as `taichi_kernels._func` at call time instead.
from . import taichi_kernels
from .taichi_kernels import (
    TAICHI_AVAILABLE as TAICHI_AVAILABLE,
)
from .taichi_kernels import (
    _create_gaussian_kernel,
    _hex_to_rgb,
    is_taichi_available,
)
from .taichi_kernels import (
    init_taichi as init_taichi,
)
from .taichi_particles import ParticleField
from .taichi_text import TitleTextRenderer

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

    bg_color1: str = "#1A1A2E"
    bg_color2: str = "#16213E"
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
    bokeh_opacity_range: tuple[float, float] = (0.05, 0.15)  # Subtle on dark backgrounds
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

    text_color: str = "#FFFFFF"
    title_size_ratio: float = 0.12
    subtitle_size_ratio: float = 0.06
    font_family: str = "Montserrat"
    use_sdf_text: bool = False  # PIL text = pixel-sharp (matches map titles)
    enable_shadow: bool = False
    shadow_offset_ratio: float = 0.004  # Only used if shadow re-enabled
    shadow_opacity: float = 0.35

    fade_in_duration: float = 0.6
    fade_out_duration: float = 1.0
    slide_distance: int = 50
    scale_from: float = 0.85
    stagger_delay: float = 0.12

    # Custom background image (numpy float32 array, overrides gradient)
    # Used for map frames -- the map is rendered once, then used as static background
    background_image: np.ndarray | None = None

    # Per-frame background reader for slow-motion content-backed titles.
    # When set, read_frame() is called each frame instead of using static background_image.
    background_reader: Any | None = None

    # HDR output: when True, renderer outputs uint16 (65535 scale) instead of uint8
    hdr: bool = False

    # Reverse blur: for ending screens — blur increases instead of decreasing
    reverse_blur: bool = False

    _bokeh_particles: np.ndarray = field(default_factory=lambda: np.array([]))
    _bokeh_seed: int = 42


# =============================================================================
# Main Renderer Class
# =============================================================================


class TaichiTitleRenderer:
    """GPU-accelerated title renderer using Taichi.

    Pre-allocates GPU buffers and compiles kernels on first use.
    Subsequent renders reuse the compiled kernels for maximum performance.
    Particle state and text compositing are delegated to ParticleField and
    TitleTextRenderer; this class owns the background and the frame pipeline.
    """

    def __init__(self, config: TaichiTitleConfig | None = None):
        """Initialize renderer with configuration."""
        if not is_taichi_available():
            raise RuntimeError("Taichi not available. Install with: pip install taichi")

        self.config = config or TaichiTitleConfig()
        self.total_frames = int(self.config.fps * self.config.duration)

        h, w = self.config.height, self.config.width
        # WHY: GPU-resident buffers (ti.ndarray) eliminate implicit CPU↔GPU
        # transfers. Kernels operate on device memory; only background-in
        # and uint8-out cross the bus. See issue #164.
        from .taichi_kernels import GPUBuffers

        self.gpu = GPUBuffers(h, w, hdr=self.config.hdr)
        # The slow-mo sources are the same handful of frames for the whole
        # animation, so they go to the device once and the per-frame blend
        # happens there. Falls back to the numpy path when the reader cannot
        # offer them — different resolution, 16-bit HDR, or no reader at all.
        reader = self.config.background_reader
        frames: list = getattr(reader, "source_frames", None) or [] if reader else []
        self._sources_resident = bool(frames) and self.gpu.load_sources(frames)
        if self._sources_resident:
            logger.debug("Slow-mo sources resident on device: %d frames", len(frames))

        self._blur_kernel_np = _create_gaussian_kernel(self.config.blur_radius)

        self.color1 = _hex_to_rgb(self.config.bg_color1)
        self.color2 = _hex_to_rgb(self.config.bg_color2)

        self.particles = ParticleField(self.config, seed=self.config._bokeh_seed)
        self.text = TitleTextRenderer(self.config, self.gpu)

        logger.info(
            f"TaichiTitleRenderer initialized: {w}x{h} @ {self.config.fps}fps "
            f"(SDF: {self.text.use_sdf})"
        )

    def render_frame(
        self, frame_number: int, title: str, subtitle: str | None = None
    ) -> np.ndarray:
        """Render a single frame. One CPU-to-GPU in, one GPU-to-CPU out."""
        t = frame_number / self.config.fps
        progress = frame_number / self.total_frames
        cfg = self.config

        # 1. Background into the frame buffer
        has_animated_bg = self._load_background(cfg, t, progress)

        # 2. Blur (GPU→GPU, no transfers)
        if has_animated_bg:
            self._apply_animated_deblur(progress, cfg)
        elif cfg.blur_radius > 0:
            taichi_kernels._gaussian_blur_h(
                self.gpu.frame, self.gpu.temp, self._blur_kernel_np, cfg.blur_radius
            )
            taichi_kernels._gaussian_blur_v(
                self.gpu.temp, self.gpu.frame, self._blur_kernel_np, cfg.blur_radius
            )

        # 3. Color pulse (GPU in-place, non-animated only)
        if not has_animated_bg:
            brightness_delta = cfg.color_pulse_amount * math.sin(progress * 2 * math.pi)
            saturation_mult = 1.0 + 0.05 * math.sin(progress * 2 * math.pi + math.pi / 2)
            taichi_kernels._apply_color_pulse(self.gpu.frame, brightness_delta, saturation_mult)

        # 4. Vignette + noise (FUSED — one kernel launch instead of two)
        vignette_strength = cfg.vignette_strength + cfg.vignette_pulse * math.sin(
            progress * 2 * math.pi
        )
        noise_intensity = (
            cfg.noise_intensity if (cfg.enable_noise and cfg.noise_intensity > 0) else 0.0
        )
        noise_seed = frame_number * 12345 % 1000000 if noise_intensity > 0 else 0
        taichi_kernels._apply_vignette_and_noise(
            self.gpu.frame, vignette_strength, noise_intensity, noise_seed, cfg.width, cfg.height
        )

        # 5. Particles (GPU)
        self._render_particles(progress, cfg)

        # 6. Text (GPU)
        self.text.render(t, progress, title, subtitle)

        # 7. Finalize on GPU: clip + scale + convert, then single GPU→CPU readback
        max_val = 65535.0 if cfg.hdr else 255.0
        taichi_kernels._finalize_to_output(self.gpu.frame, self.gpu.output, max_val, hdr=cfg.hdr)
        return self.gpu.read_output()

    def _load_background(self, cfg, t: float, progress: float) -> bool:
        """Fill the frame buffer for this frame; True when it came from footage.

        Three sources in order of preference: the slow-mo reader, a still
        background image, a generated gradient.
        """
        reader = cfg.background_reader
        if reader is not None:
            if self._sources_resident:
                # Sources already on the device: interpolate there and send
                # four indices instead of a 25 MB frame.
                window = reader.next_blend()
                if window is not None:
                    self.gpu.blend_sources(*window)
                    return True
            else:
                bg_frame = reader.read_frame()
                if bg_frame is not None:
                    self.gpu.load_background(bg_frame)
                    return True

        if cfg.background_image is not None:
            self.gpu.load_background(cfg.background_image)
        else:
            self._render_gradient(t, progress, cfg)
        return False

    def _apply_animated_deblur(self, progress: float, cfg: TaichiTitleConfig) -> None:
        """Apply animated blur transition (all GPU-resident).

        Intro (reverse_blur=False): full blur → sharp reveal in last 1s
        Ending (reverse_blur=True): sharp → full blur in first 1s, then fade to white
        """
        transition_duration = 1.0
        if cfg.reverse_blur:
            transition_end = transition_duration / cfg.duration
            if progress > transition_end:
                blur_mix = 1.0
            else:
                t = progress / transition_end
                blur_mix = 3 * t * t - 2 * t * t * t
        else:
            deblur_start = 1.0 - (transition_duration / cfg.duration)
            if progress < deblur_start:
                blur_mix = 1.0
            else:
                t = (progress - deblur_start) / (1.0 - deblur_start)
                blur_mix = 1.0 - (3 * t * t - 2 * t * t * t)

        if blur_mix < 1.0:
            self.gpu.ensure_sharp()
            taichi_kernels._copy_field_3(self.gpu.frame, self.gpu.sharp)

        taichi_kernels._gaussian_blur_h(
            self.gpu.frame, self.gpu.temp, self._blur_kernel_np, cfg.blur_radius
        )
        taichi_kernels._gaussian_blur_v(
            self.gpu.temp, self.gpu.frame, self._blur_kernel_np, cfg.blur_radius
        )

        if blur_mix < 1.0:
            taichi_kernels._blend_fields(self.gpu.frame, self.gpu.sharp, 1.0 - blur_mix)

        brightness_delta = -0.15 * blur_mix
        taichi_kernels._apply_color_pulse(self.gpu.frame, brightness_delta, 1.0)

    def _render_gradient(self, t: float, progress: float, cfg: TaichiTitleConfig):
        """Render the background gradient directly to GPU frame buffer."""
        angle_rad = math.radians(cfg.gradient_angle)
        angle_offset = math.radians(cfg.gradient_rotation) * math.sin(progress * 2 * math.pi)
        current_angle = angle_rad + angle_offset

        if cfg.gradient_type == "aurora":
            if not hasattr(self, "_aurora_blobs"):
                self._init_aurora_blobs()
            taichi_kernels._generate_aurora_gradient(
                self.gpu.frame,
                self._aurora_blobs,
                len(self._aurora_blobs),
                cfg.width,
                cfg.height,
                t,
            )
        elif cfg.gradient_type == "radial":
            taichi_kernels._generate_radial_gradient(
                self.gpu.frame,
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
                self.gpu.frame,
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

    def _render_particles(self, progress: float, cfg: TaichiTitleConfig):
        """Render bokeh or fireworks particles (GPU-resident)."""
        if not cfg.enable_bokeh:
            return

        # WHY: particle position data (~500B for bokeh, ~10KB for fireworks) is
        # updated on CPU each frame. The implicit transfer is negligible vs
        # the ~13MB frame buffers that now stay on GPU.
        self.particles.update(progress)
        taichi_kernels._zero_field_4(self.gpu.bokeh)
        taichi_kernels._render_bokeh_particles(
            self.gpu.bokeh, self.particles.buffer, self.particles.count, cfg.width, cfg.height
        )
        taichi_kernels._composite_rgba_over(self.gpu.frame, self.gpu.bokeh, self.gpu.temp, 1.0)
        taichi_kernels._copy_field_3(self.gpu.temp, self.gpu.frame)
