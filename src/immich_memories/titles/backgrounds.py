"""Background generation for title screens.

This module provides:
- Linear gradient backgrounds
- Radial gradient backgrounds
- Vignette effects
- Static and animated background options
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

try:
    from PIL import Image, ImageFilter

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# Cache for coordinate meshgrids to avoid recreating ~66MB arrays per frame at 4K
_COORD_CACHE: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def _get_coord_grids(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Get cached coordinate grids for the given dimensions.

    Memory optimization: reuses meshgrid arrays instead of creating new ones
    for each frame. For 4K (3840x2160), this saves ~66MB per frame.

    Args:
        width: Image width.
        height: Image height.

    Returns:
        Tuple of (y_coords, x_coords) numpy arrays.
    """
    key = (width, height)
    if key not in _COORD_CACHE:
        _COORD_CACHE[key] = np.meshgrid(
            np.arange(height, dtype=np.float32), np.arange(width, dtype=np.float32), indexing="ij"
        )
    return _COORD_CACHE[key]


class BackgroundType(Enum):
    """Types of background effects."""

    SOLID_GRADIENT = "solid_gradient"
    SOFT_GRADIENT = "soft_gradient"
    RADIAL_GRADIENT = "radial_gradient"
    VIGNETTE = "vignette"
    SOLID = "solid"


@dataclass
class GradientStop:
    """A color stop in a gradient."""

    position: float  # 0.0 to 1.0
    color: tuple[int, int, int]  # RGB


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple.

    Args:
        hex_color: Hex color string (e.g., "#FFF5E6" or "FFF5E6").

    Returns:
        RGB tuple (r, g, b) with values 0-255.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert RGB tuple to hex color.

    Args:
        rgb: RGB tuple (r, g, b) with values 0-255.

    Returns:
        Hex color string (e.g., "#FFF5E6").
    """
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def interpolate_color(
    color1: tuple[int, int, int],
    color2: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Linearly interpolate between two colors.

    Args:
        color1: Starting RGB color.
        color2: Ending RGB color.
        t: Interpolation factor (0.0 to 1.0).

    Returns:
        Interpolated RGB color.
    """
    return tuple(int(color1[i] + (color2[i] - color1[i]) * t) for i in range(3))  # type: ignore


def create_gradient_background(
    width: int,
    height: int,
    colors: list[str],
    angle: float = 135.0,
) -> Image.Image:
    """Create a linear gradient background using NumPy vectorization.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        colors: List of hex color strings (2 or more).
        angle: Gradient angle in degrees (0 = left-to-right, 90 = top-to-bottom).

    Returns:
        PIL Image with gradient background.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    if len(colors) < 2:
        raise ValueError("At least 2 colors required for gradient")

    # Convert colors to numpy arrays
    rgb_colors = np.array([hex_to_rgb(c) for c in colors], dtype=np.float32)

    # Convert angle to radians
    angle_rad = math.radians(angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    diagonal = math.sqrt(width**2 + height**2)

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Center the coordinates
    cx = x_coords - width / 2
    cy = y_coords - height / 2

    # Project onto gradient direction (vectorized)
    projection = (cx * cos_a + cy * sin_a) / diagonal + 0.5

    # Clamp to 0-1
    t = np.clip(projection, 0.0, 1.0)

    # Vectorized color interpolation
    if len(rgb_colors) == 2:
        # Simple two-color gradient
        t_expanded = t[:, :, np.newaxis]
        result = rgb_colors[0] + (rgb_colors[1] - rgb_colors[0]) * t_expanded
    else:
        # Multi-stop gradient
        segment_count = len(rgb_colors) - 1
        segment = np.clip((t * segment_count).astype(np.int32), 0, segment_count - 1)
        local_t = (t * segment_count) - segment

        # Get colors for each segment
        local_t_expanded = local_t[:, :, np.newaxis]
        color1 = rgb_colors[segment]
        color2 = rgb_colors[np.minimum(segment + 1, segment_count)]
        result = color1 + (color2 - color1) * local_t_expanded

    # Convert to uint8 and create image
    result = np.clip(result, 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode="RGB")


def create_radial_gradient(
    width: int,
    height: int,
    center_color: str,
    edge_color: str,
    radius_ratio: float = 0.7,
) -> Image.Image:
    """Create a radial gradient background using NumPy vectorization.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        center_color: Hex color at center.
        edge_color: Hex color at edges.
        radius_ratio: How far from center the gradient extends (0.5 to 1.5).

    Returns:
        PIL Image with radial gradient.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    center_rgb = np.array(hex_to_rgb(center_color), dtype=np.float32)
    edge_rgb = np.array(hex_to_rgb(edge_color), dtype=np.float32)

    center_x = width / 2
    center_y = height / 2
    max_radius = math.sqrt(center_x**2 + center_y**2) * radius_ratio

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Calculate distance from center (vectorized)
    dist = np.sqrt((x_coords - center_x) ** 2 + (y_coords - center_y) ** 2)

    # Normalize and clamp
    t = np.clip(dist / max_radius, 0.0, 1.0)

    # Smooth the gradient (ease-out)
    t = 1 - (1 - t) ** 2

    # Vectorized color interpolation
    t_expanded = t[:, :, np.newaxis]
    result = center_rgb + (edge_rgb - center_rgb) * t_expanded

    # Convert to uint8 and create image
    result = np.clip(result, 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode="RGB")


