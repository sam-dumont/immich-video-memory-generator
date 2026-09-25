"""Where a title breaks into lines: inside the frame, at a sensible place, in few lines."""

from __future__ import annotations

from PIL import ImageFont

from immich_memories.titles.line_breaking import wrap_lines


def _font(size: int = 40) -> ImageFont.FreeTypeFont:
    # WHY Pillow's bundled face: line widths must not depend on the machine's fonts.
    return ImageFont.load_default(size=size)


def _width(text: str, font) -> float:
    left, _, right, _ = font.getbbox(text)
    return right - left


def test_a_title_with_no_spaces_breaks_between_its_characters_to_stay_inside():
    font = _font()
    title = "我们一家人在云南大理和丽江的夏天旅行回忆"
    limit = _width(title, font) / 2.5

    lines = wrap_lines(title, font, limit)

    assert "".join(lines) == title
    assert all(_width(line, font) <= limit for line in lines)


def test_a_separator_never_opens_a_line_and_a_year_stays_with_its_season():
    font = _font()
    title = "CANARY ISLANDS · TWO WEEKS SUMMER 2025"
    for limit in range(120, int(_width(title, font)), 20):
        lines = wrap_lines(title, font, limit)
        assert not any(line.split()[0] in {"·", "2025"} for line in lines), (limit, lines)


def test_the_lines_are_balanced_rather_than_leaving_one_word_behind():
    font = _font()
    title = "ZWEI WOCHEN IN DEN NIEDERLANDEN SOMMER"
    limit = _width("ZWEI WOCHEN IN DEN NIEDERLANDEN", font) + 5

    lines = wrap_lines(title, font, limit)

    assert len(lines) == 2
    assert min(len(line.split()) for line in lines) >= 2


def test_a_cjk_word_that_fits_on_a_line_is_not_cut():
    font = _font()
    title = "プーリア州, イタリア · 2週間, 2025年の夏"
    limit = _width("プーリア州, イタリア · 2週間,", font) + 5

    lines = wrap_lines(title, font, limit)

    assert all("イタリア" in line or "イ" not in line for line in lines)
    assert all("2週間" in line or "週" not in line for line in lines)
