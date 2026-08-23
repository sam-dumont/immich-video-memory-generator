"""A slow-mo title background must not animate the debris of a failed decode.

#517: `SlowmoBackgroundReader` streams frames as FFmpeg emits them, so a clip
whose tail is unreadable still leaves a handful of frames behind. The streaming
rewrite stopped checking the exit status, so those frames were kept and the 3.5s
ease spread them over the whole title — a nearly frozen, smearing background
where the static frame used to take over.

Run: make test-integration-titles
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from immich_memories.titles.content_background import SlowmoBackgroundReader

pytestmark = [pytest.mark.integration]

# The reader does not scale — it reshapes the raw pipe to these dimensions,
# so the source clip has to be authored at exactly this size.
_W, _H, _FPS = 320, 180, 30
_FRAME_BYTES = _W * _H * 3


def _make_clip(path: Path) -> Path:
    # WHY faststart: moov lands ahead of mdat, so corrupting the tail damages the
    # picture data while leaving the file openable — a decode that starts,
    # delivers frames, and only then fails.
    cmd = [
        "ffmpeg", "-v", "error",
        "-f", "lavfi",
        "-i", f"testsrc2=size={_W}x{_H}:duration=3:rate={_FPS}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-g", "5",
        "-movflags", "+faststart",
        "-y", str(path),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return path


def _corrupt_tail(source: Path, dest: Path, fraction: float) -> Path:
    data = bytearray(source.read_bytes())
    rng = random.Random(7)
    for i in range(int(len(data) * (1 - fraction)), len(data)):
        data[i] = rng.randrange(256)
    dest.write_bytes(bytes(data))
    return dest


def _decode(clip: Path) -> tuple[int, int]:
    """Exit status and whole-frame count from the pipe the reader itself uses."""
    cmd = [
        "ffmpeg",
        "-ss", "0",
        "-t", "0.5",
        "-i", str(clip),
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-an",
        "pipe:1",
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    return proc.returncode, len(proc.stdout) // _FRAME_BYTES


@pytest.fixture(scope="module")
def intact_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_clip(tmp_path_factory.mktemp("slowmo") / "intact.mp4")


@pytest.fixture(scope="module")
def half_decoded_clip(intact_clip: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clip whose decode exits nonzero *after* emitting usable frames.

    How much corruption produces that depends on the FFmpeg build, so the
    fixture searches for a fraction that does and skips when none is found
    rather than asserting on a number that only holds on one machine.
    """
    out = tmp_path_factory.mktemp("slowmo-corrupt") / "corrupt.mp4"
    for fraction in (0.8, 0.7, 0.6, 0.9):
        _corrupt_tail(intact_clip, out, fraction)
        returncode, frames = _decode(out)
        if returncode != 0 and frames >= 2:
            return out
    pytest.skip("this FFmpeg build never exits nonzero with frames already emitted")


def test_intact_clip_animates(intact_clip: Path) -> None:
    """Positive control: the fallback must be about the failure, not the fixture."""
    reader = SlowmoBackgroundReader(intact_clip, _W, _H, _FPS, title_duration=3.5)

    assert reader.is_active
    assert reader.read_frame() is not None
    reader.close()


def test_failed_decode_falls_back_instead_of_animating_a_sliver(
    half_decoded_clip: Path,
) -> None:
    returncode, frames = _decode(half_decoded_clip)
    assert returncode != 0 and frames >= 2, "fixture no longer reproduces the failure"

    reader = SlowmoBackgroundReader(half_decoded_clip, _W, _H, _FPS, title_duration=3.5)

    assert not reader.is_active, (
        f"kept {frames} frames from a decode that exited {returncode}; "
        "the 3.5s ease would animate a sliver of source"
    )
    assert reader.read_frame() is None
    reader.close()
