"""Streaming video assembler — constant-memory frame blending.

Decodes clips one at a time, blends crossfade transitions with numpy,
and pipes frames to a single FFmpeg encode process. Memory stays constant
regardless of clip count (~550 MB at 4K, ~300 MB at 1080p).

Extends the proven photo pipeline pattern (photos/renderer.py + photo_pipeline.py).
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class FrameDecoder:
    """Decode a video clip to raw frames via FFmpeg stdout pipe.

    Yields one numpy frame (H, W, 3, uint8) at a time. Only one FFmpeg
    process is alive per decoder instance.
    """

    def __init__(
        self,
        clip_path: Path,
        width: int,
        height: int,
        fps: int,
        pix_fmt: str = "rgb24",
    ) -> None:
        self._clip_path = clip_path
        self._width = width
        self._height = height
        self._fps = fps
        self._pix_fmt = pix_fmt
        self._frame_size = width * height * 3  # rgb24 = 3 bytes/pixel

    def __iter__(self) -> Iterator[np.ndarray]:
        """Yield decoded frames one at a time."""
        cmd = [
            "ffmpeg",
            "-i",
            str(self._clip_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            self._pix_fmt,
            "-vf",
            (
                f"scale={self._width}:{self._height}"
                f":force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={self._fps},setsar=1"
            ),
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "pipe:1",
        ]
        proc = subprocess.Popen(  # noqa: S603, S607
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._frame_size,
        )
        assert proc.stdout is not None  # noqa: S101

        try:
            while True:
                raw = proc.stdout.read(self._frame_size)
                if len(raw) < self._frame_size:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(self._height, self._width, 3)
                yield frame
        finally:
            proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


class StreamingEncoder:
    """Encode raw frames to video via FFmpeg stdin pipe.

    Uses ndarray.data (memoryview) for zero-copy writes — saves ~25 MB
    per frame at 4K vs .tobytes().
    """

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        crf: int = 18,
        pix_fmt: str = "yuv420p",
        codec: str = "libx264",
    ) -> None:
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        self._crf = crf
        self._pix_fmt = pix_fmt
        self._codec = codec
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start the FFmpeg encode process."""
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-c:v",
            self._codec,
            "-preset",
            "medium",
            "-crf",
            str(self._crf),
            "-pix_fmt",
            self._pix_fmt,
            "-movflags",
            "+faststart",
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(  # noqa: S603, S607
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write_frame(self, frame: np.ndarray) -> None:
        """Write one frame to the encoder. Uses memoryview for zero-copy."""
        assert self._proc is not None and self._proc.stdin is not None  # noqa: S101
        # WHY: ndarray.data is a memoryview — avoids copying ~25 MB per 4K frame
        # that .tobytes() would allocate
        self._proc.stdin.write(frame.data)

    def finish(self) -> None:
        """Close stdin pipe and wait for FFmpeg to finish."""
        if self._proc is None:
            return
        assert self._proc.stdin is not None  # noqa: S101
        with contextlib.suppress(BrokenPipeError):
            self._proc.stdin.close()
        self._proc.wait(timeout=300)
        if self._proc.returncode != 0:
            stderr = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
            raise RuntimeError(
                f"Streaming encode failed (exit {self._proc.returncode}): {stderr[-500:]}"
            )
