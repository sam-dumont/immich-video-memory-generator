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

from immich_memories.processing.clip_caption import ClipCaption
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


def _first_frame(clip: Path, width: int = 640, height: int = 360, **kwargs) -> np.ndarray:
    decoder = FrameDecoder(clip, width=width, height=height, fps=10, **kwargs)
    return next(iter(decoder)).copy()


def test_the_caption_is_actually_drawn(grey_clip: Path) -> None:
    plain = _first_frame(grey_clip)
    captioned = _first_frame(grey_clip, caption=ClipCaption(date="5 Jan 2026"))

    assert not np.array_equal(plain, captioned), "overlay changed nothing"


def test_the_caption_lands_in_the_bottom_corner(grey_clip: Path) -> None:
    """Bottom-right, inset — not centred over the subject's face."""
    plain = _first_frame(grey_clip)
    captioned = _first_frame(grey_clip, caption=ClipCaption(date="5 Jan 2026"))

    changed = np.argwhere(np.any(plain != captioned, axis=-1))
    assert changed.size, "overlay changed nothing"
    rows, cols = changed[:, 0], changed[:, 1]

    assert rows.min() > 360 * 0.7, "caption is not in the lower part of the frame"
    assert cols.min() > 640 * 0.5, "caption is not on the right"
    assert rows.max() < 360, "caption runs off the bottom edge"
    assert cols.max() < 640, "caption runs off the right edge"


@pytest.fixture(scope="module")
def grey_portrait(tmp_path_factory) -> Path:
    """A 9:16 clip, the shape Reels/Shorts/Stories actually receive."""
    out = tmp_path_factory.mktemp("overlay9x16") / "grey.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=360x640:rate=10:duration=1",
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


def test_a_vertical_caption_sits_above_the_platform_chrome(grey_portrait: Path) -> None:
    plain = _first_frame(grey_portrait, width=360, height=640)
    captioned = _first_frame(
        grey_portrait, width=360, height=640, caption=ClipCaption(date="5 Jan 2026")
    )

    changed = np.argwhere(np.any(plain != captioned, axis=-1))
    assert changed.size, "overlay changed nothing"

    lowest = changed[:, 0].max()
    chrome_top = 640 * (1 - 0.15)
    assert lowest < chrome_top, (
        f"caption reaches row {lowest}; the platform UI starts around {chrome_top:.0f}"
    )


def test_a_place_with_an_apostrophe_reaches_the_screen(tmp_path: Path) -> None:
    """Measured: drawtext silently drops an ASCII apostrophe however it is
    escaped, so "L'Aquila" rendered as "LAquila". Compared against a
    `textfile=` render, which needs no escaping at all and is therefore the
    ground truth."""
    from immich_memories.processing.clip_caption import ClipCaption, caption_filters

    def caption_filter(text: str, w: int, h: int) -> str:
        (only,) = caption_filters(ClipCaption(place=text), w, h)
        return only

    base = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=gray:size=640x360:rate=1:duration=1",
        "-frames:v",
        "1",
    ]
    escaped = caption_filter("L'Aquila", 640, 360)
    reference = tmp_path / "ref.txt"
    reference.write_text("L’Aquila")
    tail = escaped.split(":fontsize=", 1)[1]

    ours, theirs = tmp_path / "ours.png", tmp_path / "theirs.png"
    subprocess.run([*base, "-vf", escaped, str(ours)], check=True, capture_output=True)
    subprocess.run(
        [*base, "-vf", f"drawtext=textfile={reference}:fontsize={tail}", str(theirs)],
        check=True,
        capture_output=True,
    )

    assert ours.read_bytes() == theirs.read_bytes()
