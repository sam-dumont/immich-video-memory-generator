"""Tests for video downscaler path logic and encoder selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.processing.downscaler import (
    DEFAULT_ANALYSIS_HEIGHT,
    cleanup_downscaled,
    get_downscaled_path,
    needs_downscaling,
)
from immich_memories.processing.hardware import fast_encoder_args


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


class TestFastEncoderArgs:
    """Analysis/preview temp encodes use a hardware encoder only when the probe passed (#343)."""

    @staticmethod
    def _caps(backend):
        from immich_memories.processing.hardware import HWAccelCapabilities

        return HWAccelCapabilities(backend=backend, supports_h264_encode=True)

    @patch("immich_memories.processing.hardware.sys")
    def test_macos_uses_videotoolbox(self, mock_sys: MagicMock):
        mock_sys.platform = "darwin"  # WHY: VideoToolbox is always present on macOS
        assert "h264_videotoolbox" in fast_encoder_args()

    @patch("immich_memories.processing.hardware.detect_hardware_acceleration")
    @patch("immich_memories.processing.hardware.sys")
    def test_probed_backend_picks_its_fast_encoder(self, mock_sys, detect):
        from immich_memories.processing.hardware import HWAccelBackend

        mock_sys.platform = "linux"
        for backend, encoder in (
            (HWAccelBackend.NVIDIA, "h264_nvenc"),
            (HWAccelBackend.VAAPI, "h264_vaapi"),
            (HWAccelBackend.QSV, "h264_qsv"),
        ):
            detect.return_value = self._caps(backend)  # WHY: stands in for a probed device
            assert encoder in fast_encoder_args()

    @patch("immich_memories.processing.hardware.detect_hardware_acceleration")
    @patch("immich_memories.processing.hardware.sys")
    def test_no_probed_backend_means_software_even_if_ffmpeg_lists_nvenc(self, mock_sys, detect):
        from immich_memories.processing.hardware import HWAccelBackend

        mock_sys.platform = "linux"
        detect.return_value = self._caps(HWAccelBackend.NONE)  # WHY: GPU-less Docker host
        args = fast_encoder_args()
        assert "libx264" in args and "ultrafast" in args
        assert "nvenc" not in " ".join(args)

    @patch("immich_memories.processing.hardware.detect_hardware_acceleration")
    def test_hardware_disabled_never_probes(self, detect):
        args = fast_encoder_args(hardware_enabled=False)
        assert "libx264" in args
        detect.assert_not_called()


class TestDefaultAnalysisHeight:
    """Tests for the default constant."""

    def test_default_is_480(self):
        assert DEFAULT_ANALYSIS_HEIGHT == 480
