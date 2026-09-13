"""The title kernels must keep rendering the frames Taichi rendered.

The golden sheet in this directory was produced by Taichi 1.7.4, the library the
renderer ran on until #558. Quadrants reproduced it byte for byte at this size,
and at 1080p differed by one pixel of 1/255 on two of forty-five frames. That
parity is the whole argument for the swap, so it is pinned here rather than left
in a PR description: any future kernel change that moves the picture fails.

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

# A rounding boundary in the gradient's uint8 quantisation is tolerable; a
# different image is not. Measured Taichi vs Quadrants: identical at this size,
# max 1/255 on 1 pixel of 2 of the 45 frames at 1080p.
MAX_ABS_DIFF = 2
MEAN_ABS_DIFF = 0.5

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
        f"{case_key(style, mood)}: {difference.max()}/255 worst pixel, "
        f"{int((difference.any(axis=2)).sum())} pixels differ"
    )
    assert difference.mean() <= MEAN_ABS_DIFF, (
        f"{case_key(style, mood)}: mean {difference.mean():.4f}/255"
    )


def test_the_matrix_is_not_quietly_rendering_the_same_frame_forty_five_times(
    rendered_sheet: np.ndarray,
) -> None:
    """A harness that rendered one image 45 times would pass every case above."""
    distinct = {_cell(rendered_sheet, index).tobytes() for index in range(len(CASES))}

    assert len(distinct) > 1, "every cell of the style x mood matrix rendered identically"
