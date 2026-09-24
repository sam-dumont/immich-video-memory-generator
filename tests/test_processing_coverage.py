"""Clip extraction, photo rendering, probing and HDR filter choices."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.clips import (
    ClipExtractor,
    ClipSegment,
    extract_clip,
)
from immich_memories.processing.encoding_plan import HdrTransfer
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities
from immich_memories.processing.hdr_utilities import (
    detect_dominant_hdr_transfer,
    get_colorspace_filter,
    get_hdr_conversion_filter,
    quality_to_crf,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    """Build a Config with defaults — avoids loading YAML."""
    from immich_memories.config_loader import Config

    return Config(**overrides)


def _make_segment(tmp_path: Path, start: float = 1.0, end: float = 5.0) -> ClipSegment:
    src = tmp_path / "video.mp4"
    src.write_bytes(b"\x00" * 100)
    return ClipSegment(
        source_path=src,
        start_time=start,
        end_time=end,
        asset_id="test-asset-001",
        score=0.8,
    )


def _make_asset(**overrides):
    """Build a minimal Asset for photo pipeline tests."""
    from immich_memories.api.models import Asset

    defaults = {
        "id": "asset-photo-001",
        "type": "IMAGE",
        "fileCreatedAt": datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        "fileModifiedAt": datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        "updatedAt": datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        "originalFileName": "IMG_1234.HEIC",
    }
    defaults.update(overrides)
    return Asset(**defaults)


# ============================================================================
# Module 1: clips.py
# ============================================================================


class TestMakeBufferedSegment:
    """Buffered segment creation for transitions."""

    def test_adds_start_and_end_buffer(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=2.0, end=6.0)
        extractor = ClipExtractor(tmp_path, config=config)

        # WHY: get_video_duration probes actual file via ffprobe
        with patch("immich_memories.processing.clips.get_video_duration", return_value=10.0):
            buffered, filename = extractor._make_buffered_segment(
                segment, add_start_buffer=True, add_end_buffer=True, buffer_seconds=0.5
            )

        assert buffered.start_time == 1.5
        assert buffered.end_time == 6.5
        assert "_b11" in filename

    def test_only_end_buffer(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=0.0, end=3.0)
        extractor = ClipExtractor(tmp_path, config=config)

        with patch("immich_memories.processing.clips.get_video_duration", return_value=10.0):
            buffered, filename = extractor._make_buffered_segment(
                segment, add_start_buffer=False, add_end_buffer=True, buffer_seconds=0.5
            )

        assert buffered.start_time == 0.0
        assert buffered.end_time == 3.5
        assert "_b01" in filename

    def test_buffer_clamps_to_video_duration(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=0.0, end=9.8)
        extractor = ClipExtractor(tmp_path, config=config)

        with patch("immich_memories.processing.clips.get_video_duration", return_value=10.0):
            buffered, _ = extractor._make_buffered_segment(
                segment, add_start_buffer=True, add_end_buffer=True, buffer_seconds=0.5
            )

        assert buffered.start_time == 0.0
        assert buffered.end_time == 10.0

    def test_buffer_with_zero_duration_video(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=1.0, end=4.0)
        extractor = ClipExtractor(tmp_path, config=config)

        with patch("immich_memories.processing.clips.get_video_duration", return_value=0.0):
            buffered, _ = extractor._make_buffered_segment(
                segment, add_start_buffer=False, add_end_buffer=True, buffer_seconds=0.5
            )

        # When duration <= 0, end buffer is added unconditionally
        assert buffered.end_time == 4.5


class TestExtractCopy:
    """Stream-copy extraction via FFmpeg."""

    def test_runs_ffmpeg_copy(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=1.0, end=3.0)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        # WHY: subprocess.run calls ffmpeg binary
        with (
            patch("immich_memories.processing.clips.subprocess.run") as mock_run,
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            extractor._extract_copy(segment, output)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-c" in cmd
        assert "copy" in cmd

    def test_raises_on_ffmpeg_failure(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path, start=1.0, end=3.0)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        with (
            patch("immich_memories.processing.clips.subprocess.run") as mock_run,
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="codec not found")
            with pytest.raises(RuntimeError, match="Failed to extract clip"):
                extractor._extract_copy(segment, output)


class TestBuildReencodeCommand:
    """Building FFmpeg re-encode command."""

    def test_software_encode_no_hw(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        # WHY: validate_video_path checks file exists
        with patch(
            "immich_memories.processing.clips.validate_video_path", return_value=segment.source_path
        ):
            cmd = extractor._build_reencode_command(segment, output, hw_caps=None)

        assert "ffmpeg" in cmd
        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert str(output) in cmd

    def test_hw_decode_args_when_available(self, tmp_path):
        config = _make_config()
        config.hardware.gpu_decode = True
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)
        hw = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_decode=True,
        )

        with (
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
            patch(
                "immich_memories.processing.clips.get_ffmpeg_hwaccel_args",
                return_value=["-hwaccel", "cuda"],
            ),
            patch(
                "immich_memories.processing.clips.get_ffmpeg_encoder",
                return_value=("h264_nvenc", ["-preset", "p4"]),
            ),
        ):
            cmd = extractor._build_reencode_command(segment, output, hw_caps=hw)

        assert "-hwaccel" in cmd


class TestAppendEncoderArgs:
    """Encoder arg selection (HW vs software, quality arg variants)."""

    def test_software_fallback(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        cmd: list[str] = []
        extractor._append_encoder_args(cmd, hw_caps=None, codec="h264", config=config)
        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert "-preset" in cmd

    def test_hw_encoder(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        hw = HWAccelCapabilities(backend=HWAccelBackend.NVIDIA, supports_h264_encode=True)
        cmd: list[str] = []

        # WHY: get_ffmpeg_encoder shells out to detect GPU encoders
        with patch(
            "immich_memories.processing.clips.get_ffmpeg_encoder",
            return_value=("h264_nvenc", ["-preset", "p4"]),
        ):
            extractor._append_encoder_args(cmd, hw_caps=hw, codec="h264", config=config)

        assert "h264_nvenc" in cmd

    def test_quality_args_nvenc(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        cmd: list[str] = []
        extractor._append_quality_args(cmd, "h264_nvenc", 8)
        assert cmd == ["-cq", "8"]

    def test_quality_args_videotoolbox(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        cmd: list[str] = []
        extractor._append_quality_args(cmd, "hevc_videotoolbox", 8)
        assert cmd == []

    def test_quality_args_vaapi(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        cmd: list[str] = []
        extractor._append_quality_args(cmd, "h264_vaapi", 8)
        assert cmd == ["-global_quality", "8"]

    def test_quality_args_software(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        cmd: list[str] = []
        extractor._append_quality_args(cmd, "libx264", 8)
        assert cmd == ["-crf", "8"]


class TestParseProgressLine:
    """Progress callback from FFmpeg output."""

    def test_valid_progress_line(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        values = []
        extractor._parse_progress_line("out_time_ms=5000000", 10.0, values.append)
        assert len(values) == 1
        assert values[0] == pytest.approx(0.5, abs=0.01)

    def test_ignores_non_progress_lines(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        values = []
        extractor._parse_progress_line("frame=100", 10.0, values.append)
        assert values == []

    def test_clamps_to_1(self, tmp_path):
        config = _make_config()
        extractor = ClipExtractor(tmp_path, config=config)
        values = []
        extractor._parse_progress_line("out_time_ms=20000000", 10.0, values.append)
        assert values[0] == 1.0


class TestHandleEncodeFailure:
    """HW encode failure → software fallback."""

    def test_nvenc_failure_retries_software(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)
        hw = HWAccelCapabilities(backend=HWAccelBackend.NVIDIA, supports_h264_encode=True)
        cb = MagicMock()

        with patch.object(extractor, "_extract_with_reencode") as mock_reencode:
            extractor._handle_encode_failure("Error initializing nvenc", segment, output, cb, hw)
            mock_reencode.assert_called_once_with(segment, output, cb, use_hw_accel=False)

    def test_non_nvenc_failure_raises(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        with pytest.raises(RuntimeError, match="Failed to extract clip"):
            extractor._handle_encode_failure("generic error", segment, output, MagicMock(), None)


class TestRunWithProgress:
    """FFmpeg process with progress monitoring."""

    def test_successful_encoding_with_progress(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)
        progress_values = []

        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "out_time_ms=2000000\n",
            "out_time_ms=4000000\n",
            "",  # signals EOF
        ]
        mock_process.poll.return_value = 0
        mock_process.returncode = 0
        mock_process.__enter__ = MagicMock(return_value=mock_process)
        mock_process.__exit__ = MagicMock(return_value=False)

        # WHY: subprocess.Popen runs ffmpeg binary
        with patch("immich_memories.processing.clips.subprocess.Popen", return_value=mock_process):
            extractor._run_with_progress(
                ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"],
                segment,
                progress_values.append,
                None,
                output,
            )

        assert len(progress_values) == 2

    def test_failed_encoding_triggers_handle(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = ["", ""]
        mock_process.poll.return_value = 1
        mock_process.returncode = 1
        # WHY: EOF after one chunk — a constant return_value spins the drain.
        mock_process.stderr.read.side_effect = ["encode failed", ""]
        mock_process.__enter__ = MagicMock(return_value=mock_process)
        mock_process.__exit__ = MagicMock(return_value=False)

        with (
            patch("immich_memories.processing.clips.subprocess.Popen", return_value=mock_process),
            patch.object(extractor, "_handle_encode_failure") as mock_handle,
        ):
            extractor._run_with_progress(
                ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"],
                segment,
                MagicMock(),
                None,
                output,
            )
            mock_handle.assert_called_once()


class TestExtractWithReencode:
    """Re-encode path with HW fallback."""

    def test_reencode_without_progress(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        # WHY: subprocess.run calls ffmpeg, validate_video_path checks filesystem
        # WHY: _get_hw_caps probes ffmpeg for HW accel — mock to avoid subprocess calls
        with (
            patch("immich_memories.processing.clips.subprocess.run") as mock_run,
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
            patch("immich_memories.processing.clips._get_hw_caps", return_value=None),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            extractor._extract_with_reencode(segment, output, None)
            mock_run.assert_called_once()

    def test_reencode_failure_falls_back_to_software(self, tmp_path):
        config = _make_config()
        config.hardware.enabled = True
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)

        hw = HWAccelCapabilities(backend=HWAccelBackend.NVIDIA, supports_h264_encode=True)
        call_count = 0

        def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(returncode=1, stderr="hw error")
            return MagicMock(returncode=0)

        with (
            patch(
                "immich_memories.processing.clips.subprocess.run", side_effect=mock_run_side_effect
            ),
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
            patch("immich_memories.processing.clips._get_hw_caps", return_value=hw),
        ):
            extractor._extract_with_reencode(segment, output, None, use_hw_accel=True)

        assert call_count == 2

    def test_reencode_with_progress_callback(self, tmp_path):
        config = _make_config()
        segment = _make_segment(tmp_path)
        output = tmp_path / "out.mp4"
        extractor = ClipExtractor(tmp_path, config=config)
        cb = MagicMock()

        # WHY: validate_video_path checks filesystem
        with (
            patch(
                "immich_memories.processing.clips.validate_video_path",
                return_value=segment.source_path,
            ),
            patch.object(extractor, "_run_with_progress") as mock_progress,
        ):
            extractor._extract_with_reencode(segment, output, cb)
            mock_progress.assert_called_once()


class TestExtractClipFunction:
    """Top-level extract_clip convenience function."""

    def test_extract_clip_copy_mode(self, tmp_path):
        config = _make_config()
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00" * 100)
        output = tmp_path / "out.mp4"

        # WHY: subprocess.run calls ffmpeg, validate_video_path checks filesystem
        with (
            patch("immich_memories.processing.clips.subprocess.run") as mock_run,
            patch("immich_memories.processing.clips.validate_video_path", return_value=src),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = extract_clip(src, 1.0, 3.0, output, reencode=False, config=config)

        assert result == output

    def test_extract_clip_returns_cached(self, tmp_path):
        config = _make_config()
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00" * 100)
        output = tmp_path / "out.mp4"
        output.write_bytes(b"\x00" * 50)

        result = extract_clip(src, 1.0, 3.0, output, config=config)
        assert result == output

    def test_extract_clip_with_buffers(self, tmp_path):
        config = _make_config()
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00" * 100)
        output = tmp_path / "out.mp4"

        with (
            patch("immich_memories.processing.clips.subprocess.run") as mock_run,
            patch("immich_memories.processing.clips.validate_video_path", return_value=src),
            patch("immich_memories.processing.clips.get_video_duration", return_value=10.0),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = extract_clip(
                src,
                1.0,
                3.0,
                output,
                buffer_start=True,
                buffer_end=True,
                buffer_seconds=0.5,
                config=config,
            )

        assert result == output


class TestRenderSinglePhoto:
    """Single photo render pipeline."""

    def test_returns_none_on_download_failure(self, tmp_path):
        from immich_memories.config_models_render import PhotoConfig
        from immich_memories.photos.photo_pipeline import render_single_photo

        asset = _make_asset(id="render-fail-001")
        config = PhotoConfig()

        def fail_download(asset_id, path):
            raise ConnectionError("offline")

        result = render_single_photo(asset, config, 1920, 1080, tmp_path, fail_download)
        assert result is None


class TestPhotoPlaceCaption:
    """A photo and a video must caption a place the same way.

    `--add-place` renders "City, Country" for a video, via clip_location_name.
    The photo path attached the bare city, so one memory containing both showed
    "Ghent, Belgium" under a video and "Ghent" under the photo beside it.

    This has never passed on a macOS runner -- not since #434 shortened it, since
    #421 introduced it. #421's macOS job failed too, and #434 merged with all
    three macOS jobs cancelled, so nothing ever reported it.
    """

    def test_a_photo_captions_a_place_like_a_video_does(self, tmp_path, monkeypatch):
        import cv2
        import numpy as np

        from immich_memories.api.models import ExifInfo
        from immich_memories.config_models_render import PhotoConfig
        from immich_memories.generate_privacy import clip_location_name
        from immich_memories.photos.photo_pipeline import render_single_photo

        # WHY: the encoder is what this fails on, not the caption.
        # render_single_photo picks hevc_videotoolbox when zscale is present, and
        # VideoToolbox writes no file inside CI's macOS VM -- the render returns
        # None and the assertion below blames the caption instead. This test has
        # never passed on a macOS runner for that reason, at any duration.
        # Forcing the SDR path (rgb24 + libx264, always available) keeps it about
        # the thing it names.
        monkeypatch.setattr(
            "immich_memories.processing.hdr_utilities.check_zscale_available", lambda: False
        )

        exif = ExifInfo(city="Ghent", country="Belgium")
        # WHY .jpg: the download path takes its suffix from the filename, and
        # the fixture default (.HEIC) is not something cv2 can write.
        asset = _make_asset(id="place-001", exifInfo=exif, originalFileName="IMG_1.jpg")

        def download(asset_id, path):
            cv2.imwrite(str(path), np.full((240, 320, 3), 128, dtype=np.uint8))

        config = PhotoConfig(duration=1.0)

        clip = render_single_photo(asset, config, 640, 360, tmp_path, download)

        assert clip is not None
        assert clip.location_name == clip_location_name(exif) == "Ghent, Belgium"


# ============================================================================
# Module 3: ffmpeg_prober.py
# ============================================================================


class TestPickResolutionTier:
    """Pure logic — resolution tier selection from counts."""

    def setup_method(self):
        self.prober = FFmpegProber(
            settings=AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
        )
        self.res_4k = (3840, 2160)
        self.res_1080p = (1920, 1080)
        self.res_720p = (1280, 720)

    def test_majority_4k(self):
        counts = {"4k": 6, "1080p": 2, "720p": 1, "other": 1}
        result = self.prober.pick_resolution_tier(
            counts, 10, "landscape", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_4k

    def test_majority_1080p(self):
        counts = {"4k": 1, "1080p": 6, "720p": 2, "other": 1}
        result = self.prober.pick_resolution_tier(
            counts, 10, "landscape", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_1080p

    def test_majority_720p(self):
        counts = {"4k": 0, "1080p": 1, "720p": 6, "other": 3}
        result = self.prober.pick_resolution_tier(
            counts, 10, "landscape", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_720p

    def test_no_majority_picks_highest(self):
        counts = {"4k": 2, "1080p": 3, "720p": 3, "other": 2}
        result = self.prober.pick_resolution_tier(
            counts, 10, "landscape", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_4k

    def test_only_720p_available(self):
        counts = {"4k": 0, "1080p": 0, "720p": 3, "other": 0}
        result = self.prober.pick_resolution_tier(
            counts, 3, "landscape", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_720p

    def test_all_zero_defaults_720p(self):
        counts = {"4k": 0, "1080p": 0, "720p": 0, "other": 5}
        result = self.prober.pick_resolution_tier(
            counts, 5, "portrait", self.res_4k, self.res_1080p, self.res_720p
        )
        assert result == self.res_720p


class TestDetectMaxFramerate:
    """Framerate rounding to common values."""

    def setup_method(self):
        self.prober = FFmpegProber(
            settings=AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
        )

    def test_60fps_round(self, tmp_path):
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=5.0)

        # WHY: detect_framerate shells out to ffprobe
        with patch.object(self.prober, "detect_framerate", return_value=59.94):
            assert self.prober.detect_max_framerate([clip]) == 60

    def test_30fps_round(self, tmp_path):
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=5.0)
        with patch.object(self.prober, "detect_framerate", return_value=29.97):
            assert self.prober.detect_max_framerate([clip]) == 30

    def test_50fps_round(self, tmp_path):
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=5.0)
        with patch.object(self.prober, "detect_framerate", return_value=50.0):
            assert self.prober.detect_max_framerate([clip]) == 50

    def test_24fps_round(self, tmp_path):
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=5.0)
        # detect_max_framerate starts at max_fps=30.0 floor; 23.976 < 30 so stays at 30
        with patch.object(self.prober, "detect_framerate", return_value=23.976):
            assert self.prober.detect_max_framerate([clip]) == 30


class TestEstimateDuration:
    """Duration estimation with transition overlap."""

    def test_no_clips(self):
        prober = FFmpegProber(
            settings=AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
        )
        assert prober.estimate_duration([]) == 0.0

    def test_single_clip(self, tmp_path):
        prober = FFmpegProber(
            settings=AssemblySettings(
                encoding_plan=standalone_assembly_encoding_plan(),
                transition=TransitionType.CROSSFADE,
            )
        )
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=10.0)
        assert prober.estimate_duration([clip]) == 10.0

    def test_crossfade_overlap(self, tmp_path):
        prober = FFmpegProber(
            settings=AssemblySettings(
                encoding_plan=standalone_assembly_encoding_plan(),
                transition=TransitionType.CROSSFADE,
                transition_duration=0.5,
            )
        )
        clips = [AssemblyClip(path=tmp_path / f"{i}.mp4", duration=5.0) for i in range(3)]
        # 15.0 - (0.5 * 2) = 14.0
        assert prober.estimate_duration(clips) == 14.0

    def test_cut_no_overlap(self, tmp_path):
        prober = FFmpegProber(
            settings=AssemblySettings(
                encoding_plan=standalone_assembly_encoding_plan(),
                transition=TransitionType.CUT,
            )
        )
        clips = [AssemblyClip(path=tmp_path / f"{i}.mp4", duration=5.0) for i in range(3)]
        assert prober.estimate_duration(clips) == 15.0


# ============================================================================
# Module 4: hdr_utilities.py
# ============================================================================


class TestGetColorspaceFilter:
    """Pure string building — no subprocess."""

    def test_hlg_filter(self):
        f = get_colorspace_filter("hlg")
        assert "arib-std-b67" in f
        assert "bt2020nc" in f

    def test_pq_filter(self):
        f = get_colorspace_filter("pq")
        assert "smpte2084" in f
        assert "bt2020nc" in f


class TestDetectDominantHdrTransfer:
    @pytest.mark.parametrize(
        ("probed", "expected"),
        [
            (["hlg", "hlg", "pq"], HdrTransfer.HLG),
            (["pq", "pq", "hlg"], HdrTransfer.PQ),
            ([None, None, None], HdrTransfer.NONE),
        ],
    )
    def test_the_majority_transfer_wins(self, tmp_path, probed, expected):
        clips = [AssemblyClip(path=tmp_path / f"{i}.mp4", duration=1.0) for i in range(3)]

        # WHY: _detect_hdr_type shells out to ffprobe
        with patch("immich_memories.processing.hdr_utilities._detect_hdr_type", side_effect=probed):
            assert detect_dominant_hdr_transfer(clips) is expected


def _conversion(source, target, primaries=None, *, zscale=True):
    # WHY: check_zscale_available asks the installed ffmpeg which filters it has
    with patch(
        "immich_memories.processing.hdr_utilities.check_zscale_available", return_value=zscale
    ):
        return get_hdr_conversion_filter(source, target, primaries)


class TestConversionChoices:
    def test_sdr_to_hlg_lifts_reference_white_to_203_nits(self):
        f = _conversion("sdr", "hlg", "bt709")
        assert "t=arib-std-b67" in f
        assert "npl=203" in f

    def test_display_p3_keeps_its_primaries_and_a_bt709_matrix(self):
        f = _conversion("sdr", "hlg", "smpte432")
        assert "pin=smpte432" in f
        assert "min=bt709" in f

    def test_hlg_and_pq_convert_into_each_other(self):
        assert "tin=arib-std-b67:t=smpte2084" in _conversion("hlg", "pq")
        assert "tin=smpte2084:t=arib-std-b67" in _conversion("pq", "hlg")

    def test_an_unknown_target_gets_no_filter(self):
        assert _conversion("sdr", "unknown") == ""

    @pytest.mark.parametrize(("source", "target"), [("sdr", "hlg"), ("hlg", "pq")])
    def test_a_conversion_without_zscale_fails_closed(self, source, target):
        with pytest.raises(RuntimeError, match="zscale"):
            _conversion(source, target, zscale=False)


class TestGetHdrConversionFilter:
    def test_same_type_no_conversion(self):
        assert get_hdr_conversion_filter("hlg", "hlg") == ""

    def test_sdr_to_hlg(self):
        # WHY: check_zscale_available shells out to ffmpeg
        with patch(
            "immich_memories.processing.hdr_utilities.check_zscale_available",
            return_value=True,
        ):
            f = get_hdr_conversion_filter(None, "hlg")
        assert "arib-std-b67" in f

    def test_sdr_string_to_pq(self):
        with patch(
            "immich_memories.processing.hdr_utilities.check_zscale_available",
            return_value=True,
        ):
            f = get_hdr_conversion_filter("sdr", "pq")
        assert "smpte2084" in f

    def test_hlg_to_pq(self):
        with patch(
            "immich_memories.processing.hdr_utilities.check_zscale_available",
            return_value=True,
        ):
            f = get_hdr_conversion_filter("hlg", "pq")
        assert "smpte2084" in f


class TestQualityToCrf:
    def test_known_presets(self):
        assert quality_to_crf("high") < quality_to_crf("balanced")
        assert quality_to_crf("fast") == quality_to_crf("balanced")

    def test_unknown_defaults_to_balanced(self):
        assert quality_to_crf("ultra") == quality_to_crf("balanced")
