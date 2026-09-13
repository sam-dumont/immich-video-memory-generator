"""Render one title frame per style x mood, as its own program.

    python render_title_frames.py <out.png>

Writes the 45 frames as one lossless sprite sheet, which is the golden the
integration test compares against. Its own program because the kernel library
has to be initialised on a fresh runtime, and because regenerating the golden
is a deliberate act rather than a side effect of running the suite.

Every import is function-local on purpose: the test that drives this module
imports it for the case list alone, and must not pull a C++ runtime into the
pytest process to get it.
"""

from __future__ import annotations

import sys

# The five shipped preset styles and the nine moods that dress them. The cross
# product is a test matrix, not a product path: what varies is the palette, the
# weight and the animation curve, which is what the kernels actually consume.
STYLE_NAMES = (
    "modern_warm",
    "elegant_minimal",
    "vintage_charm",
    "playful_bright",
    "soft_romantic",
)
MOODS = (
    "happy",
    "calm",
    "energetic",
    "nostalgic",
    "romantic",
    "playful",
    "peaceful",
    "exciting",
    "default",
)
CASES = tuple((style, mood) for style in STYLE_NAMES for mood in MOODS)

FRAME_WIDTH, FRAME_HEIGHT, FRAME_FPS = 160, 90, 10.0
FRAME_DURATION = 2.0
# Mid-animation: fade, slide, colour pulse and deblur are all part-way through,
# so a frame here exercises more of the kernel arithmetic than frame 0 does.
FRAME_NUMBER = 9


SHEET_COLUMNS = len(MOODS)


def case_key(style: str, mood: str) -> str:
    """Name of one cell of the matrix."""
    return f"{style}|{mood}"


def sheet_position(index: int) -> tuple[int, int]:
    """Top-left pixel of one cell in the sprite sheet, row-major by style."""
    row, column = divmod(index, SHEET_COLUMNS)
    return column * FRAME_WIDTH, row * FRAME_HEIGHT


def _style_for(style_name: str, mood: str):
    """One preset style wearing one mood's palette, weight and animation."""
    from dataclasses import replace

    from immich_memories.titles.styles import PRESET_STYLES, get_style_for_mood

    mood_style = get_style_for_mood(mood, randomize=False)
    return replace(
        PRESET_STYLES[style_name],
        name=f"{style_name}_{mood}",
        background_colors=mood_style.background_colors,
        text_color=mood_style.text_color,
        accent_color=mood_style.accent_color,
        animation_preset=mood_style.animation_preset,
        font_weight=mood_style.font_weight,
    )


def _config_for(style):
    """The renderer config the title service builds, minus the content backing."""
    from immich_memories.titles.renderer_kernels import KernelTitleConfig

    return KernelTitleConfig(
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        fps=FRAME_FPS,
        duration=FRAME_DURATION,
        bg_color1=style.background_colors[0],
        bg_color2=style.background_colors[-1],
        gradient_angle=float(style.background_angle),
        gradient_type="linear" if style.background_type != "radial" else "radial",
        text_color=style.text_color,
        title_size_ratio=style.title_size_ratio,
        subtitle_size_ratio=style.subtitle_size_ratio * style.title_size_ratio,
        font_family="Montserrat",
        use_sdf_text=False,
        enable_shadow=False,
        blur_radius=8,
        enable_bokeh=True,
        gradient_rotation=10.0,
        color_pulse_amount=0.03,
        vignette_pulse=0.05,
        vignette_strength=0.3,
    )


def render_sheet():
    """Render every case and lay them out as one array, row-major by style."""
    import numpy as np

    from immich_memories.titles.kernels import init_kernels
    from immich_memories.titles.renderer_kernels import KernelTitleRenderer

    if init_kernels() is None:
        raise SystemExit("no kernel backend could be initialised")

    frames = []
    for style_name, mood in CASES:
        renderer = KernelTitleRenderer(_config_for(_style_for(style_name, mood)))
        frames.append(renderer.render_frame(FRAME_NUMBER, "Summer in Lisbon", "July 2024"))
    rows = [
        np.concatenate(frames[start : start + SHEET_COLUMNS], axis=1)
        for start in range(0, len(frames), SHEET_COLUMNS)
    ]
    return np.concatenate(rows, axis=0)


def main(destination: str) -> None:
    """Render the sheet on the CPU backend and write it losslessly."""
    import os

    # The golden is about the kernels, not about a card: on the CPU backend a
    # driver is never the thing that changed.
    os.environ["IMMICH_FORCE_CPU"] = "1"

    from PIL import Image

    Image.fromarray(render_sheet()).save(destination, optimize=True)


if __name__ == "__main__":
    main(sys.argv[1])