def create_vignette_background(
    width: int,
    height: int,
    center_color: str,
    edge_color: str,
    strength: float = 0.3,
) -> Image.Image:
    """Create a vignette background effect using NumPy vectorization.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        center_color: Hex color at center.
        edge_color: Hex color at edges.
        strength: Vignette strength (0.0 to 1.0).

    Returns:
        PIL Image with vignette effect.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    center_rgb = np.array(hex_to_rgb(center_color), dtype=np.float32)
    edge_rgb = np.array(hex_to_rgb(edge_color), dtype=np.float32)

    center_x = width / 2
    center_y = height / 2
    max_dist_x = center_x
    max_dist_y = center_y

    # Get cached coordinate grids (memory optimization for 4K)
    y_coords, x_coords = _get_coord_grids(width, height)

    # Elliptical distance (normalized)
    dx = (x_coords - center_x) / max_dist_x
    dy = (y_coords - center_y) / max_dist_y
    dist = np.sqrt(dx**2 + dy**2)

    # Apply vignette curve (vectorized)
    # Fade starts around 0.5 of the radius
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


def create_soft_gradient(
    width: int,
    height: int,
    colors: list[str],
    blur_radius: int = 50,
) -> Image.Image:
    """Create a soft gradient with blurred edges.

    This creates a smoother, more diffuse gradient effect.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        colors: List of hex color strings.
        blur_radius: Blur amount for softening.

    Returns:
        PIL Image with soft gradient.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    # Create base gradient
    base = create_gradient_background(width, height, colors)

    # Apply subtle blur for softness
    if blur_radius > 0:
        base = base.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    return base


def create_background_for_style(
    width: int,
    height: int,
    background_type: str,
    colors: list[str],
    angle: int = 135,
) -> Image.Image:
    """Create background based on style settings.

    Args:
        width: Image width.
        height: Image height.
        background_type: Type from BackgroundType enum.
        colors: List of color hex strings.
        angle: Gradient angle (for linear gradients).

    Returns:
        PIL Image with background.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for background generation")

    if len(colors) < 2:
        # Single color - use solid
        color = hex_to_rgb(colors[0]) if colors else (255, 255, 255)
        return Image.new("RGB", (width, height), color)

    if background_type in ("solid_gradient", BackgroundType.SOLID_GRADIENT.value):
        return create_gradient_background(width, height, colors, angle)

    elif background_type in ("soft_gradient", BackgroundType.SOFT_GRADIENT.value):
        return create_soft_gradient(width, height, colors, blur_radius=30)

    elif background_type in ("radial_gradient", BackgroundType.RADIAL_GRADIENT.value):
        return create_radial_gradient(width, height, colors[0], colors[1])

    elif background_type in ("vignette", BackgroundType.VIGNETTE.value):
        # Vignette should fade to WHITE at edges (not dark) for a bright, clean look
        # Use the center color as the main background, fade edges to white
        return create_vignette_background(width, height, colors[0], "#FFFFFF", strength=0.3)

    # Default to soft gradient
    return create_soft_gradient(width, height, colors)


def create_background_array(
    width: int,
    height: int,
    background_type: str,
    colors: list[str],
    angle: int = 135,
) -> np.ndarray:
    """Create background as numpy array for video processing.

    Args:
        width: Image width.
        height: Image height.
        background_type: Type from BackgroundType enum.
        colors: List of color hex strings.
        angle: Gradient angle.

    Returns:
        Numpy array of shape (height, width, 3) with RGB values.
    """
    image = create_background_for_style(width, height, background_type, colors, angle)
    return np.array(image)
