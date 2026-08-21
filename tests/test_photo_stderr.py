"""Tests for photo FFmpeg stderr capture.

WHY: stderr=DEVNULL discards FFmpeg error messages. When encoding fails,
there's no diagnostic info — just a non-zero return code.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


class TestStreamRenderStderrCapture:
    """_stream_render_to_mp4 must capture stderr and raise on failure."""

    def test_ffmpeg_failure_raises_with_stderr(self, tmp_path):
        """When FFmpeg returns non-zero, raise with captured stderr."""
        import numpy as np

        from immich_memories.photos.photo_pipeline import _stream_render_to_mp4
        from immich_memories.photos.renderer import KenBurnsParams

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        params = KenBurnsParams(duration=1.0, fps=1, zoom_start=1.0, zoom_end=1.2)
        output = tmp_path / "test.mp4"

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 1
        mock_proc.stderr = MagicMock()
        # WHY: a real pipe reaches EOF; a constant return_value never does and
        # the drain thread spins on it for the rest of the suite.
        mock_proc.stderr.read.side_effect = [b"Error: codec not found", b""]

        threads_before = set(threading.enumerate())

        # WHY: mock Popen and render_ken_burns_streaming to avoid real FFmpeg
        with (
            patch(
                "immich_memories.photos.photo_pipeline.subprocess.Popen",
                return_value=mock_proc,
            ),
            patch(
                "immich_memories.photos.photo_pipeline.render_ken_burns_streaming",
                return_value=[np.zeros((100, 100, 3))],
            ),
            pytest.raises(RuntimeError, match="codec not found"),
        ):
            _stream_render_to_mp4(img, params, output, 100, 100)

        # WHY: a drain thread that outlives the call spins on the mock for the
        # rest of the suite (~250k read()/s, each recorded by MagicMock) — the
        # +2.8 GB / 10 s regression behind the macOS CI exit-137 kills.
        leaked = [t for t in threading.enumerate() if t not in threads_before and t.is_alive()]
        assert not leaked, f"stderr drain thread outlived _stream_render_to_mp4: {leaked}"

    def test_ffmpeg_success_does_not_raise(self, tmp_path):
        """When FFmpeg succeeds, no error raised."""
        import numpy as np

        from immich_memories.photos.photo_pipeline import _stream_render_to_mp4
        from immich_memories.photos.renderer import KenBurnsParams

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        params = KenBurnsParams(duration=1.0, fps=1, zoom_start=1.0, zoom_end=1.2)
        output = tmp_path / "test.mp4"

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b""

        # WHY: mock Popen to avoid real FFmpeg
        with (
            patch("immich_memories.photos.photo_pipeline.subprocess.Popen", return_value=mock_proc),
            patch(
                "immich_memories.photos.photo_pipeline.render_ken_burns_streaming",
                return_value=[np.zeros((100, 100, 3))],
            ),
        ):
            _stream_render_to_mp4(img, params, output, 100, 100)  # Should not raise
