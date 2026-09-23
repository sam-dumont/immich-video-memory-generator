"""Content-aligned Live Photo stitch windows, measured from the files themselves.

The metadata plan places each companion's in-file shutter at the file midpoint.
Apple's pre-shutter lead is not half the file: measured on real bursts it varies
by −0.33 to +0.82 s per file (#1012), and a join's visible error is the
difference of two files' errors — the Francorchamps stitch rewound 0.39 s and
0.83 s of real content while its plan was arithmetically perfect.

The companions of a burst overlap by construction, so the files themselves say
how their clocks relate: cross-correlating consecutive companions' frames finds
the offset with no metadata guess at all. This module turns those measurements
into trim windows whose joins are content-continuous.

A burst's genuinely continuous content is shorter than the metadata plan
claims — the Francorchamps files held 3.2 s of continuous coverage while the
plan stitched 4.42 s, the difference being repeated (rewound) content. The
aligned windows therefore keep each planned length only as far as the real
content allows, shrinking all members proportionally; every measured case kept
each still's moment inside its window.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Correlation works at thumbnail scale: a 36-pixel-tall grayscale at 15 fps
# resolves handoff alignment to a fifteenth of a second — finer than any join
# error an eye can catch — at a cost of kilobytes per companion. The width
# follows each source's own aspect ratio, so frame shapes carry orientation.
PROBE_FPS = 15.0
_PROBE_HEIGHT = 36
# A correlation this bad means the pair shares no usable content; aligning on
# it would place a join worse than the metadata guess does.
_MAX_MATCH_ERROR = 12.0
# A member displaying less than this is not worth a join at all; such a burst
# keeps the metadata plan rather than a degenerate aligned one.
_MIN_MEMBER_SECONDS = 0.3
# Names the measurement a banked offset came from. Change it with anything above or in
# `pairwise_clock_offset` that can move an answer, so older answers retire themselves.
OFFSET_METHOD = f"motion-diff-v1-{PROBE_FPS:g}fps-{_PROBE_HEIGHT}px"


class CompanionUndecodable(ValueError):
    """A companion's bytes could not be turned into frames for alignment."""


@dataclass(frozen=True)
class ClockOffset:
    """How one companion's clock relates to the previous file's.

    `seconds` is how much later this file's local time zero sits in the
    previous file's timeline: previous(t + seconds) shows what this file shows
    at t.
    """

    seconds: float


def _probe_frame_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,  # noqa: S603
    )
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise CompanionUndecodable("companion probe found no video frame size") from None
    return width, height


def _probe_width_aspect(native_w: int, native_h: int) -> int:
    width = round(_PROBE_HEIGHT * native_w / native_h)
    return width + width % 2


