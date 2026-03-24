"""Tests for video downscaler path logic and encoder selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.processing.downscaler import (
    DEFAULT_ANALYSIS_HEIGHT,
    _get_fast_encoder_args,
    cleanup_downscaled,
    get_downscaled_path,
    needs_downscaling,
)


class TestGetDownscaledPath:
    """Tests for downscaled path naming."""

    def test_default_height_suffix(self):
        original = Path("/tmp/videos/video.mp4")
        result = get_downscaled_path(original)
        assert result == Path("/tmp/videos/video_480p.mp4")

    def test_custom_height_suffix(self):
        original = Path("/tmp/videos/video.mp4")
        result = get_downscaled_path(original, target_height=720)
        assert result == Path("/tmp/videos/video_720p.mp4")

    def test_preserves_directory(self):
        original = Path("/deep/nested/dir/clip.mov")
        result = get_downscaled_path(original, target_height=480)
        assert result.parent == original.parent

    def test_preserves_extension(self):
        original = Path("/tmp/videos/video.mkv")
        result = get_downscaled_path(original)
        assert result.suffix == ".mkv"

    def test_different_extensions(self):
        for ext in [".mp4", ".mov", ".avi", ".webm"]:
            original = Path(f"/tmp/video{ext}")
            result = get_downscaled_path(original)
            assert result.suffix == ext
            assert result.stem == "video_480p"

    def test_filename_with_dots(self):
        original = Path("/tmp/my.video.file.mp4")
        result = get_downscaled_path(original)
        assert result == Path("/tmp/my.video.file_480p.mp4")


class TestNeedsDownscaling:
    """Tests for downscaling threshold logic."""

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_tall_video_needs_downscaling(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        mock_height.return_value = 2160  # 4K — well above 480 * 1.5 = 720
        assert needs_downscaling(Path("/tmp/video.mp4")) is True

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_small_video_no_downscaling(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        mock_height.return_value = 480  # Same as target
        assert needs_downscaling(Path("/tmp/video.mp4")) is False

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_borderline_video_no_downscaling(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        # Exactly at threshold: 480 * 1.5 = 720
        mock_height.return_value = 720
        assert needs_downscaling(Path("/tmp/video.mp4")) is False

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_just_above_threshold(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        mock_height.return_value = 721  # Just above 480 * 1.5 = 720
        assert needs_downscaling(Path("/tmp/video.mp4")) is True

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_height_unknown_returns_false(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        mock_height.return_value = 0
        assert needs_downscaling(Path("/tmp/video.mp4")) is False

    @patch("immich_memories.processing.downscaler.get_video_height")
    def test_custom_target_height(self, mock_height: MagicMock):
        # WHY: mock get_video_height to avoid ffprobe subprocess
        mock_height.return_value = 1080
        # 1080 > 720 * 1.5 = 1080 → False (not strictly greater)
        assert needs_downscaling(Path("/tmp/video.mp4"), target_height=720) is False
        # 1081 > 1080 → True
        mock_height.return_value = 1081
        assert needs_downscaling(Path("/tmp/video.mp4"), target_height=720) is True


class TestCleanupDownscaled:
    """Tests for downscaled file cleanup."""

    def test_removes_existing_downscaled_file(self, tmp_path: Path):
        original = tmp_path / "video.mp4"
        original.touch()
        downscaled = tmp_path / "video_480p.mp4"
        downscaled.write_text("dummy")

        cleanup_downscaled(original)
        assert not downscaled.exists()

    def test_noop_when_no_downscaled_file(self, tmp_path: Path):
        original = tmp_path / "video.mp4"
        original.touch()
        # No downscaled file exists — should not raise
        cleanup_downscaled(original)

    def test_does_not_delete_original_if_same_path(self, tmp_path: Path):
        # get_downscaled_path always adds a suffix, so this can't happen
        # naturally, but test the guard anyway
        video = tmp_path / "video_480p.mp4"
        video.write_text("original")
        # If someone passes a path that already has the _480p suffix,
        # the downscaled path becomes video_480p_480p.mp4 (different),
        # so the original is safe.
        cleanup_downscaled(video)
        assert video.exists()

    def test_cleanup_custom_height(self, tmp_path: Path):
        original = tmp_path / "clip.mov"
        original.touch()
        downscaled = tmp_path / "clip_720p.mov"
        downscaled.write_text("dummy")

        cleanup_downscaled(original, target_height=720)
        assert not downscaled.exists()


class TestGetFastEncoderArgs:
    """Tests for platform-dependent encoder selection."""

    @patch("immich_memories.processing.downscaler.sys")
    def test_macos_uses_videotoolbox(self, mock_sys: MagicMock):
        mock_sys.platform = "darwin"
        args = _get_fast_encoder_args()
        assert "-c:v" in args
        assert "h264_videotoolbox" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_linux_nvenc_available(self, mock_sys: MagicMock, mock_subprocess: MagicMock):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to avoid calling real ffmpeg
        mock_result = MagicMock()
        mock_result.stdout = "V..... h264_nvenc\nV..... libx264"
        mock_subprocess.run.return_value = mock_result
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "h264_nvenc" in args
        assert "-preset" in args
        assert "p1" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_linux_vaapi_available(self, mock_sys: MagicMock, mock_subprocess: MagicMock):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to avoid calling real ffmpeg
        mock_result = MagicMock()
        mock_result.stdout = "V..... h264_vaapi\nV..... libx264"
        mock_subprocess.run.return_value = mock_result
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "h264_vaapi" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_linux_qsv_available(self, mock_sys: MagicMock, mock_subprocess: MagicMock):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to avoid calling real ffmpeg
        mock_result = MagicMock()
        mock_result.stdout = "V..... h264_qsv\nV..... libx264"
        mock_subprocess.run.return_value = mock_result
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "h264_qsv" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_linux_cpu_fallback(self, mock_sys: MagicMock, mock_subprocess: MagicMock):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to avoid calling real ffmpeg
        mock_result = MagicMock()
        mock_result.stdout = "V..... libx264"  # No GPU encoders
        mock_subprocess.run.return_value = mock_result
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "libx264" in args
        assert "ultrafast" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_ffmpeg_not_found_falls_back_to_cpu(
        self, mock_sys: MagicMock, mock_subprocess: MagicMock
    ):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to simulate ffmpeg not being installed
        mock_subprocess.run.side_effect = OSError("ffmpeg not found")
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "libx264" in args
        assert "ultrafast" in args

    @patch("immich_memories.processing.downscaler.subprocess")
    @patch("immich_memories.processing.downscaler.sys")
    def test_nvenc_preferred_over_vaapi(self, mock_sys: MagicMock, mock_subprocess: MagicMock):
        mock_sys.platform = "linux"
        # WHY: mock subprocess.run to test encoder priority
        mock_result = MagicMock()
        mock_result.stdout = "V..... h264_nvenc\nV..... h264_vaapi\nV..... libx264"
        mock_subprocess.run.return_value = mock_result
        mock_subprocess.SubprocessError = Exception

        args = _get_fast_encoder_args()
        assert "h264_nvenc" in args
        assert "h264_vaapi" not in args


class TestDefaultAnalysisHeight:
    """Tests for the default constant."""

    def test_default_is_480(self):
        assert DEFAULT_ANALYSIS_HEIGHT == 480
