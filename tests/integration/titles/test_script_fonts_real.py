"""Titles in every installed script draw real letters, shaped and in reading order.

Runs `titles fonts --install` for real (about 43 MB, cached between runs under
~/.cache/immich-memories-tests/noto), so it lives with the local integration
tests rather than in CI's unit tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from immich_memories.titles.font_chain import (
    raqm_available,
    text_runs,
    title_font,
    uncovered_letters,
)
from immich_memories.titles.fonts import bundled_font_path
from immich_memories.titles.script_fonts import install_script_fonts

pytestmark = pytest.mark.integration

SCRIPTS = {
    "chinese": "北京之夏",
    "japanese": "東京の夏",
    "korean": "서울의 여름",
    "arabic": "القاهرة، صيف ٢٠٢٤",
    "hebrew": "ירושלים 2024",
    "devanagari": "नई दिल्ली",
    "bengali": "ঢাকা",
    "tamil": "சென்னை",
    "thai": "กรุงเทพ",
    "armenian": "Երևան",
    "georgian": "თბილისი",
    "ethiopic": "አዲስ አበባ",
}
_CACHE = Path.home() / ".cache" / "immich-memories-tests" / "noto"


@pytest.fixture(scope="module", autouse=True)
def _installed_fonts() -> None:
    install_script_fonts(_CACHE)


@pytest.fixture(autouse=True)
def _use_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(_CACHE))


def _montserrat() -> Path:
    path = bundled_font_path("Montserrat", "Bold")
    assert path is not None
    return path


def _mask(font: ImageFont.FreeTypeFont, text: str) -> np.ndarray:
    image = Image.new("L", (1600, 160))
    ImageDraw.Draw(image).text((10, 10), text, font=font, fill=255)
    return np.asarray(image)


@pytest.mark.parametrize("text", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_every_letter_has_a_face(text: str) -> None:
    assert uncovered_letters(text, _montserrat(), bold=True) == ""


@pytest.mark.parametrize("text", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_the_title_draws_ink_and_no_boxes(text: str) -> None:
    font = title_font(_montserrat(), 64, bold=True)

    drawn = _mask(font, text)
    boxed = _mask(font, "" * len(text.replace(" ", "")))

    assert drawn.sum() > 0
    assert not np.array_equal(drawn, boxed)


@pytest.mark.skipif(not raqm_available(), reason="Pillow without Raqm cannot shape")
def test_arabic_letters_join() -> None:
    font = title_font(_montserrat(), 64, bold=True)
    # A zero-width non-joiner between letters forces their isolated forms.
    isolated = "‌".join("القاهرة")

    assert not np.array_equal(_mask(font, "القاهرة"), _mask(font, isolated))


def _ink_blocks(mask: np.ndarray) -> list[int]:
    """Widths of the column stretches that hold ink, left to right."""
    inked = mask.any(axis=0)
    widths, run = [], 0
    for column in inked:
        if column:
            run += 1
        elif run:
            widths.append(run)
            run = 0
    return [*widths, run] if run else widths


@pytest.mark.skipif(not raqm_available(), reason="Pillow without Raqm cannot order")
def test_a_hebrew_word_starts_at_the_right() -> None:
    font = title_font(_montserrat(), 96, bold=True)
    # Yod is Hebrew's narrowest letter and final mem one of its widest: the
    # word begins with yod and ends with mem, so yod must be the right-most block.
    yod = _ink_blocks(_mask(font, "\u05d9"))[0]
    mem = _ink_blocks(_mask(font, "\u05dd"))[0]

    blocks = _ink_blocks(_mask(font, "\u05d9\u05e8\u05d5\u05e9\u05dc\u05d9\u05dd"))

    assert abs(blocks[-1] - yod) <= 1
    assert abs(blocks[0] - mem) <= 1
    assert text_runs("\u05d9\u05e8\u05d5\u05e9\u05dc\u05d9\u05dd", _montserrat())[0].rtl


def test_a_kernel_title_in_arabic_is_not_boxes() -> None:
    from immich_memories.titles.kernel_text import TitleTextRenderer
    from immich_memories.titles.renderer_kernels import KernelTitleConfig

    config = KernelTitleConfig(
        width=1920, height=1080, font_family="Montserrat", use_sdf_text=False
    )
    # WHY None buffers: planning rasterises with PIL on the host; nothing is uploaded.
    renderer = TitleTextRenderer(config, cast(Any, None))

    arabic = renderer.plan_text_layers("القاهرة", None).title_layer
    boxed = renderer.plan_text_layers("" * 7, None).title_layer

    assert not np.array_equal(arabic, boxed)


@pytest.mark.parametrize("locale", ["ja", "zh-Hans", "ko"])
def test_a_cjk_film_title_has_every_letter(locale: str) -> None:
    from datetime import date

    from immich_memories.titles._trip_titles import _get_duration_label, _get_time_label
    from immich_memories.titles.text_builder import SelectionType, generate_title

    otd = generate_title(SelectionType.ON_THIS_DAY, start_date=date(2020, 7, 14), locale=locale)
    trip = f"{_get_duration_label(10, locale)} {_get_time_label(date(2024, 6, 1), date(2024, 8, 1), locale)}"
    text = f"{otd.main_title} {otd.subtitle} {trip}"

    assert uncovered_letters(text, _montserrat(), bold=True) == ""
    font = title_font(_montserrat(), 64, bold=True)
    assert not np.array_equal(_mask(font, text), _mask(font, "" * len(text)))
