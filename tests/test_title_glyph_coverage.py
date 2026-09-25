"""A title draws the letters of its own language, not boxes (#1101).

The bundled families are Latin subsets. Every letter they lack comes from the
next face of the Noto chain that has it, and Latin text never leaves the
family's own face.

"Boxes" is measured, not eyeballed: a string the face cannot draw rasterises to
exactly what a string of private-use characters does, since both are nothing but
the face's missing-glyph shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from immich_memories.titles.font_chain import text_runs, title_font, uncovered_letters
from immich_memories.titles.fonts import bundled_font_path
from immich_memories.titles.kernel_text import TitleTextRenderer
from immich_memories.titles.renderer_kernels import KernelTitleConfig
from immich_memories.titles.renderer_pil import render_title_frame
from immich_memories.titles.styles import PRESET_STYLES

GREEK = "Κρήτη"
MIXED = "Deux semaines en Crète · Κρήτη"
# Private-use code points: no face has them, so they draw the missing-glyph shape.
NO_GLYPHS = "" * len(GREEK)
BUNDLED_SCRIPTS = {
    "greek": "Ηράκλειο, Ελλάδα",
    "cyrillic": "Москва, лето 2024",
    "vietnamese": "Hà Nội mùa hè",
    "polish": "Łódź i Kraków",
}

LANDSCAPE = {"width": 1920, "height": 1080}
PORTRAIT = {"width": 1080, "height": 1920}


@pytest.fixture(autouse=True)
def _no_installed_script_fonts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: a developer box may have run `titles fonts --install`; CI has not.
    monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(tmp_path / "no-script-fonts"))


def _montserrat() -> Path:
    path = bundled_font_path("Montserrat", "Bold")
    assert path is not None
    return path


def _mask(font: ImageFont.FreeTypeFont, text: str) -> np.ndarray:
    image = Image.new("L", (1600, 120))
    ImageDraw.Draw(image).text((10, 10), text, font=font, fill=255)
    return np.asarray(image)


def _kernel_layer(text: str, size: dict[str, int]) -> np.ndarray:
    config = KernelTitleConfig(**size, font_family="Montserrat", use_sdf_text=False)
    # WHY None buffers: planning rasterises with PIL on the host; nothing is
    # uploaded until a frame is composited, which these tests never do.
    return TitleTextRenderer(config, cast(Any, None)).plan_text_layers(text, None).title_layer


@pytest.mark.parametrize("text", ["DEUX SEMAINES EN CRÈTE, ÉTÉ 2024", "A Week in Rome"])
@pytest.mark.parametrize("family", ["Montserrat", "Outfit"])
def test_a_latin_title_is_the_same_pixels_as_its_own_face(text: str, family: str) -> None:
    path = bundled_font_path(family, "Regular")
    assert path is not None

    chained = _mask(title_font(path, 48), text)
    plain = _mask(ImageFont.truetype(str(path), 48), text)

    assert np.array_equal(chained, plain)


@pytest.mark.parametrize("text", BUNDLED_SCRIPTS.values(), ids=BUNDLED_SCRIPTS.keys())
def test_the_bundled_fonts_cover_every_letter(text: str) -> None:
    assert uncovered_letters(text, _montserrat(), bold=True) == ""


@pytest.mark.parametrize("text", BUNDLED_SCRIPTS.values(), ids=BUNDLED_SCRIPTS.keys())
def test_a_bundled_script_draws_ink_and_no_boxes(text: str) -> None:
    font = title_font(_montserrat(), 48, bold=True)

    drawn = _mask(font, text)
    boxed = _mask(font, "" * len(text.replace(" ", "")))

    assert drawn.sum() > 0
    assert not np.array_equal(drawn, boxed)


def test_polish_and_vietnamese_stay_in_montserrat() -> None:
    texts = ("Łódź i Kraków", "Hà Nội")
    faces = {run.face for text in texts for run in text_runs(text, _montserrat())}

    assert all("montserrat" in face for face in faces)


def test_greek_comes_from_noto_and_the_french_stays_montserrat() -> None:
    runs = text_runs(MIXED, _montserrat(), bold=True)

    assert [Path(run.face).parent.name for run in runs] == ["montserrat", "noto-sans"]
    assert runs[1].text == GREEK


def test_a_hebrew_name_after_french_text_sits_on_the_right() -> None:
    runs = text_runs("Crète · ירושלים", _montserrat())

    assert [run.rtl for run in runs] == [False, True]
    assert runs[1].text == "ירושלים"


def test_a_hebrew_title_puts_its_latin_word_on_the_left() -> None:
    runs = text_runs("ירושלים Crète", _montserrat())

    assert [run.text.strip() for run in runs] == ["Crète", "ירושלים"]


def test_a_letter_no_font_has_is_reported() -> None:
    assert uncovered_letters("Crète ", _montserrat()) == ""


@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT], ids=["landscape", "portrait"])
def test_the_kernel_title_layer_draws_greek_letters(size: dict[str, int]) -> None:
    assert not np.array_equal(_kernel_layer(GREEK, size), _kernel_layer(NO_GLYPHS, size))


def test_a_mixed_latin_and_greek_kernel_title_draws_both() -> None:
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

    font = cast(ImageFont.FreeTypeFont, _get_font(48, bold=True))

    assert not np.array_equal(_mask(font, GREEK), _mask(font, NO_GLYPHS))


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


def test_the_shaping_hint_on_a_mac_says_how_pillow_finds_homebrew_fribidi(monkeypatch):
    # A brew-installed FriBiDi is not on dlopen's path on Apple Silicon: without
    # the library path Pillow still has no Raqm, so "brew install" alone is wrong.
    from immich_memories.titles import font_chain

    monkeypatch.setattr(font_chain.sys, "platform", "darwin")
    assert "DYLD_FALLBACK_LIBRARY_PATH" in font_chain.shaping_hint()

    monkeypatch.setattr(font_chain.sys, "platform", "linux")
    assert "libfribidi0" in font_chain.shaping_hint()
    assert "DYLD" not in font_chain.shaping_hint()
