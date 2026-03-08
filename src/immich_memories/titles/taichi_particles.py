"""Particle system mixin for Taichi title renderer.

Provides bokeh particle and fireworks burst initialization and animation
for the TaichiTitleRenderer class.

Note: This module does NOT use 'from __future__ import annotations'
because Taichi kernels require actual type objects, not string annotations.
"""

import logging

import numpy as np

from .taichi_kernels import _hex_to_rgb

logger = logging.getLogger(__name__)


class TaichiParticlesMixin:
    """Mixin providing particle system methods for TaichiTitleRenderer."""

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

        # Festive firework colors (bright, saturated)
        firework_colors = cfg.birthday_colors or [
            (1.0, 0.85, 0.2),  # Gold
            (1.0, 0.3, 0.5),  # Hot pink
            (0.3, 0.8, 1.0),  # Cyan
            (1.0, 0.5, 0.2),  # Orange
            (0.6, 0.3, 1.0),  # Purple
            (0.2, 1.0, 0.5),  # Mint green
            (1.0, 1.0, 0.4),  # Yellow
        ]

        num_bursts = cfg.fireworks_burst_count
        particles_per_burst = cfg.fireworks_particles_per_burst
        total_particles = num_bursts * particles_per_burst

        # Particle array: x, y, vx, vy, size, opacity, r, g, b, birth_time
        # Index:          0  1  2   3   4     5        6  7  8  9
        particles = np.zeros((total_particles, 10), dtype=np.float32)

        # Create burst centers spread across the screen
        # Bursts happen at different times throughout the animation
        burst_centers = []
        burst_times = []
        for b in range(num_bursts):
            # Distribute bursts in a grid-like pattern with some randomness
            cols = 4
            rows = 3
            col = b % cols
            row = b // cols
            # Base position from grid
            base_x = cfg.width * (0.15 + col * 0.7 / (cols - 1))
            base_y = cfg.height * (0.15 + row * 0.5 / max(1, rows - 1))
            # Add randomness
            cx = base_x + rng.uniform(-cfg.width * 0.08, cfg.width * 0.08)
            cy = base_y + rng.uniform(-cfg.height * 0.08, cfg.height * 0.08)
            burst_centers.append((cx, cy))
            # Stagger burst times - some overlap for continuous effect
            burst_time = b * (0.5 / max(1, num_bursts - 1))  # Bursts in first 50% of duration
            burst_times.append(burst_time)

        # Create particles for each burst
        for b in range(num_bursts):
            cx, cy = burst_centers[b]
            birth_time = burst_times[b]

            # Pick a primary color for this burst (with some variation)
            base_color = firework_colors[b % len(firework_colors)]

            for p in range(particles_per_burst):
                idx = b * particles_per_burst + p

                # All particles start at burst center
                particles[idx, 0] = cx
                particles[idx, 1] = cy

                # Random velocity direction (radial burst)
                angle = rng.uniform(0, 2 * np.pi)
                # Use gaussian for more natural look (more particles near center)
                speed = abs(rng.normal(0, 1)) * min(cfg.width, cfg.height) * 0.25
                particles[idx, 2] = np.cos(angle) * speed  # vx
                particles[idx, 3] = np.sin(angle) * speed  # vy

                # Particle size - scale with resolution
                min_dim = min(cfg.width, cfg.height)
                particles[idx, 4] = rng.uniform(4, 16) * (min_dim / 1080)

                # Initial opacity
                particles[idx, 5] = rng.uniform(0.7, 1.0)

                # Color with slight variation
                r = min(1.0, base_color[0] + rng.uniform(-0.1, 0.1))
                g = min(1.0, base_color[1] + rng.uniform(-0.1, 0.1))
                b_col = min(1.0, base_color[2] + rng.uniform(-0.1, 0.1))
                particles[idx, 6] = max(0, r)
                particles[idx, 7] = max(0, g)
                particles[idx, 8] = max(0, b_col)

                # Birth time (when this particle becomes visible)
                particles[idx, 9] = birth_time

        self._fireworks_particles = particles
        self._fireworks_base = particles.copy()
        # Also set bokeh particles for compatibility
        self._bokeh_particles = np.zeros((total_particles, 8), dtype=np.float32)
        self._bokeh_particles_base = self._bokeh_particles.copy()

    def _init_aurora_blobs(self):
        """Initialize aurora gradient color blobs."""
        cfg = self.config
        rng = np.random.RandomState(42)

        # Use aurora_colors if specified, otherwise generate from gradient colors
        if cfg.aurora_colors:
            colors = [_hex_to_rgb(c) for c in cfg.aurora_colors]
        else:
            # Generate a palette from the two gradient colors plus variations
            c1 = self.color1
            c2 = self.color2
            # Create intermediate and varied colors
            colors = [
                c1,
                c2,
                ((c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2, (c1[2] + c2[2]) / 2),  # Midpoint
                (min(1, c1[0] * 1.1), c1[1] * 0.9, c1[2] * 0.95),  # Warmer variation
                (c2[0] * 0.95, min(1, c2[1] * 1.05), c2[2] * 0.9),  # Cooler variation
            ]

        num_blobs = len(colors)
        # Blob data: cx, cy, radius, r, g, b
        blobs = np.zeros((num_blobs, 6), dtype=np.float32)

        for i, color in enumerate(colors):
            # Distribute blobs across the screen
            blobs[i, 0] = rng.uniform(cfg.width * 0.1, cfg.width * 0.9)  # cx
            blobs[i, 1] = rng.uniform(cfg.height * 0.1, cfg.height * 0.9)  # cy
            blobs[i, 2] = rng.uniform(cfg.width * 0.4, cfg.width * 0.8)  # radius
            blobs[i, 3] = color[0]  # r
            blobs[i, 4] = color[1]  # g
            blobs[i, 5] = color[2]  # b

        self._aurora_blobs = blobs

    def _update_bokeh_particles(self, progress: float):
        """Update bokeh particle positions for current frame."""
        if not self.config.enable_bokeh:
            return

        cfg = self.config

        # Birthday mode: use fireworks physics
        if cfg.is_birthday:
            self._update_fireworks_particles(progress)
            return

        # Regular bokeh mode
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

        # Physics constants
        gravity = cfg.fireworks_gravity
        friction = cfg.fireworks_friction

        # Time since animation start (in seconds)
        progress * cfg.duration

        for i in range(n):
            base = self._fireworks_base[i]
            birth_time = base[9]

            # Check if particle is born yet
            if progress < birth_time:
                # Particle not visible yet
                self._bokeh_particles[i, 3] = 0.0  # Zero opacity
                continue

            # Time since this particle was born (0 to 1 normalized to remaining duration)
            particle_age = (progress - birth_time) / (1.0 - birth_time + 0.001)
            # Actual time in seconds since birth
            age_seconds = (progress - birth_time) * cfg.duration

            # Get initial velocity
            vx0 = base[2]
            vy0 = base[3]

            # Apply physics: position = initial + velocity*t + 0.5*gravity*t^2
            # Also apply friction decay to velocity over time
            friction_factor = friction ** (age_seconds * 30)  # Decay based on "frames"

            # Current velocity with friction
            vx0 * friction_factor
            vy0 * friction_factor + gravity * age_seconds * 60  # Gravity pulls down

            # Position from center + integrated velocity
            # Simplified: use average velocity * time
            x = base[0] + vx0 * age_seconds * (1 + friction_factor) / 2
            y = (
                base[1]
                + vy0 * age_seconds * (1 + friction_factor) / 2
                + 0.5 * gravity * (age_seconds * 60) ** 2
            )

            # Fade out based on age
            base_opacity = base[5]
            fade = max(0.0, 1.0 - particle_age * 1.5)  # Fade faster at end
            opacity = base_opacity * fade

            # Update bokeh buffer for rendering
            # bokeh format: x, y, size, opacity, angle, r, g, b
            self._bokeh_particles[i, 0] = x
            self._bokeh_particles[i, 1] = y
            self._bokeh_particles[i, 2] = base[4] * (1.0 + particle_age * 0.5)  # Grow slightly
            self._bokeh_particles[i, 3] = opacity
            self._bokeh_particles[i, 4] = 0  # angle unused
            self._bokeh_particles[i, 5] = base[6]  # R
            self._bokeh_particles[i, 6] = base[7]  # G
            self._bokeh_particles[i, 7] = base[8]  # B
