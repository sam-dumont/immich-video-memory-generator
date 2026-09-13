"""Tests for system info capture with mocked subprocess calls."""

from __future__ import annotations

import subprocess as _subprocess
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.tracking.system_info import (
    _check_kernel_library,
    _get_cpu_brand,
    _get_ffmpeg_version,
    _get_gpu_name,
    _get_opencv_version,
    _get_ram_gb,
    _get_vram_mb,
)


def _reinitialized(**_kwargs) -> None:
    raise AssertionError("the kernel runtime was reinitialized")


class TestCheckTaichi:
    """Optional Taichi detection must distinguish absence from a broken install."""

    def test_reuses_initialized_title_runtime_without_reinitializing_taichi(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """System capture must not invalidate kernels owned by the title runtime."""
        from immich_memories.titles import kernels

        monkeypatch.setattr(kernels, "_kernels_initialized", True)
        monkeypatch.setattr(kernels, "_kernel_arch", "Metal")
        # WHY not patch("taichi.init"): naming the library imports it, and a
        # process that loaded Quadrants dies on a Taichi import (#558). The
        # initializer this module owns is the boundary either way.
        monkeypatch.setattr(
            kernels,
            "_silent_init",
            _reinitialized,
        )

        assert _check_kernel_library() is True

    def test_missing_optional_package_is_unavailable(self, monkeypatch: pytest.MonkeyPatch):
        """A base/dev install with no kernel library can still record a pipeline run."""
        from immich_memories.titles import kernels

        monkeypatch.setattr(kernels, "KERNELS_AVAILABLE", False)

        assert _check_kernel_library() is False

    def test_broken_taichi_dependency_is_not_hidden(self, monkeypatch: pytest.MonkeyPatch):
        """A corrupt optional install remains actionable instead of becoming false."""
        from immich_memories.titles import kernels

        def raise_missing_runtime() -> bool:
            raise ModuleNotFoundError(
                "No module named 'taichi_runtime'",
                name="taichi_runtime",
            )

        monkeypatch.setattr(kernels, "kernels_available", raise_missing_runtime)

        with pytest.raises(ModuleNotFoundError, match="taichi_runtime"):
            _check_kernel_library()


class TestGetCpuBrand:
    """Tests for CPU brand detection across platforms."""

    # WHY: platform.system() returns the real OS — force "Linux" for branch coverage
    @patch("immich_memories.tracking.system_info.platform")
    # WHY: subprocess.run calls sysctl/lscpu — avoid real shell commands in tests
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_linux_reads_proc_cpuinfo(
        self, mock_subprocess: MagicMock, mock_platform: MagicMock, tmp_path
    ):
        """Linux reads CPU brand from /proc/cpuinfo."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_platform.system.return_value = "Linux"
        cpuinfo = "processor\t: 0\nmodel name\t: Intel Core i7-12700K\nstepping\t: 0\n"
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda _self: iter(cpuinfo.splitlines(keepends=True))
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = _get_cpu_brand()
        assert result == "Intel Core i7-12700K"

    # WHY: platform.system() returns the real OS — force "Darwin" for macOS branch
    @patch("immich_memories.tracking.system_info.platform")
    # WHY: subprocess.run calls sysctl — avoid real shell commands in tests
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_darwin_uses_sysctl(self, mock_subprocess: MagicMock, mock_platform: MagicMock):
        """macOS uses sysctl to get CPU brand."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_platform.system.return_value = "Darwin"
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="Apple M2 Pro\n")
        result = _get_cpu_brand()
        assert result == "Apple M2 Pro"

    # WHY: platform.system() returns the real OS — force exception to test error handling
    @patch("immich_memories.tracking.system_info.platform")
    def test_exception_returns_none(self, mock_platform: MagicMock):
        """Exception during detection returns None."""
        mock_platform.system.side_effect = OSError("boom")
        assert _get_cpu_brand() is None


