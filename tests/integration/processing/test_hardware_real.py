"""Integration tests for hardware detection and FFmpeg encoder/decoder checks.

These tests run real subprocess calls — no mocking. They verify that the
detection pipeline returns structurally valid results on whatever platform
is running the tests.

Run with: make test-integration-processing
"""

from __future__ import annotations

import platform

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# detect_hardware_acceleration
# ---------------------------------------------------------------------------


def test_detect_hardware_returns_valid_backend():
    from immich_memories.processing.hardware import (
        HWAccelBackend,
        detect_hardware_acceleration,
    )

    detect_hardware_acceleration.cache_clear()
    caps = detect_hardware_acceleration()

    assert caps.backend in list(HWAccelBackend)


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
def test_apple_backend_has_metal():
    from immich_memories.processing.hardware import (
        HWAccelBackend,
        detect_hardware_acceleration,
    )

    detect_hardware_acceleration.cache_clear()
    caps = detect_hardware_acceleration()

    # On macOS with FFmpeg built with VideoToolbox, Apple backend is detected
    if caps.backend == HWAccelBackend.APPLE:
        assert caps.metal_available is True
        assert caps.supports_h264_encode is True
        assert caps.device_name != ""


# ---------------------------------------------------------------------------
# Software fallback: NONE backend
# ---------------------------------------------------------------------------


def _make_none_caps():
    from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities

    return HWAccelCapabilities(backend=HWAccelBackend.NONE)


def test_software_fallback_h264_encoder():
    from immich_memories.processing.hardware import get_ffmpeg_encoder

    encoder, args = get_ffmpeg_encoder(_make_none_caps(), codec="h264")
    assert encoder == "libx264"
    assert "-preset" in args


def test_software_fallback_h265_encoder():
    from immich_memories.processing.hardware import get_ffmpeg_encoder

    encoder, args = get_ffmpeg_encoder(_make_none_caps(), codec="h265")
    assert encoder == "libx265"
    assert "-preset" in args


# ---------------------------------------------------------------------------
# Encoder / decoder existence checks (real FFmpeg subprocess)
# ---------------------------------------------------------------------------


def test_check_ffmpeg_encoder_libx264_exists():
    from immich_memories.processing.hardware import _check_ffmpeg_encoder

    assert _check_ffmpeg_encoder("libx264") is True


def test_check_ffmpeg_encoder_fake_returns_false():
    from immich_memories.processing.hardware import _check_ffmpeg_encoder

    assert _check_ffmpeg_encoder("fake_encoder_xyz_999") is False


def test_check_ffmpeg_decoder_h264_exists():
    from immich_memories.processing.hardware import _check_ffmpeg_decoder

    assert _check_ffmpeg_decoder("h264") is True


def test_check_ffmpeg_decoder_fake_returns_false():
    from immich_memories.processing.hardware import _check_ffmpeg_decoder

    assert _check_ffmpeg_decoder("fake_decoder_xyz_999") is False


# ---------------------------------------------------------------------------
# get_ffmpeg_hwaccel_args with NONE backend
# ---------------------------------------------------------------------------


def test_hwaccel_args_none_backend_returns_empty():
    from immich_memories.processing.hardware import get_ffmpeg_hwaccel_args

    args = get_ffmpeg_hwaccel_args(_make_none_caps(), operation="both", codec="h264")
    assert args == []


# ---------------------------------------------------------------------------
# get_ffmpeg_scale_filter with NONE backend
# ---------------------------------------------------------------------------


def test_scale_filter_none_backend_returns_standard():
    from immich_memories.processing.hardware import get_ffmpeg_scale_filter

    result = get_ffmpeg_scale_filter(_make_none_caps(), width=1920, height=1080)
    assert result == "scale=1920:1080"


# ---------------------------------------------------------------------------
# Encoder preset variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", ["fast", "balanced", "quality"])
def test_software_preset_variants_return_valid_args(preset):
    from immich_memories.processing.hardware import get_ffmpeg_encoder

    encoder, args = get_ffmpeg_encoder(_make_none_caps(), codec="h264", preset=preset)
    assert encoder == "libx264"
    assert len(args) >= 2
    assert args[0] == "-preset"
    # Preset value should be a recognized x264 preset string
    assert args[1] in {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    }


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
@pytest.mark.parametrize("preset", ["fast", "balanced", "quality"])
def test_apple_preset_variants(preset):
    """On macOS with VideoToolbox, Apple encoder presets should return valid args."""
    from immich_memories.processing.hardware import (
        HWAccelBackend,
        detect_hardware_acceleration,
        get_ffmpeg_encoder,
    )

    detect_hardware_acceleration.cache_clear()
    caps = detect_hardware_acceleration()

    if caps.backend != HWAccelBackend.APPLE:
        pytest.skip("Apple backend not detected")

    encoder, args = get_ffmpeg_encoder(caps, codec="h264", preset=preset)
    assert "videotoolbox" in encoder
    assert len(args) >= 2
