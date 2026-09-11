"""Tests for content-backed title screen backgrounds.

Verifies slow-motion source streaming and its static-background fallback.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def sample_video(tmp_path: Path) -> Path | None:
    """Create a minimal test video using FFmpeg."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not available")

    video_path = tmp_path / "sample.mp4"
    # WHY: generates a 2-second solid red video for frame extraction tests
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=320x240:duration=2:rate=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video_path),
        ],
        capture_output=True,
        timeout=30,
    )
    if not video_path.exists():
        pytest.skip("Could not create sample video")
    return video_path


class TestSourceLoadingStreams:
    """#408: capture_output=True buffered every raw frame (746 MB at 4K HDR)
    while float32 copies accumulated beside it — a 2.2 GB peak the comment
    called 150 MB. Loading must stream the pipe, one frame at a time."""

    def test_loading_never_uses_the_buffering_api(self, sample_video: Path):
        import subprocess as sp
        from unittest.mock import patch

        from immich_memories.titles.content_background import SlowmoBackgroundReader

        real_run = sp.run  # captured before the patch replaces the module attr

        # WHY: subprocess.run(capture_output=True) IS the leak — holding the
        # whole raw stream. The tiny ffprobe duration call may buffer; the
        # frame extraction must go through a streamed pipe.
        def no_rawvideo_run(cmd, *args, **kwargs):
            assert "rawvideo" not in cmd, "frame extraction used the buffering API"
            return real_run(cmd, *args, **kwargs)

        # WHY: run() with capture_output IS the leak under test; the probe may pass
        with patch(
            "immich_memories.titles.content_background.subprocess.run",
            side_effect=no_rawvideo_run,
        ):
            reader = SlowmoBackgroundReader(sample_video, width=64, height=64, fps=10.0)

        assert reader.is_active
        frame = reader.read_frame()
        assert frame is not None and frame.dtype == np.float32


class TestFailedDecodeFallsBackToStatic:
    """#517: the streaming rewrite dropped the ffmpeg returncode check. Frames
    are appended as they arrive, so a decode that died mid-stream kept its
    partial output and the 3.5s slow-mo ease stretched over a ~0.1s sliver —
    a nearly frozen, smearing background instead of the static fallback."""

    @staticmethod
    def _reader(returncode: int, frame_count: int) -> object:
        import io
        from unittest.mock import MagicMock, patch

        from immich_memories.titles.content_background import SlowmoBackgroundReader

        mod = "immich_memories.titles.content_background"
        duration_probe = MagicMock(stdout="2.0", returncode=0)
        frame = np.full((2, 2, 3), 200, dtype=np.uint8).tobytes()
        proc = MagicMock()
        proc.stdout = io.BytesIO(frame * frame_count)
        proc.wait.return_value = returncode

        # The fake streams whole frames, then EOFs with a chosen exit status.
        # WHY: ffprobe and ffmpeg are the boundary the keep-or-drop decision reads.
        with (
            patch(f"{mod}.shutil.which", return_value="ffmpeg"),
            patch(f"{mod}.subprocess.run", side_effect=[duration_probe]),
            patch(f"{mod}.subprocess.Popen", return_value=proc),
        ):
            return SlowmoBackgroundReader(
                Path("/nonexistent/content.mp4"),
                width=2,
                height=2,
                fps=2,
                title_duration=1.0,
            )

    def test_nonzero_exit_drops_the_partial_frames(self):
        reader = self._reader(returncode=1, frame_count=8)

        assert not reader.is_active, (
            "frames from a failed decode were kept — the ease will animate a sliver"
        )
        assert reader.read_frame() is None

    def test_clean_exit_keeps_its_frames(self):
        reader = self._reader(returncode=0, frame_count=8)

        assert reader.is_active
        assert reader.read_frame() is not None

    def test_a_handful_of_frames_is_not_enough_to_animate(self):
        """A clean exit that yielded almost nothing is the same sliver: the ease
        would spread three frames over the full title duration."""
        reader = self._reader(returncode=0, frame_count=3)

        assert not reader.is_active
