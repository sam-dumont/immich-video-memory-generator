"""Animated background generation for title screens.

This module provides animated variants of background effects:
- Animated linear gradients with angle rotation and color pulsing
- Animated radial gradients with pulsing glow
- Animated vignette with breathing effect
- Bokeh particle overlay
- Main dispatcher for animated backgrounds
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .backgrounds import (
    HAS_PIL,
    BackgroundType,
    _get_coord_grids,
    hex_to_rgb,
)

if TYPE_CHECKING:
    pass

try:
    from PIL import Image, ImageFilter

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# =============================================================================
# Animated Background Support
# =============================================================================


@dataclass
class AnimatedBackgroundConfig:
    """Configuration for animated backgrounds.

    Values are tuned for NOTICEABLE but not distracting motion.
    The goal is to prevent the title from looking like a static image.
    """

    # Gradient angle animation (degrees) - visible rotation
    angle_shift_range: float = 45.0  # Total degrees to shift (+/-) - was 25
    angle_shift_speed: float = 0.5  # Full cycles per duration - slower for elegance

    # Color pulsing - noticeable color breathing
    color_pulse_amount: float = 0.25  # How much to shift colors (0-1) - was 0.15
    color_pulse_speed: float = 0.8  # Pulse cycles per duration

    # Vignette breathing - more dramatic
    vignette_pulse_amount: float = 0.25  # Vignette intensity variation - was 0.15
    vignette_pulse_speed: float = 0.5  # Pulse cycles per duration - slower

    # Radial glow pulse - bigger effect
    radial_pulse_amount: float = 0.35  # Radius variation - was 0.2
    radial_pulse_speed: float = 0.4  # Pulse cycles per duration - slower

    # Floating bokeh particles overlay
    enable_bokeh: bool = True  # Add floating bokeh particles
    bokeh_count: int = 12  # Number of bokeh circles
    bokeh_size_range: tuple[float, float] = (0.02, 0.08)  # Size as fraction of min dimension
    bokeh_opacity_range: tuple[float, float] = (0.03, 0.12)  # Very subtle
    bokeh_drift_speed: float = 0.3  # How fast particles drift


def _shift_color(
    color: tuple[int, int, int],
    shift: float,
    brightness_only: bool = True,
) -> tuple[int, int, int]:
    """Shift a color by a small amount.

    Args:
        color: RGB color tuple.
        shift: Shift amount (-1 to 1).
        brightness_only: If True, only shift brightness, not hue.

    Returns:
        Shifted RGB color.
    """
    if brightness_only:
        # Simple brightness shift
        factor = 1.0 + shift * 0.15
        return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore
    else:
        # Shift each channel slightly differently for subtle color variation
        r_shift = shift * 10
        g_shift = shift * 8
        b_shift = shift * 12
        return (
            max(0, min(255, int(color[0] + r_shift))),
            max(0, min(255, int(color[1] + g_shift))),
            max(0, min(255, int(color[2] + b_shift))),
        )


def create_animated_gradient(
    width: int,
    height: int,
    colors: list[str],
    base_angle: float,
    progress: float,
    config: AnimatedBackgroundConfig | None = None,
) -> Image.Image:
    """Create an animated gradient background frame using NumPy vectorization.

    The gradient animates by:
    - Gently rotating the angle back and forth
    - Subtly pulsing the colors (brightness)

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        colors: List of hex color strings.
        base_angle: Base gradient angle in degrees.
        progress: Animation progress (0.0 to 1.0).
        config: Animation configuration.

    Returns:
        PIL Image with animated gradient.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    cfg = config or AnimatedBackgroundConfig()

    # Calculate animated angle using smooth sine wave
    angle_phase = progress * cfg.angle_shift_speed * 2 * math.pi
    angle_offset = math.sin(angle_phase) * cfg.angle_shift_range
    animated_angle = base_angle + angle_offset

    # Calculate color pulse using different phase
    color_phase = progress * cfg.color_pulse_speed * 2 * math.pi
    color_shift = math.sin(color_phase) * cfg.color_pulse_amount

    # Convert and shift colors
    rgb_colors = [hex_to_rgb(c) for c in colors]
    shifted_colors = [_shift_color(c, color_shift) for c in rgb_colors]

    # Create the gradient with animated parameters
    if len(shifted_colors) < 2:
        color = shifted_colors[0] if shifted_colors else (255, 255, 255)
        return Image.new("RGB", (width, height), color)

    # Convert to numpy array for vectorization
    shifted_np = np.array(shifted_colors, dtype=np.float32)

    angle_rad = math.radians(animated_angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    diagonal = math.sqrt(width**2 + height**2)

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Center and project (vectorized)
    cx = x_coords - width / 2
    cy = y_coords - height / 2
    projection = (cx * cos_a + cy * sin_a) / diagonal + 0.5
    t = np.clip(projection, 0.0, 1.0)

    # Vectorized color interpolation
    if len(shifted_np) == 2:
        t_expanded = t[:, :, np.newaxis]
        result = shifted_np[0] + (shifted_np[1] - shifted_np[0]) * t_expanded
    else:
        segment_count = len(shifted_np) - 1
        segment = np.clip((t * segment_count).astype(np.int32), 0, segment_count - 1)
        local_t = (t * segment_count) - segment
        local_t_expanded = local_t[:, :, np.newaxis]
        color1 = shifted_np[segment]
        color2 = shifted_np[np.minimum(segment + 1, segment_count)]
        result = color1 + (color2 - color1) * local_t_expanded

    # Convert to uint8 and create image
    result = np.clip(result, 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode="RGB")


def create_animated_radial(
    width: int,
    height: int,
    center_color: str,
    edge_color: str,
    progress: float,
    config: AnimatedBackgroundConfig | None = None,
) -> Image.Image:
    """Create an animated radial gradient with pulsing glow using NumPy vectorization.

    Args:
        width: Image width.
        height: Image height.
        center_color: Hex color at center.
        edge_color: Hex color at edges.
        progress: Animation progress (0.0 to 1.0).
        config: Animation configuration.

    Returns:
        PIL Image with animated radial gradient.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    cfg = config or AnimatedBackgroundConfig()

    # Pulse the radius
    radial_phase = progress * cfg.radial_pulse_speed * 2 * math.pi
    radius_factor = 0.7 + math.sin(radial_phase) * cfg.radial_pulse_amount

    # Pulse colors
    color_phase = progress * cfg.color_pulse_speed * 2 * math.pi
    color_shift = math.sin(color_phase) * cfg.color_pulse_amount

    center_rgb = np.array(_shift_color(hex_to_rgb(center_color), color_shift), dtype=np.float32)
    edge_rgb = np.array(_shift_color(hex_to_rgb(edge_color), -color_shift * 0.5), dtype=np.float32)

    center_x = width / 2
    center_y = height / 2
    max_radius = math.sqrt(center_x**2 + center_y**2) * radius_factor

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Calculate distance (vectorized)
    dist = np.sqrt((x_coords - center_x) ** 2 + (y_coords - center_y) ** 2)
    t = np.clip(dist / max_radius, 0.0, 1.0)
    t = 1 - (1 - t) ** 2  # Ease out

    # Vectorized color interpolation
    t_expanded = t[:, :, np.newaxis]
    result = center_rgb + (edge_rgb - center_rgb) * t_expanded

    # Convert to uint8 and create image
    result = np.clip(result, 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode="RGB")


def create_animated_vignette(
    width: int,
    height: int,
    center_color: str,
    edge_color: str,
    progress: float,
    config: AnimatedBackgroundConfig | None = None,
) -> Image.Image:
    """Create an animated vignette with breathing effect using NumPy vectorization.

    Args:
        width: Image width.
        height: Image height.
        center_color: Hex color at center.
        edge_color: Hex color at edges.
        progress: Animation progress (0.0 to 1.0).
        config: Animation configuration.

    Returns:
        PIL Image with animated vignette.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    cfg = config or AnimatedBackgroundConfig()

    # Animate vignette intensity
    vignette_phase = progress * cfg.vignette_pulse_speed * 2 * math.pi
    strength = 0.3 + math.sin(vignette_phase) * cfg.vignette_pulse_amount

    # Pulse colors subtly
    color_phase = progress * cfg.color_pulse_speed * 2 * math.pi
    color_shift = math.sin(color_phase) * cfg.color_pulse_amount * 0.5

    center_rgb = np.array(_shift_color(hex_to_rgb(center_color), color_shift), dtype=np.float32)
    edge_rgb = np.array(hex_to_rgb(edge_color), dtype=np.float32)

    center_x = width / 2
    center_y = height / 2
    max_dist_x = center_x
    max_dist_y = center_y

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Elliptical distance (vectorized)
    dx = (x_coords - center_x) / max_dist_x
    dy = (y_coords - center_y) / max_dist_y
    dist = np.sqrt(dx**2 + dy**2)

    # Apply vignette curve (vectorized)
    t = np.where(dist < 0.5, 0.0, (dist - 0.5) * 2)
    t = np.clip(t, 0.0, 1.0)
    t = t * strength
    t = t**1.5

    # Vectorized color interpolation
    t_expanded = t[:, :, np.newaxis]
    result = center_rgb + (edge_rgb - center_rgb) * t_expanded

    # Convert to uint8 and create image
    result = np.clip(result, 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode="RGB")


def _create_bokeh_overlay(
    width: int,
    height: int,
    progress: float,
    config: AnimatedBackgroundConfig,
    seed: int = 42,
) -> Image.Image:
    """Create a bokeh particle overlay with drifting circles.

    Args:
        width: Image width.
        height: Image height.
        progress: Animation progress (0.0 to 1.0).
        config: Animation configuration.
        seed: Random seed for reproducible particle positions.

    Returns:
        RGBA image with bokeh overlay (mostly transparent).
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required")

    from PIL import ImageDraw

    # Create transparent overlay
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Use seeded random for consistent particles between frames
    rng = np.random.RandomState(seed)
    min_dim = min(width, height)

    for i in range(config.bokeh_count):
        # Base position (seeded)
        base_x = rng.uniform(0, width)
        base_y = rng.uniform(0, height)

        # Drift based on progress (each particle has different speed/direction)
        drift_angle = rng.uniform(0, 2 * math.pi)
        drift_speed = rng.uniform(0.5, 1.5) * config.bokeh_drift_speed
        drift_distance = progress * min_dim * drift_speed

        x = base_x + math.cos(drift_angle) * drift_distance
        y = base_y + math.sin(drift_angle) * drift_distance

        # Wrap around edges
        x = x % width
        y = y % height

        # Size (seeded)
        size_fraction = rng.uniform(*config.bokeh_size_range)
        radius = int(min_dim * size_fraction)

        # Opacity (seeded) - pulse slightly with progress
        base_opacity = rng.uniform(*config.bokeh_opacity_range)
        pulse = math.sin(progress * 2 * math.pi + i * 0.5) * 0.3 + 0.7
        opacity = int(255 * base_opacity * pulse)

        # Draw soft circle (white, will blend with background)
        x1, y1 = int(x - radius), int(y - radius)
        x2, y2 = int(x + radius), int(y + radius)
        draw.ellipse([x1, y1, x2, y2], fill=(255, 255, 255, opacity))

    # Blur for soft bokeh effect (capped for performance)
    blur_radius = min(15, max(5, min_dim // 80))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    return overlay


def create_animated_background(
    width: int,
    height: int,
    background_type: str,
    colors: list[str],
    angle: float,
    progress: float,
    config: AnimatedBackgroundConfig | None = None,
) -> Image.Image:
    """Create an animated background frame.

    This is the main entry point for animated backgrounds. It dispatches
    to the appropriate animation function based on background type.

    Args:
        width: Image width.
        height: Image height.
        background_type: Type of background.
        colors: List of hex color strings.
        angle: Base gradient angle.
        progress: Animation progress (0.0 to 1.0).
        config: Animation configuration.

    Returns:
        PIL Image with animated background.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    if len(colors) < 2:
        # Single color - just pulse it
        cfg = config or AnimatedBackgroundConfig()
        color_phase = progress * cfg.color_pulse_speed * 2 * math.pi
        color_shift = math.sin(color_phase) * cfg.color_pulse_amount
        base_rgb = hex_to_rgb(colors[0]) if colors else (255, 255, 255)
        shifted = _shift_color(base_rgb, color_shift)
        return Image.new("RGB", (width, height), shifted)

    bg_type = background_type.lower() if isinstance(background_type, str) else background_type

    cfg = config or AnimatedBackgroundConfig()

    if bg_type in ("radial_gradient", BackgroundType.RADIAL_GRADIENT.value):
        image = create_animated_radial(width, height, colors[0], colors[1], progress, config)

    elif bg_type in ("vignette", BackgroundType.VIGNETTE.value):
        # Vignette should fade to WHITE at edges for a bright, clean look
        image = create_animated_vignette(width, height, colors[0], "#FFFFFF", progress, config)

    else:
        # Default to animated gradient (works for solid_gradient, soft_gradient)
        image = create_animated_gradient(width, height, colors, angle, progress, config)

        # Apply blur for soft gradient
        # Radius scales with image size but capped for performance
        if bg_type in ("soft_gradient", BackgroundType.SOFT_GRADIENT.value):
            blur_radius = min(20, max(8, min(width, height) // 100))
            image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Apply bokeh overlay if enabled (for all background types)
    if cfg.enable_bokeh:
        bokeh = _create_bokeh_overlay(width, height, progress, cfg)
        image = image.convert("RGBA")
        image = Image.alpha_composite(image, bokeh)
        image = image.convert("RGB")

    return image
