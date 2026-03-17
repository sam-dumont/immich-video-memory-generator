"""Tests for hardware detection — mocking subprocess to test pure logic."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from immich_memories.processing.hardware import (
    HWAccelBackend,
    HWAccelCapabilities,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
    get_ffmpeg_scale_filter,
    get_opencv_backend,
)
from immich_memories.processing.hardware_detection import (
    _detect_apple_chip_info,
    _detect_apple_vram,
    _detect_nvidia,
    _detect_qsv,
    _detect_vaapi,
    detect_hardware_acceleration,
)

# ---------------------------------------------------------------------------
# _detect_apple_chip_info — pure logic with mocked subprocess
# ---------------------------------------------------------------------------


class TestDetectAppleChipInfo:
    def test_detects_m1(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        # WHY: mock sysctl to avoid running actual system command
        mock_result = MagicMock(returncode=0, stdout="Apple M1 Pro")
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run", return_value=mock_result
        ):
            _detect_apple_chip_info(caps)
        assert caps.extra_info["chip_generation"] == "M1"
        assert caps.extra_info["chip_variant"] == "Pro"

    def test_detects_m4_max(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        mock_result = MagicMock(returncode=0, stdout="Apple M4 Max")
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run", return_value=mock_result
        ):
            _detect_apple_chip_info(caps)
        assert caps.extra_info["chip_generation"] == "M4"
        assert caps.extra_info["chip_variant"] == "Max"

    def test_plain_chip_no_variant(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        mock_result = MagicMock(returncode=0, stdout="Apple M2")
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run", return_value=mock_result
        ):
            _detect_apple_chip_info(caps)
        assert caps.extra_info["chip_generation"] == "M2"
        assert "chip_variant" not in caps.extra_info

    def test_sysctl_failure_is_silent(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            _detect_apple_chip_info(caps)
        assert caps.extra_info == {}

    def test_timeout_is_silent(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sysctl", 5),
        ):
            _detect_apple_chip_info(caps)
        assert caps.extra_info == {}


# ---------------------------------------------------------------------------
# _detect_apple_vram
# ---------------------------------------------------------------------------


class TestDetectAppleVram:
    def test_reads_memsize(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        # 16 GB in bytes
        mock_result = MagicMock(returncode=0, stdout="17179869184\n")
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run", return_value=mock_result
        ):
            _detect_apple_vram(caps)
        assert caps.vram_mb == 16384

    def test_nonzero_returncode_leaves_vram_at_zero(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        mock_result = MagicMock(returncode=1, stdout="")
        with patch(
            "immich_memories.processing.hardware_detection.subprocess.run", return_value=mock_result
        ):
            _detect_apple_vram(caps)
        assert caps.vram_mb == 0


# ---------------------------------------------------------------------------
# _detect_nvidia — mocked ffmpeg checks
# ---------------------------------------------------------------------------


class TestDetectNvidia:
    def test_no_cuda_returns_none(self):
        with patch(
            "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
            return_value=False,
        ):
            assert _detect_nvidia() is None

    def test_cuda_available_returns_capabilities(self):
        def fake_hwaccel(name):
            return name == "cuda"

        def fake_encoder(name):
            return name == "h264_nvenc"

        def fake_decoder(name):
            return name == "h264_cuvid"

        with (
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
                side_effect=fake_hwaccel,
            ),
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_encoder",
                side_effect=fake_encoder,
            ),
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_decoder",
                side_effect=fake_decoder,
            ),
            patch(
                "immich_memories.processing.hardware_detection.subprocess.run",
                side_effect=FileNotFoundError,  # nvidia-smi not found
            ),
        ):
            caps = _detect_nvidia()

        assert caps is not None
        assert caps.backend == HWAccelBackend.NVIDIA
        assert caps.cuda_available
        assert caps.supports_h264_encode
        assert caps.supports_h264_decode
        assert caps.supports_scaling


# ---------------------------------------------------------------------------
# _detect_vaapi
# ---------------------------------------------------------------------------


class TestDetectVaapi:
    def test_non_linux_returns_none(self):
        with patch(
            "immich_memories.processing.hardware_detection.platform.system", return_value="Darwin"
        ):
            assert _detect_vaapi() is None

    def test_linux_no_vaapi_returns_none(self):
        with (
            patch(
                "immich_memories.processing.hardware_detection.platform.system",
                return_value="Linux",
            ),
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
                return_value=False,
            ),
        ):
            assert _detect_vaapi() is None


# ---------------------------------------------------------------------------
# _detect_qsv
# ---------------------------------------------------------------------------


class TestDetectQsv:
    def test_no_qsv_returns_none(self):
        with patch(
            "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
            return_value=False,
        ):
            assert _detect_qsv() is None


# ---------------------------------------------------------------------------
# detect_hardware_acceleration — integration of detectors
# ---------------------------------------------------------------------------


class TestDetectHardwareAcceleration:
    def test_no_backends_returns_none_backend(self):
        # Clear the lru_cache so we get fresh detection
        detect_hardware_acceleration.cache_clear()
        # WHY: patching both modules since hardware.py re-exports from _hardware_backends
        with (
            patch(
                "immich_memories.processing.hardware_detection._detect_nvidia", return_value=None
            ),
            patch("immich_memories.processing.hardware_detection._detect_apple", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_qsv", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_vaapi", return_value=None),
            patch(
                "immich_memories.processing._hardware_backends._detect_nvidia", return_value=None
            ),
            patch("immich_memories.processing._hardware_backends._detect_apple", return_value=None),
            patch("immich_memories.processing._hardware_backends._detect_qsv", return_value=None),
            patch("immich_memories.processing._hardware_backends._detect_vaapi", return_value=None),
        ):
            caps = detect_hardware_acceleration()
        assert caps.backend == HWAccelBackend.NONE
        detect_hardware_acceleration.cache_clear()

    def test_first_backend_with_encoding_wins(self):
        detect_hardware_acceleration.cache_clear()
        nvidia_caps = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_encode=True,
        )
        with (
            patch(
                "immich_memories.processing.hardware_detection._detect_nvidia",
                return_value=nvidia_caps,
            ),
            patch("immich_memories.processing.hardware_detection._detect_apple", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_qsv", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_vaapi", return_value=None),
        ):
            caps = detect_hardware_acceleration()
        assert caps.backend == HWAccelBackend.NVIDIA
        detect_hardware_acceleration.cache_clear()


# ---------------------------------------------------------------------------
# hardware.py helper functions (pure logic, no subprocess)
# ---------------------------------------------------------------------------


class TestGetFfmpegHwaccelArgs:
    def test_none_backend_returns_empty(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NONE)
        assert get_ffmpeg_hwaccel_args(caps) == []

    def test_nvidia_decode_args(self):
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_decode=True,
        )
        args = get_ffmpeg_hwaccel_args(caps, operation="decode", codec="h264")
        assert "-hwaccel" in args
        assert "cuda" in args

    def test_apple_decode_args(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)
        args = get_ffmpeg_hwaccel_args(caps, operation="decode")
        assert args == ["-hwaccel", "videotoolbox"]


class TestGetFfmpegEncoder:
    def test_nvidia_h264_encoder(self):
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_encode=True,
        )
        encoder, args = get_ffmpeg_encoder(caps, codec="h264")
        assert encoder == "h264_nvenc"

    def test_no_hw_falls_back_to_software(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NONE)
        encoder, args = get_ffmpeg_encoder(caps, codec="h264")
        assert encoder == "libx264"

    def test_software_h265(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NONE)
        encoder, args = get_ffmpeg_encoder(caps, codec="h265")
        assert encoder == "libx265"


class TestGetFfmpegScaleFilter:
    def test_nvidia_scale(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NVIDIA, supports_scaling=True)
        assert get_ffmpeg_scale_filter(caps, 1920, 1080) == "scale_cuda=1920:1080"

    def test_vaapi_scale(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.VAAPI, supports_scaling=True)
        assert get_ffmpeg_scale_filter(caps, 1920, 1080) == "scale_vaapi=1920:1080"

    def test_software_scale(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NONE)
        assert get_ffmpeg_scale_filter(caps, 1920, 1080) == "scale=1920:1080"


class TestGetOpencvBackend:
    def test_cuda_available(self):
        caps = HWAccelCapabilities(cuda_available=True, opencv_cuda=True)
        assert get_opencv_backend(caps) == "cuda"

    def test_no_cuda(self):
        caps = HWAccelCapabilities()
        assert get_opencv_backend(caps) == "cpu"
