"""Bokeh and fireworks particle state for the Taichi title renderer.

Everything here is CPU-side numpy. One small float32 array carries the
particles that the GPU kernel draws each frame; the frame buffers themselves
never leave the device. See taichi_kernels.py for the drawing kernel.
"""

from typing import Protocol

import numpy as np


class ParticleConfig(Protocol):
    """Title config fields the particle simulation reads."""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def duration(self) -> float: ...

    @property
    def enable_bokeh(self) -> bool: ...

    @property
    def bokeh_count(self) -> int: ...

    @property
    def bokeh_size_range(self) -> tuple[float, float]: ...

    @property
    def bokeh_opacity_range(self) -> tuple[float, float]: ...

    @property
    def bokeh_drift_speed(self) -> float: ...

    @property
    def bokeh_color(self) -> tuple[float, float, float]: ...

    @property
    def is_birthday(self) -> bool: ...

    @property
    def birthday_colors(self) -> list: ...

    @property
    def fireworks_burst_count(self) -> int: ...

    @property
    def fireworks_particles_per_burst(self) -> int: ...

    @property
    def fireworks_gravity(self) -> float: ...

    @property
    def fireworks_friction(self) -> float: ...


class ParticleField:
    """Bokeh drift or fireworks physics for one title animation.

    Holds a single (n, 8) float32 array -- x, y, size, opacity, angle, r, g, b --
    that the GPU kernel reads verbatim. `update()` rewrites it in place, so the
    per-frame upload stays the few hundred bytes it was at construction.
    """

    def __init__(self, config: ParticleConfig, seed: int = 42):
        """Build the particle array, or leave it empty when bokeh is disabled."""
        self.config = config
        self.buffer: np.ndarray = np.array([])
        self._base: np.ndarray = np.array([])
        if config.enable_bokeh:
            self._init_bokeh_particles(seed)

    @property
    def count(self) -> int:
        """Number of particles the kernel should draw."""
        cfg = self.config
        if cfg.is_birthday:
            return cfg.fireworks_burst_count * cfg.fireworks_particles_per_burst
        return cfg.bokeh_count

    def update(self, progress: float) -> None:
        """Advance particle positions and opacities to `progress` (0..1)."""
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
            angle = self._base[i, 4]
            base_x = self._base[i, 0]
            base_y = self._base[i, 1]

            new_x = (base_x + np.cos(angle) * drift) % cfg.width
            new_y = (base_y + np.sin(angle) * drift) % cfg.height

            self.buffer[i, 0] = new_x
            self.buffer[i, 1] = new_y

            base_opacity = self._base[i, 3]
            pulse = np.sin(progress * 2 * np.pi + i * 0.5) * 0.3 + 0.7
            self.buffer[i, 3] = base_opacity * pulse

    def _init_bokeh_particles(self, seed: int):
        """Initialize bokeh particle positions, properties, and colors."""
        rng = np.random.RandomState(seed)
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

        self.buffer = particles
        self._base = particles.copy()

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
        self.buffer = np.zeros((total_particles, 8), dtype=np.float32)
        self._base = self.buffer.copy()

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
                self.buffer[i, 3] = 0.0
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

            self.buffer[i, 0] = x
            self.buffer[i, 1] = y
            self.buffer[i, 2] = base[4] * (1.0 + particle_age * 0.5)
            self.buffer[i, 3] = opacity
            self.buffer[i, 4] = 0
            self.buffer[i, 5] = base[6]
            self.buffer[i, 6] = base[7]
            self.buffer[i, 7] = base[8]
