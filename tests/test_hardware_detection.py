"""Tests for hardware detection — mocking subprocess to test pure logic."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

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
                # WHY: CI has no NVIDIA GPU; this stands in for "listed AND a probe encode works"
                "immich_memories.processing.hardware_detection._encoder_works",
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

    def test_nvenc_listed_by_ffmpeg_but_no_gpu_yields_no_encoding(self):
        """Debian's ffmpeg lists cuda + h264_nvenc on every box; only a probe encode proves a GPU (#343)."""
        with (
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
                return_value=True,
            ),
            patch(
                # WHY: models the GPU-less Docker host — encoder listed, probe encode fails
                "immich_memories.processing.hardware_detection._encoder_works",
                return_value=False,
            ),
        ):
            caps = _detect_nvidia()

        assert caps is None or not caps.has_encoding


class TestEncoderWorks:
    def test_listed_encoder_still_needs_a_successful_probe(self):
        from immich_memories.processing.hardware_detection import _encoder_works

        with (
            # WHY: what `ffmpeg -encoders` prints is build-dependent, not hardware-dependent
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_encoder",
                return_value=True,
            ),
            # WHY: the probe is the real device check; both outcomes must be exercised
            patch(
                "immich_memories.processing.hardware_detection._probe_ffmpeg_encode",
                side_effect=[True, False],
            ) as probe,
        ):
            assert _encoder_works("h264_nvenc") is True
            assert _encoder_works("h264_nvenc") is False
        assert probe.call_count == 2

    def test_unlisted_encoder_is_not_probed(self):
        from immich_memories.processing.hardware_detection import _encoder_works

        with (
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_encoder",
                return_value=False,
            ),
            patch("immich_memories.processing.hardware_detection._probe_ffmpeg_encode") as probe,
        ):
            assert _encoder_works("h264_qsv", upload="qsv") is False
        probe.assert_not_called()


class TestEncoderProbe:
    def test_software_encoder_probe_succeeds_and_absent_hardware_encoder_fails(self):
        import shutil

        from immich_memories.processing.hardware import _probe_ffmpeg_encode

        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg not installed")

        assert _probe_ffmpeg_encode(["-c:v", "libx264"]) is True
        # No NVIDIA driver on this machine: the probe must fail even if ffmpeg lists the encoder.
        assert _probe_ffmpeg_encode(["-c:v", "h264_nvenc"]) is False


# ---------------------------------------------------------------------------
# _detect_vaapi
# ---------------------------------------------------------------------------


class TestDetectVaapi:
    def test_vaapi_listed_but_probe_fails_yields_no_encoding(self):
        with (
            patch(
                "immich_memories.processing.hardware_detection.platform.system",
                return_value="Linux",
            ),
            # WHY: ffmpeg lists vaapi + h264_vaapi on Debian regardless of /dev/dri
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.hardware_detection._encoder_works",
                return_value=False,
            ),
        ):
            caps = _detect_vaapi()

        assert caps is not None and not caps.has_encoding

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
    def test_qsv_listed_but_probe_fails_yields_no_encoding(self):
        with (
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_hwaccel",
                return_value=True,
            ),
            # WHY: models an Intel-less host whose ffmpeg build lists h264_qsv
            patch(
                "immich_memories.processing.hardware_detection._encoder_works",
                return_value=False,
            ),
            patch(
                "immich_memories.processing.hardware_detection._check_ffmpeg_decoder",
                return_value=False,
            ),
        ):
            caps = _detect_qsv()

        assert caps is not None and not caps.has_encoding

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
        # WHY: no backend may talk to real hardware in a unit test
        with (
            patch(
                "immich_memories.processing.hardware_detection._detect_nvidia", return_value=None
            ),
            patch("immich_memories.processing.hardware_detection._detect_apple", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_qsv", return_value=None),
            patch("immich_memories.processing.hardware_detection._detect_vaapi", return_value=None),
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

    def test_prores_uses_software_prores_encoder(self):
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.APPLE,
            prores_encode=True,
        )
        encoder, args = get_ffmpeg_encoder(caps, codec="prores")
        assert encoder == "prores_ks"
        assert args == []

    @pytest.mark.parametrize(
        ("preset", "priority_speed"),
        [
            pytest.param("fast", "1", id="fast"),
            pytest.param("balanced", "0", id="balanced"),
            pytest.param("quality", "0", id="quality"),
        ],
    )
    def test_apple_preset_controls_speed_not_image_quality(
        self,
        preset: str,
        priority_speed: str,
    ) -> None:
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.APPLE,
            supports_h265_encode=True,
        )

        encoder, args = get_ffmpeg_encoder(caps, codec="h265", preset=preset)

        assert encoder == "hevc_videotoolbox"
        assert args == ["-prio_speed", priority_speed, "-allow_sw", "1"]
        assert "-q:v" not in args

    def test_unknown_codec_is_rejected_instead_of_becoming_h265(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.NONE)
        with pytest.raises(ValueError, match="Unsupported codec"):
            get_ffmpeg_encoder(caps, codec="vp9")  # type: ignore[arg-type]


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


class TestHwaccelArgsForSoftwareFilters:
    """`-hwaccel cuda -hwaccel_output_format cuda` leaves decoded frames in GPU
    memory, where a CPU filter like `scale=-2:480` cannot reach them and ffmpeg
    fails outright. Callers that filter on the CPU need the decode acceleration
    without the output-format pin.
    """

    def test_nvidia_keeps_frames_in_system_memory_when_asked(self):
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_decode=True,
            supports_h265_decode=True,
        )

        args = get_ffmpeg_hwaccel_args(caps, operation="decode", for_software_filters=True)

        assert "-hwaccel" in args, "hardware decode should still be requested"
        assert "cuda" in args
        assert "-hwaccel_output_format" not in args

    def test_nvidia_default_still_pins_frames_on_the_gpu(self):
        caps = HWAccelCapabilities(
            backend=HWAccelBackend.NVIDIA,
            supports_h264_decode=True,
            supports_h265_decode=True,
        )

        args = get_ffmpeg_hwaccel_args(caps, operation="decode")

        assert "-hwaccel_output_format" in args

    def test_apple_is_unaffected_because_it_already_decodes_to_system_memory(self):
        caps = HWAccelCapabilities(backend=HWAccelBackend.APPLE)

        plain = get_ffmpeg_hwaccel_args(caps, operation="decode")
        for_filters = get_ffmpeg_hwaccel_args(caps, operation="decode", for_software_filters=True)

        assert plain == for_filters == ["-hwaccel", "videotoolbox"]