def companion_frames(payload: bytes, *, fps: float = PROBE_FPS) -> np.ndarray:
    """Decode companion bytes to grayscale probe frames, shape (frames, h, w).

    The probe keeps each source's own proportions at a fixed display height, so
    a portrait companion decodes to a narrow array and a landscape one to a
    wide array. The shapes themselves then say whether two companions can be
    stitched at all: the members of one burst must share a frame.
    """
    if not payload:
        raise CompanionUndecodable("companion playback was empty")
    with tempfile.TemporaryDirectory(prefix="stitch-align-") as directory:
        path = Path(directory) / "companion.mp4"
        path.write_bytes(payload)
        native_w, native_h = _probe_frame_size(path)
        width = _probe_width_aspect(native_w, native_h)
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale={width}:{_PROBE_HEIGHT},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)  # noqa: S603
        if result.returncode or not result.stdout:
            detail = result.stderr[-300:].decode(errors="replace")
            raise CompanionUndecodable(f"companion probe failed: {detail}")
        frames = np.frombuffer(result.stdout, dtype=np.uint8)
        usable = (len(frames) // (_PROBE_HEIGHT * width)) * _PROBE_HEIGHT * width
        if usable < _PROBE_HEIGHT * width:
            raise CompanionUndecodable("companion probe produced no frames")
        return frames[:usable].reshape(-1, _PROBE_HEIGHT, width).astype(np.float32)


def pairwise_clock_offset(frames_a: np.ndarray, frames_b: np.ndarray) -> ClockOffset | None:
    """How much later b's clock started, in a's timeline, from shared content.

    Correlates frame-to-frame motion (per-pixel differences) rather than raw
    frames: a shared scene survives exposure and colour shifts that way. None
    when the overlap is too short, the match is poor, or the correlation has
    no clear winner — a wrong measurement would place a join worse than the
    metadata guess does.
    """
    if frames_a.shape[1:] != frames_b.shape[1:]:
        # Different orientations or proportions: these members cannot share a
        # frame, so the burst is not stitchable at all (the caller keeps the
        # stills as photographs).
        return None
    motion_a = np.abs(np.diff(frames_a, axis=0))
    motion_b = np.abs(np.diff(frames_b, axis=0))
    error_by_offset: list[tuple[float, float]] = []
    for d in range(len(motion_a) - 2):
        n = min(len(motion_b), len(motion_a) - d)
        if n < 3:
            break
        error_by_offset.append((float(np.abs(motion_b[:n] - motion_a[d : d + n]).mean()), d))
    if len(error_by_offset) < 6:
        return None
    errors = np.array([error for error, _ in error_by_offset])
    best_error, best_d = min(error_by_offset)
    median = float(np.median(errors))
    # The best must clearly beat the typical offset error: flat or repeated
    # content scores equally well everywhere, and a best of 0 against a median
    # of 0 says nothing was measured.
    if best_error >= median * 0.7 or best_error >= _MAX_MATCH_ERROR:
        return None
    # Separation is measured against the best error at least four probe
    # frames away: adjacent offsets share most of their frames and always
    # score nearly as well, so they say nothing about confidence. A far
    # offset matching almost as well means the content repeats itself and the
    # winner cannot be trusted. Near-static scenes score several offsets
    # closely, so the bar is a ratio to the best, not to the median.
    far = min(error for error, d in error_by_offset if abs(d - best_d) > 4)
    if far < best_error * 1.25:
        return None
    return ClockOffset(seconds=best_d / PROBE_FPS)


def aligned_trims(
    trims: list[tuple[float, float]],
    durations: list[float],
    measured_deltas: list[float],
) -> list[tuple[float, float]] | None:
    """Re-place trim windows so every join is content-continuous.

    `measured_deltas[i]` is the measured clock offset between companion i and
    i+1: companion i+1's local time t shows what companion i shows at
    t + delta. Content continuity at join i then requires
    `end_i = start_{i+1} + delta_i`.

    The planned window lengths are kept as far as the files' genuinely
    continuous content allows, shrunk proportionally when it is shorter than
    the plan stitched — the difference was rewind artifacts. Returns None when
    the measured clocks leave a window before its file starts: such a pair
    barely overlaps, and the burst keeps the metadata plan.
    """
    if not (len(trims) == len(durations) == len(measured_deltas) + 1):
        raise ValueError("alignment needs one measured delta per join")
    # Each file's clock zero sits at this accumulated position on the first
    # file's timeline; the chain's end is the genuinely continuous span, since
    # consecutive companions overlap by construction.
    clocks = [0.0]
    for delta in measured_deltas:
        clocks.append(clocks[-1] + delta)
    continuous = clocks[-1] + durations[-1]
    planned_total = sum(end - start for start, end in trims)
    budget = min(planned_total, continuous)
    scale = budget / planned_total
    windows: list[tuple[float, float]] = []
    for index, (start, end) in enumerate(trims):
        window_start = start if index == 0 else windows[-1][1] - measured_deltas[index - 1]
        length = min((end - start) * scale, budget, durations[index] - window_start)
        if window_start < 0 or length <= 0:
            return None
        windows.append((window_start, window_start + length))
        budget -= length
    return windows
