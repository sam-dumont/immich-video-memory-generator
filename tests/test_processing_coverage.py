"""Tests targeting specific uncovered lines in processing and photos modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.clips import (
    ClipExtractor,
    ClipSegment,
    _build_clip_output_path,
    _get_hw_caps,
    _resolve_buffer_times,
    extract_clip,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities
from immich_memories.processing.hdr_utilities import (
    _check_zscale_available,
    _crf_to_vt_quality,
    _encoder_args_cpu,
    _encoder_args_macos,
    _encoder_args_nvenc,
    _get_colorspace_filter,
    _get_dominant_hdr_type,
    _get_gpu_encoder_args,
    _get_hdr_conversion_filter,
    _get_hdr_to_hdr_filter,
    _get_sdr_to_hdr_filter,
    _hdr_color_args,
    has_any_hdr_clip,
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


class TestGetHwCaps:
    """Lines 43-45: _get_hw_caps caches hardware detection."""

    def test_returns_hw_capabilities(self):
        # WHY: detect_hardware_acceleration shells out to ffmpeg — mock it
        with patch("immich_memories.processing.clips.detect_hardware_acceleration") as mock_detect:
            import immich_memories.processing.clips as clips_mod

            clips_mod._hw_caps = None  # reset cache
            mock_detect.return_value = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
            result = _get_hw_caps()
            assert result.backend == HWAccelBackend.APPLE
            mock_detect.assert_called_once()

    def test_caches_after_first_call(self):
        with patch("immich_memories.processing.clips.detect_hardware_acceleration") as mock_detect:
            import immich_memories.processing.clips as clips_mod

            clips_mod._hw_caps = None
            mock_detect.return_value = HWAccelCapabilities(backend=HWAccelBackend.NVIDIA)
            _get_hw_caps()
            _get_hw_caps()
            mock_detect.assert_called_once()
            clips_mod._hw_caps = None  # cleanup


class TestMakeBufferedSegment:
    """Lines 174-200: buffered segment creation for transitions."""

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
    """Lines 203-225: stream-copy extraction via FFmpeg."""

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
    """Lines 298-325: building FFmpeg re-encode command."""

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
    """Lines 335-362: encoder arg selection (HW vs software, quality arg variants)."""

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
    """Lines 370-375: progress callback from FFmpeg output."""

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
    """Lines 385-389: HW encode failure → software fallback."""

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
    """Lines 408-422: FFmpeg process with progress monitoring."""

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
        mock_process.stderr.read.return_value = "encode failed"
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
    """Lines 443-461: re-encode path with HW fallback."""

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
    """Lines 492-520: top-level extract_clip convenience function."""

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


class TestResolveBufferTimes:
    """Lines 531-545: buffer time resolution with clamping."""

    def test_no_buffers(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00")
        start, end = _resolve_buffer_times(src, 2.0, 5.0, False, False, 0.5)
        assert start == 2.0
        assert end == 5.0

    def test_both_buffers(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00")

        # WHY: get_video_duration probes actual file
        with patch("immich_memories.processing.clips.get_video_duration", return_value=10.0):
            start, end = _resolve_buffer_times(src, 2.0, 5.0, True, True, 0.5)

        assert start == 1.5
        assert end == 5.5

    def test_start_buffer_clamps_to_zero(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00")

        with patch("immich_memories.processing.clips.get_video_duration", return_value=10.0):
            start, _ = _resolve_buffer_times(src, 0.2, 5.0, True, False, 0.5)

        assert start == 0.0

    def test_end_buffer_with_zero_duration_video(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_bytes(b"\x00")

        with patch("immich_memories.processing.clips.get_video_duration", return_value=0.0):
            _, end = _resolve_buffer_times(src, 1.0, 4.0, False, True, 0.5)

        assert end == 4.5


class TestBuildClipOutputPath:
    """Lines 548-566: deterministic output path generation."""

    def test_path_contains_hash_and_times(self, tmp_path):
        src = tmp_path / "video.mp4"
        result = _build_clip_output_path(src, 1.0, 3.5, False, False, False)
        assert result.suffix == ".mp4"
        assert "1.0" in result.name
        assert "3.5" in result.name

    def test_buffer_suffix_in_path(self, tmp_path):
        src = tmp_path / "video.mp4"
        result = _build_clip_output_path(src, 1.0, 3.5, True, True, False)
        assert "_b11" in result.name

    def test_reencode_suffix_in_path(self, tmp_path):
        src = tmp_path / "video.mp4"
        result = _build_clip_output_path(src, 1.0, 3.5, False, False, True)
        assert "_enc" in result.name


# ============================================================================
# Module 2: photo_pipeline.py
# ============================================================================


class TestComputeMaxPhotos:
    """Lines 212-220: max photo count from video count + ratio."""

    def test_full_ratio_returns_999(self):
        from immich_memories.photos.photo_pipeline import _compute_max_photos

        assert _compute_max_photos(10, 1.0) == 999

    def test_zero_videos_returns_10(self):
        from immich_memories.photos.photo_pipeline import _compute_max_photos

        assert _compute_max_photos(0, 0.25) == 10

    def test_normal_ratio(self):
        from immich_memories.photos.photo_pipeline import _compute_max_photos

        # 0.25 ratio with 12 videos: 0.25 * 12 / 0.75 = 4
        assert _compute_max_photos(12, 0.25) == 4


class TestSelectDistributed:
    """Lines 231-252: temporal distribution of photos."""

    def test_returns_all_when_under_limit(self):
        from immich_memories.photos.photo_pipeline import _select_distributed

        a1 = _make_asset(id="a1", fileCreatedAt=datetime(2025, 1, 1, tzinfo=UTC))
        a2 = _make_asset(id="a2", fileCreatedAt=datetime(2025, 2, 1, tzinfo=UTC))
        scored = [(a1, 0.8), (a2, 0.9)]

        result = _select_distributed(scored, max_count=5)
        assert len(result) == 2

    def test_picks_best_per_bucket(self):
        from immich_memories.photos.photo_pipeline import _select_distributed

        assets = [
            _make_asset(id=f"a{i}", fileCreatedAt=datetime(2025, 1, i + 1, tzinfo=UTC))
            for i in range(10)
        ]
        scored = [(a, float(i)) for i, a in enumerate(assets)]

        result = _select_distributed(scored, max_count=3)
        assert len(result) == 3
        # Each bucket picks the highest score
        for _, score in result:
            assert score > 0


class TestEnhanceWithLlm:
    """Lines 255-303: LLM scoring with cache."""

    def test_cache_hit_skips_llm(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        asset = _make_asset(id="cached-001")
        scored = [(asset, 0.5)]
        config = PhotoConfig()

        mock_cache = MagicMock()
        mock_cache.get_asset_scores_batch.return_value = {"cached-001": {"combined_score": 0.95}}

        # WHY: _get_score_cache imports from cache module — mock to avoid DB
        with patch(
            "immich_memories.photos.photo_pipeline._get_score_cache",
            return_value=mock_cache,
        ):
            result = _enhance_with_llm(
                scored, config, tmp_path, MagicMock(), db_path=tmp_path / "db.sqlite"
            )

        assert result[0][1] == 0.95

    def test_cache_miss_calls_llm(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _enhance_with_llm

        asset = _make_asset(id="uncached-001")
        scored = [(asset, 0.5)]
        config = PhotoConfig()

        mock_cache = MagicMock()
        mock_cache.get_asset_scores_batch.return_value = {}

        with (
            patch(
                "immich_memories.photos.photo_pipeline._get_score_cache",
                return_value=mock_cache,
            ),
            patch(
                "immich_memories.photos.photo_pipeline._llm_score_photo",
                return_value=0.85,
            ),
        ):
            result = _enhance_with_llm(
                scored, config, tmp_path, MagicMock(), db_path=tmp_path / "db.sqlite"
            )

        assert result[0][1] == 0.85
        mock_cache.save_asset_score.assert_called_once()


class TestLlmScorePhoto:
    """Lines 306-354: LLM photo scoring with thumbnail optimization."""

    def test_thumbnail_path_used_when_available(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _make_asset(id="thumb-001")
        config = PhotoConfig()
        thumb_path = tmp_path / "thumb-001_thumb.jpg"
        thumb_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        thumb_fn = MagicMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)

        # WHY: score_photo_with_llm calls the LLM API
        with patch(
            "immich_memories.photos.scoring.score_photo_with_llm",
            return_value=0.92,
        ):
            result = _llm_score_photo(
                asset, 0.5, config, tmp_path, MagicMock(), None, thumbnail_fn=thumb_fn
            )

        assert result == 0.92

    def test_falls_back_to_meta_score_on_llm_error(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _make_asset(id="err-001")
        config = PhotoConfig()
        thumb_path = tmp_path / "err-001_thumb.jpg"
        thumb_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        with patch(
            "immich_memories.photos.scoring.score_photo_with_llm",
            side_effect=RuntimeError("LLM down"),
        ):
            result = _llm_score_photo(
                asset,
                0.6,
                config,
                tmp_path,
                MagicMock(),
                None,
                thumbnail_fn=MagicMock(return_value=b"\xff"),
            )

        assert result == 0.6

    def test_falls_back_to_full_download(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _make_asset(id="no-thumb-001", originalFileName="IMG.HEIC")
        config = PhotoConfig()

        def fake_download(asset_id, path):
            path.write_bytes(b"\x00" * 50)

        # WHY: prepare_photo_source does HEIC decode + gain map extraction
        mock_prepared = MagicMock()
        mock_prepared.path = tmp_path / "prepared.jpg"
        mock_prepared.path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        # WHY: local import at line 349 resolves from animator module, not photo_pipeline
        with (
            patch(
                "immich_memories.photos.animator.prepare_photo_source",
                return_value=mock_prepared,
            ),
            patch(
                "immich_memories.photos.scoring.score_photo_with_llm",
                return_value=0.88,
            ),
        ):
            result = _llm_score_photo(
                asset, 0.4, config, tmp_path, fake_download, None, thumbnail_fn=None
            )

        assert result == 0.88

    def test_download_failure_returns_meta_score(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _llm_score_photo

        asset = _make_asset(id="dl-fail-001")
        config = PhotoConfig()

        def fail_download(asset_id, path):
            raise ConnectionError("offline")

        result = _llm_score_photo(
            asset, 0.3, config, tmp_path, fail_download, None, thumbnail_fn=None
        )
        assert result == 0.3


class TestRenderSinglePhoto:
    """Lines 367-446: single photo render pipeline."""

    def test_returns_none_on_download_failure(self, tmp_path):
        from immich_memories.config_models import PhotoConfig
        from immich_memories.photos.photo_pipeline import _render_single_photo

        asset = _make_asset(id="render-fail-001")
        config = PhotoConfig()

        def fail_download(asset_id, path):
            raise ConnectionError("offline")

        result = _render_single_photo(asset, config, 1920, 1080, tmp_path, fail_download)
        assert result is None


class TestStreamRenderToMp4:
    """Lines 377-540: FFmpeg streaming render (SDR and HDR paths)."""

    def test_sdr_path_uses_rgb24(self, tmp_path):
        import numpy as np

        from immich_memories.photos.photo_pipeline import _stream_render_to_mp4
        from immich_memories.photos.renderer import KenBurnsParams

        img = np.zeros((100, 100, 3), dtype=np.float32)
        params = KenBurnsParams(
            zoom_start=1.0,
            zoom_end=1.05,
            pan_start=(0.5, 0.5),
            pan_end=(0.5, 0.5),
            fps=30,
            duration=1.0,
        )
        output = tmp_path / "photo.mp4"

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        mock_proc.stderr = MagicMock()

        # WHY: subprocess.Popen starts ffmpeg process for encoding
        with (
            patch("immich_memories.photos.photo_pipeline.subprocess.Popen", return_value=mock_proc),
            patch(
                "immich_memories.photos.photo_pipeline._get_photo_encoder_args",
                return_value=["-c:v", "libx265"],
            ),
            patch(
                "immich_memories.photos.photo_pipeline.render_ken_burns_streaming",
                return_value=[img],
            ),
        ):
            _stream_render_to_mp4(img, params, output, 100, 100, gain_map_hdr=False)

        # Check the command used rgb24 pixel format
        call_args = mock_proc.stdin.write.call_args_list
        assert len(call_args) > 0

    def test_hdr_path_uses_rgb48le(self, tmp_path):
        import numpy as np

        from immich_memories.photos.photo_pipeline import _stream_render_to_mp4
        from immich_memories.photos.renderer import KenBurnsParams

        img = np.zeros((100, 100, 3), dtype=np.float32)
        params = KenBurnsParams(
            zoom_start=1.0,
            zoom_end=1.05,
            pan_start=(0.5, 0.5),
            pan_end=(0.5, 0.5),
            fps=30,
            duration=1.0,
        )
        output = tmp_path / "photo_hdr.mp4"

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        mock_proc.stderr = MagicMock()

        with (
            patch(
                "immich_memories.photos.photo_pipeline.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
            patch(
                "immich_memories.photos.photo_pipeline._get_photo_encoder_args",
                return_value=["-c:v", "libx265"],
            ),
            patch(
                "immich_memories.photos.photo_pipeline.render_ken_burns_streaming",
                return_value=[img],
            ),
        ):
            _stream_render_to_mp4(img, params, output, 100, 100, gain_map_hdr=True, peak_nits=1200)

        # Verify FFmpeg was called with rgb48le for HDR
        popen_cmd = mock_popen.call_args[0][0]
        assert "rgb48le" in popen_cmd

    def test_encoding_failure_raises(self, tmp_path):
        import numpy as np

        from immich_memories.photos.photo_pipeline import _stream_render_to_mp4
        from immich_memories.photos.renderer import KenBurnsParams

        img = np.zeros((100, 100, 3), dtype=np.float32)
        params = KenBurnsParams(
            zoom_start=1.0,
            zoom_end=1.05,
            pan_start=(0.5, 0.5),
            pan_end=(0.5, 0.5),
            fps=30,
            duration=1.0,
        )
        output = tmp_path / "fail.mp4"

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 1
        mock_proc.stderr.read.return_value = b"encode error"

        with (
            patch("immich_memories.photos.photo_pipeline.subprocess.Popen", return_value=mock_proc),
            patch(
                "immich_memories.photos.photo_pipeline._get_photo_encoder_args",
                return_value=["-c:v", "libx265"],
            ),
            patch(
                "immich_memories.photos.photo_pipeline.render_ken_burns_streaming",
                return_value=[img],
            ),
            pytest.raises(RuntimeError, match="Photo FFmpeg encoding failed"),
        ):
            _stream_render_to_mp4(img, params, output, 100, 100)


class TestGetPhotoEncoderArgs:
    """Lines 543-574: encoder selection for photo pipeline."""

    def test_detects_videotoolbox(self):
        from immich_memories.photos.photo_pipeline import _get_photo_encoder_args

        # WHY: subprocess.run checks ffmpeg for available encoders
        with patch("immich_memories.photos.photo_pipeline.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="hevc_videotoolbox blah")
            args = _get_photo_encoder_args()

        assert "hevc_videotoolbox" in args

    def test_falls_back_without_videotoolbox(self):
        from immich_memories.photos.photo_pipeline import _get_photo_encoder_args

        with patch("immich_memories.photos.photo_pipeline.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="libx264 libx265")
            args = _get_photo_encoder_args()

        assert "hevc_videotoolbox" not in args


# ============================================================================
# Module 3: ffmpeg_prober.py
# ============================================================================


class TestParseResolutionFromStream:
    """Pure parsing logic — no subprocess needed."""

    def setup_method(self):
        self.prober = FFmpegProber(settings=AssemblySettings())

    def test_landscape_no_rotation(self):
        stream = {"width": 1920, "height": 1080}
        assert self.prober.parse_resolution_from_stream(stream) == (1920, 1080)

    def test_swaps_for_90_degree_rotation(self):
        stream = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]}
        assert self.prober.parse_resolution_from_stream(stream) == (1080, 1920)

    def test_swaps_for_270_degree_rotation(self):
        stream = {"width": 3840, "height": 2160, "side_data_list": [{"rotation": 270}]}
        assert self.prober.parse_resolution_from_stream(stream) == (2160, 3840)

    def test_no_swap_for_180(self):
        stream = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": 180}]}
        assert self.prober.parse_resolution_from_stream(stream) == (1920, 1080)

    def test_returns_none_for_zero_dimensions(self):
        stream = {"width": 0, "height": 0}
        assert self.prober.parse_resolution_from_stream(stream) is None

    def test_returns_none_for_missing_dimensions(self):
        stream = {}
        assert self.prober.parse_resolution_from_stream(stream) is None


class TestParseFpsStr:
    """Pure string parsing — no subprocess."""

    def test_fraction(self):
        assert FFmpegProber.parse_fps_str("30/1") == 30.0

    def test_ntsc_fraction(self):
        result = FFmpegProber.parse_fps_str("60000/1001")
        assert result == pytest.approx(59.94, abs=0.01)

    def test_plain_number(self):
        assert FFmpegProber.parse_fps_str("60") == 60.0

    def test_empty_string(self):
        assert FFmpegProber.parse_fps_str("") is None

    def test_zero_denominator(self):
        assert FFmpegProber.parse_fps_str("30/0") is None


class TestPickResolutionTier:
    """Pure logic — resolution tier selection from counts."""

    def setup_method(self):
        self.prober = FFmpegProber(settings=AssemblySettings())
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
        self.prober = FFmpegProber(settings=AssemblySettings())

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
        prober = FFmpegProber(settings=AssemblySettings())
        assert prober.estimate_duration([]) == 0.0

    def test_single_clip(self, tmp_path):
        prober = FFmpegProber(settings=AssemblySettings(transition=TransitionType.CROSSFADE))
        clip = AssemblyClip(path=tmp_path / "a.mp4", duration=10.0)
        assert prober.estimate_duration([clip]) == 10.0

    def test_crossfade_overlap(self, tmp_path):
        prober = FFmpegProber(
            settings=AssemblySettings(
                transition=TransitionType.CROSSFADE,
                transition_duration=0.5,
            )
        )
        clips = [AssemblyClip(path=tmp_path / f"{i}.mp4", duration=5.0) for i in range(3)]
        # 15.0 - (0.5 * 2) = 14.0
        assert prober.estimate_duration(clips) == 14.0

    def test_cut_no_overlap(self, tmp_path):
        prober = FFmpegProber(settings=AssemblySettings(transition=TransitionType.CUT))
        clips = [AssemblyClip(path=tmp_path / f"{i}.mp4", duration=5.0) for i in range(3)]
        assert prober.estimate_duration(clips) == 15.0


# ============================================================================
# Module 4: hdr_utilities.py
# ============================================================================


class TestGetColorspaceFilter:
    """Pure string building — no subprocess."""

    def test_hlg_filter(self):
        f = _get_colorspace_filter("hlg")
        assert "arib-std-b67" in f
        assert "bt2020nc" in f

    def test_pq_filter(self):
        f = _get_colorspace_filter("pq")
        assert "smpte2084" in f
        assert "bt2020nc" in f


class TestGetDominantHdrType:
    def test_mostly_hlg(self, tmp_path):
        @dataclass
        class FakeClip:
            path: Path

        clips = [FakeClip(path=tmp_path / f"{i}.mp4") for i in range(3)]

        # WHY: _detect_hdr_type shells out to ffprobe
        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            side_effect=["hlg", "hlg", "pq"],
        ):
            assert _get_dominant_hdr_type(clips) == "hlg"

    def test_mostly_pq(self, tmp_path):
        @dataclass
        class FakeClip:
            path: Path

        clips = [FakeClip(path=tmp_path / f"{i}.mp4") for i in range(3)]

        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            side_effect=["pq", "pq", "hlg"],
        ):
            assert _get_dominant_hdr_type(clips) == "pq"

    def test_no_hdr_defaults_hlg(self, tmp_path):
        @dataclass
        class FakeClip:
            path: Path

        clips = [FakeClip(path=tmp_path / "a.mp4")]

        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            return_value=None,
        ):
            assert _get_dominant_hdr_type(clips) == "hlg"


class TestHasAnyHdrClip:
    def test_returns_true_when_hdr_present(self, tmp_path):
        @dataclass
        class FakeClip:
            path: Path

        clips = [FakeClip(path=tmp_path / "a.mp4"), FakeClip(path=tmp_path / "b.mp4")]

        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            side_effect=[None, "hlg"],
        ):
            assert has_any_hdr_clip(clips) is True

    def test_returns_false_when_all_sdr(self, tmp_path):
        @dataclass
        class FakeClip:
            path: Path

        clips = [FakeClip(path=tmp_path / "a.mp4")]

        with patch(
            "immich_memories.processing.hdr_utilities._detect_hdr_type",
            return_value=None,
        ):
            assert has_any_hdr_clip(clips) is False


class TestSdrToHdrFilter:
    def test_hlg_conversion(self):
        f = _get_sdr_to_hdr_filter("hlg", "bt709", has_zscale=True)
        assert "arib-std-b67" in f
        assert "npl=203" in f

    def test_pq_conversion(self):
        f = _get_sdr_to_hdr_filter("pq", "bt709", has_zscale=True)
        assert "smpte2084" in f

    def test_no_zscale_returns_empty(self):
        assert _get_sdr_to_hdr_filter("hlg", "bt709", has_zscale=False) == ""

    def test_display_p3_source(self):
        f = _get_sdr_to_hdr_filter("hlg", "smpte432", has_zscale=True)
        assert "pin=smpte432" in f
        assert "min=bt709" in f

    def test_unknown_target_returns_empty(self):
        assert _get_sdr_to_hdr_filter("unknown", "bt709", has_zscale=True) == ""


class TestHdrToHdrFilter:
    def test_hlg_to_pq(self):
        f = _get_hdr_to_hdr_filter("hlg", "pq", has_zscale=True)
        assert "tin=arib-std-b67" in f
        assert "t=smpte2084" in f

    def test_pq_to_hlg(self):
        f = _get_hdr_to_hdr_filter("pq", "hlg", has_zscale=True)
        assert "tin=smpte2084" in f
        assert "t=arib-std-b67" in f

    def test_same_type_returns_empty(self):
        assert _get_hdr_to_hdr_filter("hlg", "hlg", has_zscale=True) == ""

    def test_no_zscale_returns_empty(self):
        assert _get_hdr_to_hdr_filter("hlg", "pq", has_zscale=False) == ""


class TestGetHdrConversionFilter:
    def test_same_type_no_conversion(self):
        assert _get_hdr_conversion_filter("hlg", "hlg") == ""

    def test_sdr_to_hlg(self):
        # WHY: _check_zscale_available shells out to ffmpeg
        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            f = _get_hdr_conversion_filter(None, "hlg")
        assert "arib-std-b67" in f

    def test_sdr_string_to_pq(self):
        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            f = _get_hdr_conversion_filter("sdr", "pq")
        assert "smpte2084" in f

    def test_hlg_to_pq(self):
        with patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=True,
        ):
            f = _get_hdr_conversion_filter("hlg", "pq")
        assert "smpte2084" in f


class TestQualityToCrf:
    def test_known_presets(self):
        assert quality_to_crf("high") == 12
        assert quality_to_crf("medium") == 18
        assert quality_to_crf("low") == 28

    def test_unknown_defaults_to_12(self):
        assert quality_to_crf("ultra") == 12


class TestCrfToVtQuality:
    def test_crf_12(self):
        assert _crf_to_vt_quality(12) == 66

    def test_crf_18(self):
        assert _crf_to_vt_quality(18) == 54

    def test_clamps_low(self):
        assert _crf_to_vt_quality(51) == 20

    def test_clamps_high(self):
        assert _crf_to_vt_quality(0) == 90


class TestHdrColorArgs:
    def test_returns_expected_args(self):
        args = _hdr_color_args("arib-std-b67")
        assert args == [
            "-colorspace",
            "bt2020nc",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
        ]


class TestEncoderArgsMacos:
    def test_sdr_mode(self):
        args = _encoder_args_macos(18, preserve_hdr=False, color_trc="arib-std-b67")
        assert "hevc_videotoolbox" in args
        assert "-pix_fmt" not in args

    def test_hdr_mode(self):
        args = _encoder_args_macos(12, preserve_hdr=True, color_trc="arib-std-b67")
        assert "p010le" in args
        assert "bt2020" in args


class TestEncoderArgsNvenc:
    def test_available(self):
        # WHY: subprocess.run checks for hevc_nvenc encoder
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="hevc_nvenc available")
            args = _encoder_args_nvenc(18, preserve_hdr=False, color_trc="arib-std-b67")

        assert args is not None
        assert "hevc_nvenc" in args

    def test_not_available(self):
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="libx264 only")
            args = _encoder_args_nvenc(18, preserve_hdr=False, color_trc="arib-std-b67")

        assert args is None

    def test_hdr_adds_color_args(self):
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="hevc_nvenc")
            args = _encoder_args_nvenc(12, preserve_hdr=True, color_trc="smpte2084")

        assert "p010le" in args
        assert "bt2020" in args


class TestEncoderArgsCpu:
    def test_sdr_uses_libx264(self):
        args = _encoder_args_cpu(18, preserve_hdr=False, color_trc="arib-std-b67", hdr_type="hlg")
        assert "libx264" in args

    def test_hdr_hlg_uses_libx265(self):
        args = _encoder_args_cpu(12, preserve_hdr=True, color_trc="arib-std-b67", hdr_type="hlg")
        assert "libx265" in args
        assert "yuv420p10le" in args

    def test_hdr_pq_x265_params(self):
        args = _encoder_args_cpu(12, preserve_hdr=True, color_trc="smpte2084", hdr_type="pq")
        x265_param = [a for a in args if "transfer=smpte2084" in a]
        assert len(x265_param) == 1


class TestGetGpuEncoderArgs:
    def test_darwin_uses_videotoolbox(self):
        with patch("immich_memories.processing.hdr_utilities.sys") as mock_sys:
            mock_sys.platform = "darwin"
            args = _get_gpu_encoder_args(crf=18)
        assert "hevc_videotoolbox" in args

    def test_linux_nvidia(self):
        with (
            patch("immich_memories.processing.hdr_utilities.sys") as mock_sys,
            patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run,
        ):
            mock_sys.platform = "linux"
            mock_run.return_value = MagicMock(stdout="hevc_nvenc")
            args = _get_gpu_encoder_args(crf=18)
        assert "hevc_nvenc" in args

    def test_linux_cpu_fallback(self):
        with (
            patch("immich_memories.processing.hdr_utilities.sys") as mock_sys,
            patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run,
        ):
            mock_sys.platform = "linux"
            mock_run.return_value = MagicMock(stdout="libx264 libx265")
            args = _get_gpu_encoder_args(crf=18, preserve_hdr=True, hdr_type="hlg")
        assert "libx265" in args

    def test_pq_color_trc(self):
        with patch("immich_memories.processing.hdr_utilities.sys") as mock_sys:
            mock_sys.platform = "darwin"
            args = _get_gpu_encoder_args(crf=18, preserve_hdr=True, hdr_type="pq")
        assert "smpte2084" in args


class TestCheckZscaleAvailable:
    def test_available(self):
        # WHY: subprocess.run checks ffmpeg filters
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=" T.. zscale ")
            assert _check_zscale_available() is True

    def test_not_available(self):
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="scale only")
            assert _check_zscale_available() is False

    def test_error_returns_false(self):
        with patch("immich_memories.processing.hdr_utilities.subprocess.run", side_effect=OSError):
            assert _check_zscale_available() is False
