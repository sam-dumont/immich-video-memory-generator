"""Unit tests for streaming assembler components."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)  # noqa: S603, S607
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@requires_ffmpeg
class TestFrameDecoder:
    def test_yields_frames_with_correct_shape(self, tmp_path: object) -> None:
        """FrameDecoder should yield numpy arrays of (height, width, 3)."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        tmp = Path(str(tmp_path))

        # Create a tiny test clip via FFmpeg
        clip = tmp / "test.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10:duration=0.5",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )

        decoder = FrameDecoder(clip, width=320, height=240, fps=10)
        frames = list(decoder)

        assert len(frames) >= 4  # 0.5s * 10fps = 5 frames (allow off-by-one)
        for frame in frames:
            assert frame.shape == (240, 320, 3)
            assert frame.dtype == np.uint8
