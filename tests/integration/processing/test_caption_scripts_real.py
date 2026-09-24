"""A caption in any script reaches the frame as letters, not boxes (#1101).

drawtext has one font file and no fallback, so a Greek or Cyrillic place drew
as a row of boxes. Such a caption is now drawn with the title fonts and laid
over the frame. "Boxes" is measured: the frame with the caption must differ
from the frame with the same number of private-use characters, which every
font draws as its missing-glyph shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from immich_memories.processing.clip_caption import ClipCaption, caption_font_path
from immich_memories.processing.streaming_frame_decoder import FrameDecoder
from immich_memories.titles.script_fonts import install_script_fonts, installed_script_fonts

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def grey_clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("captions") / "grey.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return out


def _frame(clip: Path, caption: ClipCaption | None, **kwargs) -> np.ndarray:
    decoder = FrameDecoder(
        clip, width=640, height=360, fps=10, caption=caption, caption_font=caption_font_path(),
        **kwargs,
    )  # fmt: skip
    return next(iter(decoder)).copy()


def _boxes(text: str) -> str:
    return "".join(" " if c == " " else "" for c in text)


def _drawn_differs_from_boxes(clip: Path, place: str) -> None:
    plain = _frame(clip, None)
    drawn = _frame(clip, ClipCaption(place=place))
    boxed = _frame(clip, ClipCaption(place=_boxes(place)))

    changed = np.argwhere(np.any(plain != drawn, axis=-1))
    assert changed.size, "the caption drew nothing"
    assert changed[:, 0].max() < 360 * 0.3, "the place caption left the top of the frame"
    assert changed[:, 1].min() < 640 * 0.2, "the place caption left the left edge"
    assert not np.array_equal(drawn, boxed), "the caption is drawn as missing-glyph boxes"


@pytest.mark.parametrize("place", ["Ηράκλειο, Ελλάδα", "Москва, Россия"], ids=["greek", "cyrillic"])
def test_a_greek_or_cyrillic_place_caption_draws_letters(grey_clip: Path, place: str) -> None:
    _drawn_differs_from_boxes(grey_clip, place)


def test_a_latin_caption_still_goes_through_drawtext(grey_clip: Path) -> None:
    from immich_memories.processing.clip_caption import caption_filters

    filters = caption_filters(
        ClipCaption(place="Nice, France"), 640, 360, font_path=caption_font_path()
    )

    assert len(filters) == 1
    assert filters[0].startswith("drawtext=")


def test_a_greek_date_in_an_hdr_frame_matches_the_drawtext_white(grey_clip: Path) -> None:
    def luma_peak(date: str) -> int:
        frame = _frame(grey_clip, ClipCaption(date=date), pix_fmt="yuv420p10le")
        return int(frame[: 640 * 360].max())

    drawtext_peak = luma_peak("MONDAY 5")
    overlay_peak = luma_peak("ΔΕΥΤΈΡΑ 5")

    # HLG graphics white is 75 % of range (767 of 1023). Measured on FFmpeg 8.1:
    # drawtext 648, the overlay 730; overlay converts the PNG's RGB to YUV itself,
    # drawtext blends its colour in the frame's own space. Both stay under it.
    assert overlay_peak < 767
    assert drawtext_peak < 767


@pytest.fixture
def script_fonts(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = Path.home() / ".cache" / "immich-memories-tests" / "noto"
    monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(cache))
    if not installed_script_fonts(bold=True):
        try:
            install_script_fonts(cache)
        except OSError as error:
            pytest.skip(f"script fonts not installable here: {error}")


@pytest.mark.usefixtures("script_fonts")
@pytest.mark.parametrize("place", ["القاهرة، مصر", "東京, 日本"], ids=["arabic", "japanese"])
def test_an_arabic_or_japanese_place_caption_draws_letters(grey_clip: Path, place: str) -> None:
    _drawn_differs_from_boxes(grey_clip, place)
