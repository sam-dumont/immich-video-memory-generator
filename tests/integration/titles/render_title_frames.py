"""Render one title frame per style x mood with one kernel library, as its own program.

Taichi and Quadrants cannot share a process: they each link their own copy of
LLVM and register the same options with it, so importing the second one aborts
the interpreter outright. The frame-diff harness therefore runs this file twice,
once per backend, and compares the two archives it writes.

    python render_title_frames.py <taichi|quadrants> <out.npz>

Every import is function-local on purpose — the module has to be importable by
the test that drives it without dragging a kernel library into the pytest
process along with it.
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


def case_key(style: str, mood: str) -> str:
    """Archive key for one cell of the matrix."""
    return f"{style}|{mood}"


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


def render_every_case() -> dict[str, object]:
    """Render the whole matrix in this process, on this process's kernel library."""
    from immich_memories.titles.renderer_kernels import KernelTitleRenderer
    from immich_memories.titles.kernels import init_kernels

    if init_kernels() is None:
        raise SystemExit("no kernel backend could be initialised")

    frames: dict[str, object] = {}
    for style_name, mood in CASES:
        renderer = KernelTitleRenderer(_config_for(_style_for(style_name, mood)))
        frames[case_key(style_name, mood)] = renderer.render_frame(
            FRAME_NUMBER, "Summer in Lisbon", "July 2024"
        )
    return frames


def main(backend: str, destination: str) -> None:
    """Pin the kernel library for this process, render, and write the archive."""
    import os

    os.environ["IMMICH_MEMORIES_TITLE_KERNELS"] = backend
    # The comparison is between two libraries, not two GPUs: keep both on the
    # CPU backend so a driver is never the thing that differs.
    os.environ["IMMICH_FORCE_CPU"] = "1"

    import numpy as np

    from immich_memories.titles.gpu_kernel_backend import KERNEL_BACKEND

    if backend != KERNEL_BACKEND:
        raise SystemExit(f"asked for {backend}, loaded {KERNEL_BACKEND}")

    np.savez_compressed(destination, **render_every_case())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
