"""Generate real GPU-rendered trip map opening videos via Taichi."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from immich_memories.titles.map_renderer import render_trip_map_array
from immich_memories.titles.renderer_taichi import (
    TaichiTitleConfig,
    init_taichi,
)
from immich_memories.titles.taichi_video import create_title_video_taichi

OUTPUT_DIR = Path(__file__).parent.parent / "demo_output" / "videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SPAIN = {
    "locations": [
        (41.3874, 2.1686),
        (39.4699, -0.3763),
        (37.3891, -5.9845),
        (36.7213, -4.4214),
        (40.4168, -3.7038),
    ],
    "names": ["Barcelona", "Valencia", "Seville", "Malaga", "Madrid"],
}

# Darken map background so white text pops
MAP_DIMMING = 0.55


def _map_config(arr: np.ndarray, width: int, height: int) -> TaichiTitleConfig:
    """Create a TaichiTitleConfig for map videos with dimmed background."""
    dimmed = arr * MAP_DIMMING
    # Same absolute font size in portrait and landscape
    title_ratio = 0.135 * min(width, height) / height
    return TaichiTitleConfig(
        width=width,
        height=height,
        fps=30.0,
        duration=5.0,
        background_image=dimmed,
        text_color="#FFFFFF",
        title_size_ratio=title_ratio,
        subtitle_size_ratio=0.0,
        font_family="Montserrat",
        use_sdf_text=False,  # PIL text = pixel-sharp, no SDF blur
        enable_shadow=True,
        shadow_opacity=0.5,
        shadow_offset_ratio=0.004,
        blur_radius=0,  # No blur — map must stay sharp
        enable_bokeh=False,
        enable_noise=False,
        gradient_rotation=0.0,
        color_pulse_amount=0.0,
        vignette_strength=0.15,  # Slight edge darkening
        vignette_pulse=0.0,
    )


def main():
    backend = init_taichi()
    print(f"Taichi backend: {backend}")

    # Landscape satellite
    print("\n[1] Landscape satellite map video (1920x1080)...")
    arr = render_trip_map_array(
        SPAIN["locations"],
        width=1920,
        height=1080,
        location_names=SPAIN["names"],
        map_style="satellite",
    )
    config = _map_config(arr, 1920, 1080)
    out = OUTPUT_DIR / "trip_map_landscape.mp4"
    create_title_video_taichi(
        "TWO WEEKS IN SPAIN, SUMMER 2025", None, out, config, fade_from_white=True
    )
    print(f"  -> {out} ({out.stat().st_size / 1024:.0f} KB)")

    # Portrait satellite
    print("\n[2] Portrait satellite map video (1080x1920)...")
    arr_p = render_trip_map_array(
        SPAIN["locations"],
        width=1080,
        height=1920,
        location_names=SPAIN["names"],
        map_style="satellite",
    )
    config_p = _map_config(arr_p, 1080, 1920)
    out_p = OUTPUT_DIR / "trip_map_portrait.mp4"
    create_title_video_taichi(
        "TWO WEEKS IN SPAIN, SUMMER 2025", None, out_p, config_p, fade_from_white=True
    )
    print(f"  -> {out_p} ({out_p.stat().st_size / 1024:.0f} KB)")

    print(f"\nAll videos saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
