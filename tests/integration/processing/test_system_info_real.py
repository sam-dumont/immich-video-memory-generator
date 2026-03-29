"""Integration tests for system info capture with real subprocess calls.

These tests verify that capture_system_info and its helpers return
structurally valid results on the current platform. No mocking.

Run with: make test-integration-processing
"""

from __future__ import annotations

import platform

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# capture_system_info
# ---------------------------------------------------------------------------


def test_capture_system_info_valid_platform():
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    assert info.platform in {"darwin", "linux", "windows"}


def test_capture_system_info_cpu_cores_positive():
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    assert info.cpu_cores > 0


def test_capture_system_info_ram_positive():
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    assert info.ram_gb > 0.5


def test_capture_system_info_python_version():
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    expected = platform.python_version()
    assert info.python_version == expected


def test_capture_system_info_machine_arch():
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    assert info.machine_arch in {"arm64", "aarch64", "x86_64", "AMD64", "i386", "i686"}


# ---------------------------------------------------------------------------
# Individual helper functions
# ---------------------------------------------------------------------------


def test_get_cpu_brand_returns_nonempty():
    from immich_memories.tracking.system_info import _get_cpu_brand

    brand = _get_cpu_brand()
    assert brand is not None
    assert len(brand) > 0


def test_get_ram_gb_positive():
    from immich_memories.tracking.system_info import _get_ram_gb

    ram = _get_ram_gb()
    assert ram > 0.5


def test_get_ffmpeg_version_contains_dot():
    from immich_memories.tracking.system_info import _get_ffmpeg_version

    version = _get_ffmpeg_version()
    # FFmpeg should be installed for integration tests
    assert version is not None, "FFmpeg not found — required for integration tests"
    assert "." in version, f"Expected version with dot separator, got: {version}"


def test_get_opencv_version_returns_version_or_none():
    from immich_memories.tracking.system_info import _get_opencv_version

    version = _get_opencv_version()
    if version is not None:
        # Should look like a version string (e.g., "4.10.0")
        assert "." in version


def test_capture_system_info_serializes_to_dict():
    """Verify to_dict round-trip produces all expected keys."""
    from immich_memories.tracking.system_info import capture_system_info

    info = capture_system_info()
    d = info.to_dict()

    assert "platform" in d
    assert "python_version" in d
    assert "cpu_cores" in d
    assert "ram_gb" in d
    assert "ffmpeg_version" in d
