"""Title text is composited where a title goes, and nowhere else.

The golden sheet next door renders no text on purpose: Pillow rasterises glyphs
differently on macOS and on the Linux runner, so pixel-exact text is not a thing
a golden can hold. This covers the same path in the terms that *are* portable --
that compositing the text layer changes the title band and leaves the rest of
the frame untouched -- which is what a wrong offset, a smeared alpha or a
composite that never ran would each break, on any machine.

Run: make test-integration-titles
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.integration.titles.render_title_frames import FRAME_HEIGHT, FRAME_WIDTH
from tests.integration.titles.test_title_frame_goldens import TEXT_COLUMNS, TEXT_ROWS

# White text on a dark card: where a glyph lands, the pixel moves most of the
# way up the range. Measured 220-242/255 across the matrix.
MIN_TEXT_CONTRAST = 100

_CASE = ("modern_warm", "energetic")
_RENDER_TIMEOUT_SECONDS = 300

_PROGRAM = """
import json, os, sys
os.environ["IMMICH_FORCE_CPU"] = "1"
sys.path.insert(0, {root!r})
import numpy as np
from tests.integration.titles.render_title_frames import (
    GOLDEN_SUBTITLE, GOLDEN_TITLE, SAMPLE_SUBTITLE, SAMPLE_TITLE, render_case,
)
from immich_memories.titles.kernels import init_kernels

if init_kernels() is None:
    raise SystemExit("no kernel backend could be initialised")
style, mood = {case!r}
blank = render_case(style, mood, GOLDEN_TITLE, GOLDEN_SUBTITLE).astype(np.int16)
titled = render_case(style, mood, SAMPLE_TITLE, SAMPLE_SUBTITLE).astype(np.int16)
np.save({destination!r}, np.abs(titled - blank))
"""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("quadrants") is None,
        reason="no kernel library wheel for this platform; titles are PIL-rendered",
    ),
]


@pytest.fixture(scope="module")
def text_difference(tmp_path_factory: pytest.TempPathFactory) -> np.ndarray:
    """|with text - without text| for one case, rendered in its own interpreter."""
    work = tmp_path_factory.mktemp("title-text")
    home = work / "home"
    home.mkdir()
    difference = work / "difference.npy"
    program = work / "render.py"
    root = str(Path(__file__).resolve().parents[3])
    program.write_text(_PROGRAM.format(root=root, case=_CASE, destination=str(difference)))
    completed = subprocess.run(  # noqa: S603 — this interpreter, no shell
        [sys.executable, str(program)],
        capture_output=True,
        text=True,
        timeout=_RENDER_TIMEOUT_SECONDS,
        env={"HOME": str(home), "PATH": str(Path(sys.executable).parent)},
        check=False,
    )
    assert completed.returncode == 0, f"render failed:\n{completed.stderr[-2000:]}"
    return np.load(difference)


def test_the_text_layer_reaches_the_frame(text_difference: np.ndarray) -> None:
    """A composite that never ran, or ran on a transparent layer, changes nothing."""
    assert text_difference.max() >= MIN_TEXT_CONTRAST, (
        f"title text moved no pixel by more than {text_difference.max()}/255; "
        f"expected white glyphs on a dark card, so at least {MIN_TEXT_CONTRAST}"
    )


def test_text_lands_in_the_title_band(text_difference: np.ndarray) -> None:
    """Text belongs in the middle of the card, and the composite touches nothing else.

    Deliberately a band and not a bounding box: which pixels a glyph covers is
    the font rasteriser's business and differs between platforms. Which part of
    the frame the kernel is allowed to write is not.
    """
    rows, columns = np.nonzero(text_difference.any(axis=2))
    row_band = (TEXT_ROWS[0] * FRAME_HEIGHT, TEXT_ROWS[1] * FRAME_HEIGHT)
    column_band = (TEXT_COLUMNS[0] * FRAME_WIDTH, TEXT_COLUMNS[1] * FRAME_WIDTH)

    assert row_band[0] <= rows.min() and rows.max() <= row_band[1], (
        f"text touched rows {rows.min()}-{rows.max()}, outside {row_band}"
    )
    assert column_band[0] <= columns.min() and columns.max() <= column_band[1], (
        f"text touched columns {columns.min()}-{columns.max()}, outside {column_band}"
    )


def test_the_rest_of_the_card_is_untouched(text_difference: np.ndarray) -> None:
    """The background above and below the title must be identical with or without it."""
    above = text_difference[: int(TEXT_ROWS[0] * FRAME_HEIGHT)]
    below = text_difference[int(TEXT_ROWS[1] * FRAME_HEIGHT) :]

    assert above.max() == 0, f"compositing text changed {int(above.any(axis=2).sum())} px above it"
    assert below.max() == 0, f"compositing text changed {int(below.any(axis=2).sum())} px below it"
