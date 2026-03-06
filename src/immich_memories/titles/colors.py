"""Color extraction and manipulation utilities.

This module provides:
- Dominant color extraction from video clips
- Color brightening and adjustment
- Minimum brightness enforcement
- Color space conversions
"""

from __future__ import annotations

import colorsys
import tempfile
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


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
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert RGB tuple to hex color.

    Args:
        rgb: RGB tuple (r, g, b) with values 0-255.

    Returns:
        Hex color string (e.g., "#FFF5E6").
    """
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def rgb_to_hsl(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert RGB to HSL color space.

    Args:
        rgb: RGB tuple (0-255).

    Returns:
        HSL tuple (h: 0-360, s: 0-100, l: 0-100).
    """
    r, g, b = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255
    hue, lightness, sat = colorsys.rgb_to_hls(r, g, b)
    return (hue * 360, sat * 100, lightness * 100)


def hsl_to_rgb(hsl: tuple[float, float, float]) -> tuple[int, int, int]:
    """Convert HSL to RGB color space.

    Args:
        hsl: HSL tuple (h: 0-360, s: 0-100, l: 0-100).

    Returns:
        RGB tuple (0-255).
    """
    hue, sat, lightness = hsl[0] / 360, hsl[1] / 100, hsl[2] / 100
    r, g, b = colorsys.hls_to_rgb(hue, lightness, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


def get_brightness(rgb: tuple[int, int, int]) -> float:
    """Calculate perceived brightness of a color.

    Uses the relative luminance formula for human perception.

    Args:
        rgb: RGB tuple (0-255).

    Returns:
        Brightness value (0-255).
    """
    # Perceived brightness formula
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def brighten_color(rgb: tuple[int, int, int], factor: float = 1.5) -> tuple[int, int, int]:
    """Brighten a color while maintaining hue.

    Args:
        rgb: RGB tuple (0-255).
        factor: Brightening factor (1.0 = no change, >1 = brighter).

    Returns:
        Brightened RGB tuple.
    """
    # Convert to HSL
    hue, sat, lightness = rgb_to_hsl(rgb)

    # Increase lightness
    new_lightness = min(95, lightness * factor)  # Cap at 95 to avoid pure white

    # Convert back
    return hsl_to_rgb((hue, sat, new_lightness))


def ensure_minimum_brightness(
    rgb: tuple[int, int, int],
    min_brightness: int = 100,
) -> tuple[int, int, int]:
    """Ensure a color has minimum brightness.

    Args:
        rgb: RGB tuple (0-255).
        min_brightness: Minimum brightness threshold.

    Returns:
        Adjusted RGB tuple meeting minimum brightness.
    """
    current = get_brightness(rgb)

    if current >= min_brightness:
        return rgb

    # Calculate required brightening factor
    factor = min_brightness / max(current, 1)
    factor = min(factor, 3.0)  # Cap factor to avoid over-brightening

    return brighten_color(rgb, factor)


def quantize_colors(
    colors: list[tuple[int, int, int]],
    num_clusters: int = 8,
) -> list[tuple[int, int, int]]:
    """Reduce colors to a smaller palette using simple binning.

    Args:
        colors: List of RGB tuples.
        num_clusters: Target number of color clusters.

    Returns:
        Reduced list of representative colors.
    """
    if not colors:
        return []

    # Simple binning approach (divide color space)
    bin_size = 256 // int(num_clusters ** (1/3) + 1)

    def bin_color(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        return (
            (rgb[0] // bin_size) * bin_size + bin_size // 2,
            (rgb[1] // bin_size) * bin_size + bin_size // 2,
            (rgb[2] // bin_size) * bin_size + bin_size // 2,
        )

    # Bin all colors and count
    binned = Counter(bin_color(c) for c in colors)

    # Return most common
    return [color for color, _ in binned.most_common(num_clusters)]


def extract_colors_from_image(
    image: Image.Image,
    num_colors: int = 5,
    quality: int = 10,
) -> list[tuple[int, int, int]]:
    """Extract dominant colors from an image.

    Args:
        image: PIL Image.
        num_colors: Number of colors to extract.
        quality: Sampling quality (1 = every pixel, higher = skip pixels).

    Returns:
        List of RGB tuples sorted by frequency.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for color extraction")

    # Resize for speed
    img = image.copy()
    img.thumbnail((150, 150))

    # Convert to RGB if necessary
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Sample pixels
    pixels = list(img.getdata())

    if quality > 1:
        pixels = pixels[::quality]

    # Quantize and get top colors
    return quantize_colors(pixels, num_colors)


def extract_dominant_color(
    video_paths: list[Path],
    sample_count: int = 5,
    exclude_dark: bool = True,
    dark_threshold: int = 50,
) -> tuple[int, int, int]:
    """Extract the dominant color from video clips.

    Samples keyframes from multiple clips and finds the most common color.

    Args:
        video_paths: List of video file paths to sample from.
        sample_count: Number of frames to sample per clip.
        exclude_dark: If True, avoid returning very dark colors.
        dark_threshold: RGB threshold for "dark" classification.

    Returns:
        RGB tuple of dominant color.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for color extraction")

    all_colors: list[tuple[int, int, int]] = []

    for video_path in video_paths[:5]:  # Sample from first 5 clips max
        try:
            frames = extract_keyframes_from_video(video_path, sample_count)
            for frame in frames:
                colors = extract_colors_from_image(frame, num_colors=5)
                all_colors.extend(colors)
        except Exception:
            # Skip videos that fail to extract
            continue

    if not all_colors:
        # Fallback to warm neutral
        return (232, 196, 168)  # Warm beige

    # Filter dark colors if requested
    if exclude_dark:
        filtered = [
            c for c in all_colors
            if all(v > dark_threshold for v in c)
        ]
        if filtered:
            all_colors = filtered

    # Find most common color cluster
    quantized = quantize_colors(all_colors, 16)

    if not quantized:
        return (232, 196, 168)

    dominant = quantized[0]

    # Ensure minimum brightness
    return ensure_minimum_brightness(dominant, min_brightness=100)


def extract_keyframes_from_video(
    video_path: Path,
    count: int = 5,
) -> list[Image.Image]:
    """Extract keyframes from a video file.

    Args:
        video_path: Path to video file.
        count: Number of frames to extract.

    Returns:
        List of PIL Images.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for keyframe extraction")

    try:
        import shutil
        import subprocess
    except ImportError:
        return []

    # Check if ffmpeg is available
    if not shutil.which("ffmpeg"):
        return []

    frames = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        try:
            # Get video duration first
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
            result = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            duration = float(result.stdout.strip() or "10")
        except Exception:
            duration = 10.0

        # Calculate timestamps for even distribution
        interval = duration / (count + 1)

        for i in range(1, count + 1):
            timestamp = i * interval
            frame_path = tmpdir_path / f"frame_{i:03d}.jpg"

            try:
                # Downsample to 320px width for color extraction
                # This reduces memory from ~32MB/frame to ~0.3MB/frame for 4K source
                extract_cmd = [
                    "ffmpeg",
                    "-ss", str(timestamp),
                    "-i", str(video_path),
                    "-vf", "scale=320:-1",  # Downsample - color analysis doesn't need full res
                    "-frames:v", "1",
                    "-y",
                    str(frame_path),
                ]
                subprocess.run(
                    extract_cmd,
                    capture_output=True,
                    timeout=10,
                )

                if frame_path.exists():
                    img = Image.open(frame_path)
                    frames.append(img.copy())
                    img.close()

            except Exception:
                continue

    return frames


def create_color_fade_frames(
    start_color: tuple[int, int, int],
    end_color: tuple[int, int, int],
    frame_count: int,
    width: int,
    height: int,
) -> list[Image.Image]:
    """Create a series of frames fading from one color to another.

    Args:
        start_color: Starting RGB color.
        end_color: Ending RGB color.
        frame_count: Number of frames in the fade.
        width: Frame width.
        height: Frame height.

    Returns:
        List of PIL Images for the fade transition.
    """
    if not HAS_PIL:
        raise ImportError("PIL/Pillow is required for color fade")

    frames = []

    for i in range(frame_count):
        t = i / max(frame_count - 1, 1)

        # Interpolate color
        color = tuple(
            int(start_color[j] + (end_color[j] - start_color[j]) * t)
            for j in range(3)
        )

        frame = Image.new("RGB", (width, height), color)
        frames.append(frame)

    return frames
