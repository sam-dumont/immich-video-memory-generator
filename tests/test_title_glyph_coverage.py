"""A title in Greek draws Greek letters, not boxes (#1101).

The bundled families are Latin subsets. A trip to Crete geocoded in the film's
language came back as a Greek place name, and every title path drew it as a row
of missing-glyph boxes while the French around it stayed readable.

"Boxes" is measured, not eyeballed: a string the face cannot draw rasterises to
exactly what a string of private-use characters does, since both are nothing but
the face's missing-glyph shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from immich_memories.titles.fonts import font_covering
from immich_memories.titles.kernel_text import TitleTextRenderer
from immich_memories.titles.renderer_kernels import KernelTitleConfig
from immich_memories.titles.renderer_pil import render_title_frame
from immich_memories.titles.styles import PRESET_STYLES

GREEK = "Κρήτη"
MIXED = "Deux semaines en Crète · Κρήτη"
# Private-use code points: no face has them, so they draw the missing-glyph shape.
NO_GLYPHS = "" * len(GREEK)

LANDSCAPE = {"width": 1920, "height": 1080}
PORTRAIT = {"width": 1080, "height": 1920}


def _kernel_layer(text: str, size: dict[str, int]) -> np.ndarray:
    config = KernelTitleConfig(**size, font_family="Montserrat", use_sdf_text=False)
    # WHY None buffers: planning rasterises with PIL on the host; nothing is
    # uploaded until a frame is composited, which these tests never do.
    return TitleTextRenderer(config, cast(Any, None)).plan_text_layers(text, None).title_layer


@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT], ids=["landscape", "portrait"])
def test_the_kernel_title_layer_draws_greek_letters(size: dict[str, int]) -> None:
    assert not np.array_equal(_kernel_layer(GREEK, size), _kernel_layer(NO_GLYPHS, size))


def test_a_mixed_latin_and_greek_title_is_drawn_in_one_face_that_has_both() -> None:
    mixed = _kernel_layer(MIXED, LANDSCAPE)
    boxed = _kernel_layer(MIXED.replace(GREEK, NO_GLYPHS), LANDSCAPE)

    assert not np.array_equal(mixed, boxed)


def test_the_pil_title_frame_draws_greek_letters() -> None:
    style = PRESET_STYLES["modern_warm"]

    greek = render_title_frame(GREEK, None, style, 640, 360, 1.0)
    boxed = render_title_frame(NO_GLYPHS, None, style, 640, 360, 1.0)

    assert not np.array_equal(greek, boxed)


def test_the_map_title_font_draws_greek_letters() -> None:
    from immich_memories.titles.map_renderer import _get_font

    font = _get_font(48, bold=True, text=f"Crète, {GREEK}")

    assert font_covering(Path(font.path), GREEK) == str(font.path)


def test_a_face_that_draws_the_text_is_kept() -> None:
    from immich_memories.titles.fonts import bundled_font_path

    montserrat = bundled_font_path("Montserrat", "Bold")
    assert montserrat is not None

    assert font_covering(montserrat, "Deux semaines en Italie") == str(montserrat)


def test_an_sdf_atlas_says_it_cannot_draw_what_it_has_no_glyph_for() -> None:
    """The SDF layout advances a space for an absent glyph; the renderer must know first."""
    from immich_memories.titles.sdf_font import SDFFontAtlas

    atlas = SDFFontAtlas(
        texture=np.zeros((1, 1), dtype=np.uint8),
        glyphs=dict.fromkeys("Crète", cast(Any, None)),
        font_size=48,
        line_height=58,
        ascender=44,
        descender=-12,
    )

    assert atlas.draws("Crète Crète")
    assert not atlas.draws(f"Crète {GREEK}")
