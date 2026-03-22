"""Tests for content-backed title screen backgrounds.

Verifies frame extraction, blur/darken treatment, and fallback behavior.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

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


class TestExtractRepresentativeFrame:
    """Tests for extracting a frame from a video clip."""

    def test_extracts_frame_with_correct_shape(self, sample_video: Path):
        from immich_memories.titles.content_background import extract_representative_frame

        frame = extract_representative_frame(sample_video, width=200, height=150)
        assert frame is not None
        assert frame.shape == (150, 200, 3)

    def test_frame_values_are_float32_normalized(self, sample_video: Path):
        from immich_memories.titles.content_background import extract_representative_frame

        frame = extract_representative_frame(sample_video, width=100, height=80)
        assert frame is not None
        assert frame.dtype == np.float32
        assert frame.min() >= 0.0
        assert frame.max() <= 1.0

    def test_returns_none_for_nonexistent_file(self):
        from immich_memories.titles.content_background import extract_representative_frame

        result = extract_representative_frame(Path("/nonexistent/video.mp4"), 100, 80)
        assert result is None

    def test_returns_none_when_ffmpeg_missing(self, sample_video: Path):
        from immich_memories.titles.content_background import extract_representative_frame

        # WHY: simulates environments without ffmpeg installed
        with patch("shutil.which", return_value=None):
            result = extract_representative_frame(sample_video, 100, 80)
            assert result is None


class TestPrepareContentBackground:
    """Tests for blur + darken treatment of extracted frames."""

    def test_darkens_frame(self):
        from immich_memories.titles.content_background import prepare_content_background

        # Solid white frame
        frame = np.ones((100, 100, 3), dtype=np.float32)
        result = prepare_content_background(frame, darken=0.45, blur_radius=0)
        # After darkening by 0.45, mean should be ~0.45
        assert result.mean() < 0.55, f"Expected darkened, got mean {result.mean():.3f}"

    def test_blur_smooths_edges(self):
        from immich_memories.titles.content_background import prepare_content_background

        # Sharp checkerboard pattern
        frame = np.zeros((100, 100, 3), dtype=np.float32)
        frame[::2, ::2] = 1.0  # Every other pixel white
        blurred = prepare_content_background(frame, darken=1.0, blur_radius=10)
        # Blur should reduce variance significantly
        original_var = frame.var()
        blurred_var = blurred.var()
        assert blurred_var < original_var * 0.5, (
            f"Blur should reduce variance: original={original_var:.4f}, blurred={blurred_var:.4f}"
        )

    def test_output_shape_matches_input(self):
        from immich_memories.titles.content_background import prepare_content_background

        frame = np.ones((200, 300, 3), dtype=np.float32) * 0.5
        result = prepare_content_background(frame)
        assert result.shape == frame.shape

    def test_output_is_float32(self):
        from immich_memories.titles.content_background import prepare_content_background

        frame = np.ones((50, 50, 3), dtype=np.float32) * 0.8
        result = prepare_content_background(frame)
        assert result.dtype == np.float32

    def test_values_stay_in_range(self):
        from immich_memories.titles.content_background import prepare_content_background

        frame = np.ones((50, 50, 3), dtype=np.float32)
        result = prepare_content_background(frame, darken=0.3, blur_radius=5)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


class TestExtractContentBackground:
    """Tests for the one-shot convenience function."""

    def test_end_to_end_with_video(self, sample_video: Path):
        from immich_memories.titles.content_background import extract_content_background

        result = extract_content_background(sample_video, width=200, height=150)
        assert result is not None
        assert result.shape == (150, 200, 3)
        assert result.dtype == np.float32
        # Should be dark (red video darkened to ~0.45 brightness)
        assert result.mean() < 0.5

    def test_returns_none_for_bad_video(self):
        from immich_memories.titles.content_background import extract_content_background

        result = extract_content_background(Path("/nonexistent.mp4"), 100, 80)
        assert result is None


class TestPerformance:
    """Benchmark title generation performance."""

    @pytest.mark.benchmark
    def test_frame_extraction_speed(self, sample_video: Path, benchmark):
        """Frame extraction should complete within 500ms."""
        from immich_memories.titles.content_background import extract_representative_frame

        result = benchmark(extract_representative_frame, sample_video, 320, 240)
        assert result is not None

    @pytest.mark.benchmark
    def test_blur_darken_speed(self, benchmark):
        """Blur + darken treatment on 1080p frame should be fast."""
        from immich_memories.titles.content_background import prepare_content_background

        frame = np.random.rand(1080, 1920, 3).astype(np.float32)
        result = benchmark(prepare_content_background, frame, 0.45, 40)
        assert result is not None
