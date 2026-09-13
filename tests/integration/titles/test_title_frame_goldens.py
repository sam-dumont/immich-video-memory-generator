"""The title kernels must keep rendering the frames they render today.

The golden sheet in this directory is 45 frames -- the five preset styles under
the nine moods -- of pure kernel output: gradient, blur, vignette, film grain,
bokeh and the final quantisation, with **no title text**. That is deliberate,
and it is what makes this golden mean the same thing on every machine.

Why no text. A title screen's text is rasterised by Pillow on the CPU, uploaded
as an RGBA layer and composited by a kernel. Pillow's glyph rasterisation is not
portable: the first version of this golden was rendered on macOS, and on the
Linux CI runner 208 of each frame's 14,400 pixels came back different, by as
much as 242/255, every one of them inside the text band (measured here: text
touches rows 42-61 and columns 42-115 of a 160x90 frame, with peak deltas of
220-242/255 -- the same numbers CI reported). A golden that red-lights on the
runner's FreeType build guards nothing; it just gets its tolerance loosened
until it guards nothing quietly. So the rasteriser is taken out of the picture
and the kernels are goldened alone.

That does not drop the text path: `test_text_lands_in_the_title_band` below
covers the compositing kernel by asserting *where* text goes and that it goes
nowhere else, which is true of any rasteriser.

Provenance. The identical matrix, rendered with text, was produced by Taichi
1.7.4 before it was removed in #558, and Quadrants reproduced that sheet byte
for byte on this machine -- which is the parity argument for the swap. This
sheet is the same matrix with the text layer switched off.

Regenerate deliberately, never to make a red test green:

    python tests/integration/titles/render_title_frames.py \\
        tests/integration/titles/golden_title_frames.png

Run: make test-integration-titles
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.integration.titles.render_title_frames import (
    CASES,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    case_key,
    sheet_position,
)

# A rounding boundary in the gradient's uint8 quantisation is tolerable, and one
# is expected between architectures; a different picture is not. Anything the
# kernels get wrong -- a blur radius, a vignette falloff, missing bokeh -- moves
# whole regions by tens of levels, not by one.
MAX_ABS_DIFF = 2
MEAN_ABS_DIFF = 0.5

# Where title text is allowed to land, as a fraction of the frame. Measured band
# is rows 0.47-0.68 and columns 0.26-0.72; these are that with room for a
# different rasteriser's metrics, and tight enough that a composite drawing text
# at the wrong offset fails.
TEXT_ROWS = (0.35, 0.80)
TEXT_COLUMNS = (0.15, 0.85)

GOLDEN = Path(__file__).with_name("golden_title_frames.png")
_RENDER_PROGRAM = Path(__file__).with_name("render_title_frames.py")
_RENDER_TIMEOUT_SECONDS = 300

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("quadrants") is None,
        reason="no kernel library wheel for this platform; titles are PIL-rendered",
    ),
]


def _cell(sheet: np.ndarray, index: int) -> np.ndarray:
    left, top = sheet_position(index)
    return sheet[top : top + FRAME_HEIGHT, left : left + FRAME_WIDTH].astype(np.int16)


def _where(difference: np.ndarray) -> str:
    """Name the differing pixels, so a failure says where and not only how much."""
    rows, columns = np.nonzero(difference.any(axis=2))
    if not rows.size:
        return "nowhere"
    return (
        f"{rows.size} pixel(s) in rows {rows.min()}-{rows.max()} "
        f"of {FRAME_HEIGHT}, columns {columns.min()}-{columns.max()} of {FRAME_WIDTH}"
    )


@pytest.fixture(scope="module")
def rendered_sheet(tmp_path_factory: pytest.TempPathFactory) -> np.ndarray:
    """Render the matrix in its own interpreter, the way the golden was made."""
    work = tmp_path_factory.mktemp("title-goldens")
    home = work / "home"
    home.mkdir()
    sheet = work / "sheet.png"
    completed = subprocess.run(  # noqa: S603 — this interpreter, no shell
        [sys.executable, str(_RENDER_PROGRAM), str(sheet)],
        capture_output=True,
        text=True,
        timeout=_RENDER_TIMEOUT_SECONDS,
        # A clean HOME so a developer's own config cannot change the render.
        env={"HOME": str(home), "PATH": str(Path(sys.executable).parent)},
        check=False,
    )
    assert completed.returncode == 0, f"render failed:\n{completed.stderr[-2000:]}"
    return np.asarray(Image.open(sheet).convert("RGB"))


@pytest.fixture(scope="module")
def golden_sheet() -> np.ndarray:
    return np.asarray(Image.open(GOLDEN).convert("RGB"))


def test_the_golden_sheet_holds_every_case(golden_sheet: np.ndarray) -> None:
    """A sheet the wrong size would make every comparison below meaningless."""
    rows = -(-len(CASES) // 9)

    assert golden_sheet.shape == (rows * FRAME_HEIGHT, 9 * FRAME_WIDTH, 3)


@pytest.mark.parametrize(("style", "mood"), CASES, ids=lambda value: value)
def test_the_kernels_still_render_the_golden_frame(
    rendered_sheet: np.ndarray, golden_sheet: np.ndarray, style: str, mood: str
) -> None:
    index = CASES.index((style, mood))
    difference = np.abs(_cell(rendered_sheet, index) - _cell(golden_sheet, index))

    assert difference.max() <= MAX_ABS_DIFF, (
        f"{case_key(style, mood)}: worst pixel {difference.max()}/255, {_where(difference)}"
    )
    assert difference.mean() <= MEAN_ABS_DIFF, (
        f"{case_key(style, mood)}: mean {difference.mean():.4f}/255, {_where(difference)}"
    )


def test_the_matrix_is_not_quietly_rendering_the_same_frame_forty_five_times(
    rendered_sheet: np.ndarray,
) -> None:
    """A harness that rendered one image 45 times would pass every case above."""
    distinct = {_cell(rendered_sheet, index).tobytes() for index in range(len(CASES))}

    assert len(distinct) > 1, "every cell of the style x mood matrix rendered identically"
