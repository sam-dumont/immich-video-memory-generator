"""Composing decoded frames into the output stream.

Kept apart from the assembler because none of it runs FFmpeg: it takes frame
iterators and a sink, owns the crossfade scratch buffer, and keeps the frame
count, progress reporting and preview throttling that the assembler used to
thread through argument lists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Protocol

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def blend_crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alpha: float,
    out: np.ndarray,
) -> None:
    """In-place crossfade blend: alpha=0 → frame_a, alpha=1 → frame_b.

    WHY cv2 rather than numpy: the three-pass numpy version measured 37.5 ms a
    frame at 4K against 3.5 ms here, and its `casting="unsafe"` truncated every
    element toward zero -- up to 1.6 levels of error where rounding costs 0.5.
    addWeighted also writes straight into `out`, so the second scratch buffer
    the numpy path needed (another 25 MB at 4K) is gone.
    """
    cv2.addWeighted(frame_a, 1.0 - alpha, frame_b, alpha, 0.0, dst=out)


class FrameSink(Protocol):
    """The encoder end of the pipe, as the blender needs to see it."""

    def write_frame(self, frame: np.ndarray) -> None: ...


def _alloc_blend_buf(width: int, height: int, is_hdr: bool) -> np.ndarray:
    """Allocate the blend destination. One buffer: cv2 writes straight into it."""
    if is_hdr:
        # WHY: yuv420p10le is flat uint16 — W*H*3 bytes = W*H*3/2 uint16 samples
        return np.zeros(width * height * 3 // 2, dtype=np.uint16)
    return np.zeros((height, width, 3), dtype=np.uint8)


def _match_blend_bufs(ref: np.ndarray, blend_buf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Create black frame and ensure blend buffers match actual frame shape.

    WHY: HDR mode pre-allocates flat uint16 YUV buffers, but some clips
    (title screens, FFmpeg filter fallback on Linux) may decode as 3D RGB.
    """
    black = np.zeros_like(ref)
    # WHY: in YUV, all-zeros = GREEN (U=0, V=0 = green chroma).
    # For flat uint16 arrays (yuv420p10le), set chroma planes to 512.
    if ref.ndim == 1 and ref.dtype == np.uint16:
        # Y plane occupies first 2/3 of the flat array, U+V the last 1/3
        y_size = len(ref) * 2 // 3
        black[y_size:] = 512
    if blend_buf.shape != ref.shape or blend_buf.dtype != ref.dtype:
        blend_buf = np.zeros_like(ref)
    return black, blend_buf


def _hold_or_fallback(
    frame: np.ndarray | None,
    last: np.ndarray | None,
    black: np.ndarray,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Return frame (or held last frame) and update last-seen cache."""
    if frame is not None:
        return frame, frame
    return (last if last is not None else black), last


class FrameBlender:
    """Feed decoded frames to a sink, crossfading across clip boundaries."""

    def __init__(
        self,
        sink: FrameSink,
        width: int,
        height: int,
        is_hdr: bool = False,
        total_frames: int = 0,
        report_interval: int = 1,
        progress_callback: Callable[[int, int], None] | None = None,
        frame_preview_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        self._sink = sink
        self._width = width
        self._height = height
        self._is_hdr = is_hdr
        self._total_frames = total_frames
        self._report_interval = report_interval
        self._progress_callback = progress_callback
        self._frame_preview_callback = frame_preview_callback
        self._blend_buf = _alloc_blend_buf(width, height, is_hdr)
        self._frames_written = 0
        self._last_preview_time = 0.0

    @property
    def frames_written(self) -> int:
        """Frames handed to the sink so far, crossfade frames included."""
        return self._frames_written

    def emit_body(self, active_iter: Iterator[np.ndarray], count: int) -> None:
        """Write `count` frames from one clip straight through to the sink."""
        for emitted in range(max(count, 0)):
            frame = next(active_iter, None)
            if frame is None:
                if emitted < count:
                    logger.warning(
                        f"Frame underrun: expected {count} frames, got {emitted} "
                        f"(missing {count - emitted} frames = "
                        f"{(count - emitted) / max(self._total_frames, 1) * 100:.1f}%)"
                    )
                break
            self._sink.write_frame(frame)
            self._frames_written += 1
            if self._progress_callback and self._frames_written % self._report_interval == 0:
                self._progress_callback(self._frames_written, self._total_frames)
            self._emit_preview(frame)

    def emit_crossfade(
        self,
        active_iter: Iterator[np.ndarray],
        next_iter: Iterator[np.ndarray],
        fade_frames: int,
    ) -> None:
        """Blend `fade_frames` from two clips and write the result to the sink."""
        blend_buf = self._blend_buf
        black: np.ndarray | None = None
        last_a: np.ndarray | None = None
        last_b: np.ndarray | None = None
        for fade_idx in range(fade_frames):
            frame_a = next(active_iter, None)
            frame_b = next(next_iter, None)

            if frame_a is None is frame_b:
                break

            if black is None:
                ref = frame_a if frame_a is not None else frame_b
                assert ref is not None  # noqa: S101
                black, blend_buf = _match_blend_bufs(ref, blend_buf)

            frame_a, last_a = _hold_or_fallback(frame_a, last_a, black)
            frame_b, last_b = _hold_or_fallback(frame_b, last_b, black)

            alpha = (fade_idx + 1) / fade_frames
            blend_crossfade(frame_a, frame_b, alpha, out=blend_buf)
            self._sink.write_frame(blend_buf)
            self._emit_preview(blend_buf)

        # WHY: the fade is billed at its nominal length even if a clip ran
        # short, so the progress bar stays aligned with the estimated total.
        self._frames_written += fade_frames
        if self._progress_callback:
            self._progress_callback(self._frames_written, self._total_frames)

    def _emit_preview(self, frame: np.ndarray) -> None:
        from immich_memories.processing.frame_preview import _maybe_emit_preview

        self._last_preview_time = _maybe_emit_preview(
            frame,
            self._last_preview_time,
            self._frame_preview_callback,
            self._is_hdr,
            self._height,
            self._width,
        )
