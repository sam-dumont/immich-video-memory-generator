"""Content-backed background generation for title screens.

Streams slow-motion video backgrounds from a source clip.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from immich_memories.processing.encoding_plan import HdrTransfer
from immich_memories.processing.ffmpeg_runner import stop_owned_process

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# WHY (#517): Catmull-Rom only needs two frames, so `is_active` accepted two — but
# two frames stretched across the 3.5s ease is a still image with a smear on it.
# The default request is 0.5s of source, ~15 frames at 30fps; five is where the
# ease still has distinct motion to interpolate. Below that the static background
# is the better picture. No realistic clip falls short: five frames at 30fps is
# 0.17s, and the window itself is already floored at 0.1s.
_MIN_SOURCE_FRAMES = 5

# Half a second of source at native fps; a decode still running long after that
# is wedged rather than slow, and the static background is the better picture.
_SOURCE_DECODE_TIMEOUT_SECONDS = 30


def _probe_duration(clip_path: Path) -> float:
    """Get video duration via ffprobe."""
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(probe.stdout.strip() or "3")
    except (OSError, subprocess.SubprocessError, ValueError):
        return 3.0


class SlowmoBackgroundReader:
    """Generates smooth slow-motion frames without changing their transfer.

    Pre-reads all source frames (typically 15 at 0.5s/30fps), then
    generates interpolated intermediate frames on demand. Linear blending
    creates motion-blur-like ghosting that merges with the GPU
    renderer's heavy Gaussian blur — no optical flow needed.

    HDR content remains in its source transfer. The title encoder receives
    that transfer explicitly so content-backed title frames can be tagged and
    encoded without a destructive HDR-to-SDR-to-HDR round trip.
    """

    def __init__(
        self,
        clip_path: Path,
        width: int,
        height: int,
        fps: float,
        title_duration: float = 3.5,
        source_seconds: float = 0.5,
        source_transfer: HdrTransfer = HdrTransfer.NONE,
    ):
        self._source_frames: list[np.ndarray] = []
        self._float_cache: dict[int, np.ndarray] = {}
        self._output_index = 0
        self._total_output_frames = int(title_duration * fps)

        if not shutil.which("ffmpeg"):
            return

        clip_duration = _probe_duration(clip_path)
        actual_source = min(source_seconds, clip_duration * 0.8)
        if actual_source < 0.1:
            return

        source_is_hdr = source_transfer is not HdrTransfer.NONE

        # WHY: extract source frames at native fps — no slowdown in FFmpeg.
        # Interpolation happens in Python for smooth blending.
        cmd = [
            "ffmpeg",
            "-ss", "0",
            "-t", str(actual_source),
            "-i", str(clip_path),
            "-f", "rawvideo",
            "-pix_fmt", "rgb48le" if source_is_hdr else "rgb24",
            "-an",
            "pipe:1",
        ]  # fmt: skip

        returncode = self._decode_source_frames(
            cmd, (height, width, 3), np.uint16 if source_is_hdr else np.uint8
        )
        if returncode is None:
            logger.debug(f"No source frames read from {clip_path.name}")
            self._source_frames.clear()
            return

        # WHY (#517): frames are appended as they stream, so a decode that dies
        # partway still leaves a handful behind. Keeping them let the 3.5s ease
        # animate a ~0.1s sliver of source — a nearly frozen, smearing title
        # background. A run that failed, or that never produced enough frames for
        # the ease to read as motion, has nothing worth animating: drop the lot so
        # the caller's static-frame fallback engages.
        if returncode != 0 or len(self._source_frames) < _MIN_SOURCE_FRAMES:
            logger.warning(
                f"Slowmo decode of {clip_path.name} exited {returncode} with "
                f"{len(self._source_frames)} frames; falling back to a static background"
            )
            self._source_frames.clear()
            return

        logger.info(
            f"Loaded {len(self._source_frames)} source frames for "
            f"{self._total_output_frames} output frames ({title_duration}s)"
        )

    def _decode_source_frames(
        self,
        cmd: list[str],
        shape: tuple[int, int, int],
        dtype: type[np.uint8] | type[np.uint16],
    ) -> int | None:
        """Stream FFmpeg's raw frames into the source list; None if the decode broke.

        WHY streaming + native dtype (#408): capture_output=True held every raw
        frame (746 MB for 15 frames at 4K 16-bit) while float32 copies (1.5 GB)
        accumulated beside it — a 2.2 GB peak. Reading the pipe one frame at a
        time and keeping frames in their native dtype caps the working set at the
        raw frames plus the small float window read_frame converts on demand.
        """
        frame_size = int(np.prod(shape)) * np.dtype(dtype).itemsize
        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(frame_size)
                if len(chunk) < frame_size:
                    break
                self._source_frames.append(np.frombuffer(chunk, dtype=dtype).reshape(shape))
            return proc.wait(timeout=_SOURCE_DECODE_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"Slowmo source decode failed: {e}")
            return None
        finally:
            if proc is not None:
                stop_owned_process(proc)

    @property
    def is_active(self) -> bool:
        return len(self._source_frames) >= 2

    def read_frame(self) -> np.ndarray | None:
        """Generate the next interpolated frame.

        Maps output frame index to a fractional position in the source
        frames, then linearly blends the two adjacent source frames.
        """
        if not self.is_active:
            return None
        if self._output_index >= self._total_output_frames:
            return None

        indices, t = self._blend_window()

        # WHY: Catmull-Rom cubic interpolation uses 4 frames instead of 2.
        # This eliminates the "pop" at frame pair boundaries that linear
        # interpolation creates (C1 continuity vs C0).
        p0 = self._as_float(indices[0])
        p1 = self._as_float(indices[1])
        p2 = self._as_float(indices[2])
        p3 = self._as_float(indices[3])

        frame = 0.5 * (
            2.0 * p1
            + (-p0 + p2) * t
            + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (t * t)
            + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (t * t * t)
        )
        np.clip(frame, 0.0, 1.0, out=frame)

        self._output_index += 1
        return frame

    def _blend_window(self) -> tuple[tuple[int, int, int, int], float]:
        """Which four source frames the next output frame blends, and by how much.

        WHY: ease-in cubic maps output time to source time with acceleration.
        Starts very slow (dreamy slow-mo), ends near real-time speed so the
        hard cut to the clip doesn't "jump" from slow to fast.

        Shared by the numpy path and the GPU one so the two cannot drift.
        """
        n_src = len(self._source_frames)
        progress = self._output_index / max(1, self._total_output_frames - 1)
        eased = progress * progress * progress  # cubic ease-in
        src_pos = eased * (n_src - 1)
        idx = min(int(src_pos), n_src - 2)
        return (
            (max(0, idx - 1), idx, min(idx + 1, n_src - 1), min(idx + 2, n_src - 1)),
            src_pos - idx,
        )

    @property
    def source_frames(self) -> list[np.ndarray]:
        """The frames the whole animation interpolates between."""
        return self._source_frames

    def next_blend(self) -> tuple[tuple[int, int, int, int], float] | None:
        """Advance one output frame and report its blend, doing no pixel work.

        For callers that can interpolate on the device: the sources never
        change, so only four indices and a weight need to cross per frame.
        """
        if not self.is_active or self._output_index >= self._total_output_frames:
            return None
        window = self._blend_window()
        self._output_index += 1
        return window

    def _as_float(self, idx: int) -> np.ndarray:
        """Normalized float32 view of one source frame, cached for the 4-frame
        Catmull-Rom window. Access is monotonic, so each frame converts once
        and the float working set never exceeds four frames."""
        cached = self._float_cache.get(idx)
        if cached is not None:
            return cached
        raw = self._source_frames[idx]
        scale = 65535.0 if raw.dtype == np.uint16 else 255.0
        frame = raw.astype(np.float32) / scale
        self._float_cache[idx] = frame
        while len(self._float_cache) > 4:
            del self._float_cache[min(self._float_cache)]
        return frame

    def close(self) -> None:
        self._source_frames.clear()
        self._float_cache.clear()

    def __del__(self) -> None:
        self.close()