class TestGetRamGb:
    """Tests for RAM detection."""

    # WHY: platform.system() returns the real OS — force "Darwin" for macOS branch
    @patch("immich_memories.tracking.system_info.platform")
    # WHY: subprocess.run calls sysctl hw.memsize — avoid real shell commands
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_darwin_parses_memsize(self, mock_subprocess: MagicMock, mock_platform: MagicMock):
        """macOS parses hw.memsize from sysctl."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_platform.system.return_value = "Darwin"
        # 16 GB in bytes
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout=str(16 * 1024**3) + "\n")
        result = _get_ram_gb()
        assert result == pytest.approx(16.0)

    # WHY: platform.system() returns the real OS — force exception to test error path
    @patch("immich_memories.tracking.system_info.platform")
    def test_exception_returns_zero(self, mock_platform: MagicMock):
        """Exception during detection returns 0.0."""
        mock_platform.system.side_effect = OSError("boom")
        assert _get_ram_gb() == 0.0


class TestGetFfmpegVersion:
    """Tests for FFmpeg version detection."""

    # WHY: subprocess.run calls `ffmpeg -version` — avoid requiring ffmpeg in test env
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_parses_version_string(self, mock_subprocess: MagicMock):
        """Extracts version number from ffmpeg -version output."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="ffmpeg version 7.0.1 Copyright (c) 2000-2024 the FFmpeg developers\n",
        )
        assert _get_ffmpeg_version() == "7.0.1"

    # WHY: subprocess.run calls `ffmpeg -version` — simulate missing binary
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_returns_none_on_failure(self, mock_subprocess: MagicMock):
        """Returns None when ffmpeg is not installed."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_subprocess.run.side_effect = FileNotFoundError()
        assert _get_ffmpeg_version() is None

    # WHY: subprocess.run calls `ffmpeg -version` — simulate non-zero exit code
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_returns_none_on_nonzero_exit(self, mock_subprocess: MagicMock):
        """Returns None when ffmpeg exits with error."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_subprocess.run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_ffmpeg_version() is None


class TestGetOpencvVersion:
    """Tests for OpenCV version detection."""

    def test_returns_version_when_available(self):
        """Returns cv2.__version__ when importable."""
        mock_cv2 = MagicMock(__version__="4.9.0")
        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            # Need to reimport to pick up the mock
            from importlib import reload

            import immich_memories.tracking.system_info as mod

            reload(mod)
            # Since the function does a direct import, we mock at call time
            with patch("builtins.__import__", side_effect=ImportError):
                assert _get_opencv_version() is None

    def test_returns_none_when_not_installed(self):
        """Returns None when cv2 is not importable."""
        with patch.dict("sys.modules", {"cv2": None}):
            assert _get_opencv_version() is None


class TestGetGpuName:
    """Tests for GPU name detection."""

    # WHY: platform.system() returns the real OS — force "Linux" for nvidia-smi branch
    @patch("immich_memories.tracking.system_info.platform")
    # WHY: subprocess.run calls nvidia-smi — avoid requiring GPU hardware in tests
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_linux_nvidia_smi(self, mock_subprocess: MagicMock, mock_platform: MagicMock):
        """Linux detects NVIDIA GPU via nvidia-smi."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_platform.system.return_value = "Linux"
        mock_subprocess.run.return_value = MagicMock(
            returncode=0, stdout="NVIDIA GeForce RTX 4090\n"
        )
        assert _get_gpu_name() == "NVIDIA GeForce RTX 4090"

    # WHY: platform.system() returns the real OS — force exception to test error path
    @patch("immich_memories.tracking.system_info.platform")
    def test_exception_returns_none(self, mock_platform: MagicMock):
        """Exception during detection returns None."""
        mock_platform.system.side_effect = OSError("boom")
        assert _get_gpu_name() is None


class TestGetVramMb:
    """Tests for VRAM detection."""

    # WHY: platform.system() returns the real OS — force "Linux" for nvidia-smi VRAM query
    @patch("immich_memories.tracking.system_info.platform")
    # WHY: subprocess.run calls nvidia-smi for VRAM — avoid requiring GPU hardware
    @patch("immich_memories.tracking.system_info.subprocess")
    def test_linux_nvidia_vram(self, mock_subprocess: MagicMock, mock_platform: MagicMock):
        """Linux reads VRAM from nvidia-smi."""
        mock_subprocess.SubprocessError = _subprocess.SubprocessError
        mock_platform.system.return_value = "Linux"
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="24576\n")
        assert _get_vram_mb() == 24576

    # WHY: platform.system() returns the real OS — force exception to test error path
    @patch("immich_memories.tracking.system_info.platform")
    def test_exception_returns_zero(self, mock_platform: MagicMock):
        """Exception during detection returns 0."""
        mock_platform.system.side_effect = OSError("boom")
        assert _get_vram_mb() == 0
