"""The date overlay renders actual pixels (#313).

The unit tests assert the filter string; only FFmpeg can say whether the text
is really drawn. That distinction is the whole point here: the option was
plumbed end to end and drew nothing for its entire life.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from immich_memories.processing.streaming_assembler import FrameDecoder

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def grey_clip(tmp_path_factory) -> Path:
    """A flat mid-grey clip, so any pixel variation comes from the caption."""
    out = tmp_path_factory.mktemp("overlay") / "grey.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=640x360:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _first_frame(clip: Path, **kwargs) -> np.ndarray:
    decoder = FrameDecoder(clip, width=640, height=360, fps=10, **kwargs)
    return next(iter(decoder)).copy()


def test_the_caption_is_actually_drawn(grey_clip: Path) -> None:
    plain = _first_frame(grey_clip)
    captioned = _first_frame(grey_clip, overlay_text="5 Jan 2026")

    assert not np.array_equal(plain, captioned), "overlay changed nothing"


def test_the_caption_lands_in_the_bottom_corner(grey_clip: Path) -> None:
    """Bottom-right, inset — not centred over the subject's face."""
    plain = _first_frame(grey_clip)
    captioned = _first_frame(grey_clip, overlay_text="5 Jan 2026")

    changed = np.argwhere(np.any(plain != captioned, axis=-1))
    assert changed.size, "overlay changed nothing"
    rows, cols = changed[:, 0], changed[:, 1]

    assert rows.min() > 360 * 0.7, "caption is not in the lower part of the frame"
    assert cols.min() > 640 * 0.5, "caption is not on the right"
    assert rows.max() < 360, "caption runs off the bottom edge"
    assert cols.max() < 640, "caption runs off the right edge"
