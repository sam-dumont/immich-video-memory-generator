"""The Quadrants backend must render the same title frames Taichi does.

Taichi 1.7.4 is the last release its upstream will make (#558). Quadrants is a
maintained fork with the same kernel API, and the only thing that makes it a
safe swap is that the pixels come out the same — so this renders one frame for
each of the five preset styles under each of the nine moods on both libraries
and compares them.

The two libraries cannot share a process: each links its own copy of LLVM and
registers the same options with it, so the second import aborts the interpreter.
Each backend therefore renders in its own child, driven by render_title_frames.py.

Run: make test-integration-titles
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.integration.titles.render_title_frames import CASES, case_key

# A rounding boundary in the gradient's uint8 quantisation is tolerable; a
# different image is not. Measured on this matrix: max 1/255 on 1 pixel of 2 of
# the 45 frames, mean 0.0000013/255.
MAX_ABS_DIFF = 2
MEAN_ABS_DIFF = 0.5

_RENDER_PROGRAM = Path(__file__).with_name("render_title_frames.py")
_RENDER_TIMEOUT_SECONDS = 300

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("taichi") is None,
        reason="Taichi not installed",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("quadrants") is None,
        reason="Quadrants not installed (pip install 'immich-memories[titles-quadrants]')",
    ),
]


def _render_with(backend: str, work: Path) -> dict[str, np.ndarray]:
    """Run the render program in its own interpreter and read back its archive."""
    archive = work / f"{backend}.npz"
    completed = subprocess.run(  # noqa: S603 — this interpreter, no shell
        [sys.executable, str(_RENDER_PROGRAM), backend, str(archive)],
        capture_output=True,
        text=True,
        timeout=_RENDER_TIMEOUT_SECONDS,
        # A clean HOME so the developer's own config does not pick the backend.
        env={"HOME": str(work / "home"), "PATH": str(Path(sys.executable).parent)},
        check=False,
    )
    assert completed.returncode == 0, f"{backend} render failed:\n{completed.stderr[-2000:]}"
    with np.load(archive) as frames:
        return {key: frames[key] for key in frames.files}


@pytest.fixture(scope="module")
def rendered_frames(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, np.ndarray]]:
    work = tmp_path_factory.mktemp("kernel-parity")
    (work / "home").mkdir()
    return {backend: _render_with(backend, work) for backend in ("taichi", "quadrants")}


@pytest.mark.parametrize(("style", "mood"), CASES, ids=lambda value: value)
def test_quadrants_renders_the_same_title_frame_as_taichi(
    rendered_frames: dict[str, dict[str, np.ndarray]], style: str, mood: str
) -> None:
    key = case_key(style, mood)
    taichi_frame = rendered_frames["taichi"][key].astype(np.int16)
    quadrants_frame = rendered_frames["quadrants"][key].astype(np.int16)

    difference = np.abs(taichi_frame - quadrants_frame)

    assert difference.max() <= MAX_ABS_DIFF, (
        f"{key}: {difference.max()}/255 worst pixel, {int((difference != 0).sum())} pixels differ"
    )
    assert difference.mean() <= MEAN_ABS_DIFF, f"{key}: mean {difference.mean():.4f}/255"


def test_the_matrix_is_not_quietly_rendering_the_same_frame_twice(
    rendered_frames: dict[str, dict[str, np.ndarray]],
) -> None:
    """A harness that renders one image 45 times would pass every case above."""
    frames = rendered_frames["taichi"]
    distinct = {frames[case_key(style, mood)].tobytes() for style, mood in CASES}

    assert len(distinct) > 1, "every cell of the style x mood matrix rendered identically"
