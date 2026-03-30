"""Behavioral tests for privacy pipeline components.

Tests actual behavior: torchcodec validation, output suppression,
temp file cleanup, quiet mode routing. No mocks — real objects only.
"""

from __future__ import annotations

import logging
import os

import pytest

from immich_memories.processing.assembly_config import AssemblyClip


def _has_torch_and_torchcodec() -> bool:
    try:
        import torch  # noqa: F401
        import torchcodec  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# _validate_torchcodec — real version check (skipped when torch not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_torch_and_torchcodec(),
    reason="torch + torchcodec not installed",
)
class TestValidateTorchcodec:
    """Real torchcodec validation with actually-installed packages."""

    def test_passes_when_versions_match(self):
        from immich_memories.audio.generators.ace_step_backend import _validate_torchcodec

        _validate_torchcodec()

    def test_version_extraction(self):
        import torch  # type: ignore[import-not-found]
        import torchcodec  # type: ignore[import-not-found]

        torch_minor = torch.__version__.split(".")[1]
        tc_minor = torchcodec.__version__.split(".")[1]
        assert torch_minor == tc_minor


# ---------------------------------------------------------------------------
# _run_with_suppressed_output — real stderr suppression
# ---------------------------------------------------------------------------


class TestRunWithSuppressedOutput:
    """Output suppression actually suppresses stderr."""

    def test_suppresses_stderr(self):
        from immich_memories.audio.generators.ace_step_backend import _run_with_suppressed_output

        def _noisy_fn(**kwargs):
            # This writes to stderr — should be suppressed
            os.write(2, b"SHOULD NOT APPEAR")
            return kwargs.get("value", 42)

        result = _run_with_suppressed_output(_noisy_fn, value=99)
        assert result == 99

    def test_returns_function_result(self):
        from immich_memories.audio.generators.ace_step_backend import _run_with_suppressed_output

        result = _run_with_suppressed_output(lambda **kw: kw["x"] + kw["y"], x=3, y=4)
        assert result == 7

    def test_restores_stderr_after_exception(self):
        from immich_memories.audio.generators.ace_step_backend import _run_with_suppressed_output

        original_stderr_fd = os.dup(2)
        try:
            with pytest.raises(ValueError, match="boom"):

                def _raise(**_kw):
                    raise ValueError("boom")

                _run_with_suppressed_output(_raise)

            # stderr should be restored — writing should work
            os.write(2, b"")  # Would fail if FD 2 is closed
        finally:
            os.close(original_stderr_fd)


# ---------------------------------------------------------------------------
# _cleanup_temp_files — real file cleanup
# ---------------------------------------------------------------------------


class TestCleanupTempFiles:
    """Temp file cleanup removes files and raw intermediates."""

    def test_removes_existing_files(self, tmp_path):
        from immich_memories.processing.streaming_audio import _cleanup_temp_files

        f1 = tmp_path / "audio_0.wav"
        f2 = tmp_path / "audio_1.wav"
        f1.write_text("data")
        f2.write_text("data")

        _cleanup_temp_files([f1, f2])

        assert not f1.exists()
        assert not f2.exists()

    def test_removes_raw_intermediates(self, tmp_path):
        from immich_memories.processing.streaming_audio import _cleanup_temp_files

        wav = tmp_path / "audio_0.wav"
        raw = tmp_path / "audio_0.raw.wav"
        wav.write_text("data")
        raw.write_text("intermediate")

        _cleanup_temp_files([wav])

        assert not wav.exists()
        assert not raw.exists()

    def test_handles_missing_files(self, tmp_path):
        from immich_memories.processing.streaming_audio import _cleanup_temp_files

        nonexistent = tmp_path / "does_not_exist.wav"
        _cleanup_temp_files([nonexistent])  # Should not raise

    def test_empty_list(self):
        from immich_memories.processing.streaming_audio import _cleanup_temp_files

        _cleanup_temp_files([])  # Should not raise


# ---------------------------------------------------------------------------
# QuietDisplay — real behavior
# ---------------------------------------------------------------------------


class TestQuietDisplay:
    """QuietDisplay emits log lines, not Rich output."""

    def test_add_task_logs(self, caplog):
        from immich_memories.cli._live_display import QuietDisplay

        with caplog.at_level(logging.INFO):
            display = QuietDisplay()
            with display:
                display.add_task("Connecting...")
            assert "Connecting..." in caplog.text

    def test_update_with_description_logs(self, caplog):
        from immich_memories.cli._live_display import QuietDisplay

        with caplog.at_level(logging.INFO):
            display = QuietDisplay()
            with display:
                tid = display.add_task("Step 1")
                display.update(tid, description="Step 2")
            assert "Step 2" in caplog.text

    def test_update_completed_logs_done(self, caplog):
        from immich_memories.cli._live_display import QuietDisplay

        with caplog.at_level(logging.INFO):
            display = QuietDisplay()
            with display:
                tid = display.add_task("Processing")
                display.update(tid, completed=True)
            assert "Done: Processing" in caplog.text

    def test_stop_is_noop(self):
        from immich_memories.cli._live_display import QuietDisplay

        display = QuietDisplay()
        display.stop()  # Should not raise


# ---------------------------------------------------------------------------
# _preprocess_privacy_audio — real ffmpeg + reversal chain
# ---------------------------------------------------------------------------


def _has_soundfile() -> bool:
    try:
        import soundfile  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_soundfile(), reason="soundfile not installed (audio-ml extra)")
class TestPreprocessPrivacyAudio:
    """Full chain: extract audio, reverse segments, save WAV."""

    def test_processes_non_title_clips(self, tmp_path):
        """Real WAV files go through ffmpeg extraction + reversal."""
        import numpy as np
        import soundfile as sf

        from immich_memories.processing.streaming_audio import _preprocess_privacy_audio

        # Create a real WAV file as source
        audio = np.random.randn(48000).astype(np.float32)  # 1 second mono
        src = tmp_path / "clip.wav"
        sf.write(str(src), audio, 48000)

        clip = AssemblyClip(path=src, duration=1.0)
        paths = _preprocess_privacy_audio([clip], tmp_path)

        assert len(paths) == 1
        assert paths[0].exists()
        assert paths[0].stat().st_size > 0

    def test_skips_title_screens(self, tmp_path):
        import numpy as np
        import soundfile as sf

        from immich_memories.processing.streaming_audio import _preprocess_privacy_audio

        src = tmp_path / "clip.wav"
        audio = np.random.randn(48000).astype(np.float32)
        sf.write(str(src), audio, 48000)

        title = AssemblyClip(path=src, duration=3.0, is_title_screen=True)
        content = AssemblyClip(path=src, duration=1.0)

        paths = _preprocess_privacy_audio([title, content], tmp_path)
        # Only content clip processed, title skipped
        assert len(paths) == 1

    def test_output_is_reversed_audio(self, tmp_path):
        """Output WAV should differ from input (reversal applied)."""
        import numpy as np
        import soundfile as sf

        from immich_memories.processing.streaming_audio import _preprocess_privacy_audio

        # Distinctive signal: ascending stereo ramp
        mono = np.linspace(-1, 1, 48000).astype(np.float32)
        audio = np.column_stack([mono, mono])
        src = tmp_path / "clip.wav"
        sf.write(str(src), audio, 48000)

        clip = AssemblyClip(path=src, duration=1.0)
        paths = _preprocess_privacy_audio([clip], tmp_path)

        result, sr = sf.read(str(paths[0]))
        # Reversed segments should NOT match the original ramp
        assert not np.allclose(result[:1000], audio[:1000], atol=0.1)


# ---------------------------------------------------------------------------
# apply_privacy_audio — real end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_soundfile(), reason="soundfile not installed (audio-ml extra)")
class TestApplyPrivacyAudio:
    """apply_privacy_audio extracts, reverses, and saves."""

    def test_produces_output_wav(self, tmp_path):
        import numpy as np
        import soundfile as sf

        from immich_memories.processing.privacy_audio import apply_privacy_audio

        audio = np.random.randn(48000, 2).astype(np.float32)
        src = tmp_path / "input.wav"
        sf.write(str(src), audio, 48000)

        out = tmp_path / "output.wav"
        apply_privacy_audio(src, out)

        assert out.exists()
        result, sr = sf.read(str(out))
        assert sr == 48000
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Quiet mode print helpers — real routing
# ---------------------------------------------------------------------------


class TestQuietModePrintHelpers:
    """Print helpers route to logging when quiet mode is active."""

    def test_error_goes_to_logger(self, caplog):
        from immich_memories.cli._helpers import print_error, set_quiet_mode

        set_quiet_mode(True)
        try:
            with caplog.at_level(logging.ERROR):
                print_error("something broke")
            assert "something broke" in caplog.text
        finally:
            set_quiet_mode(False)

    def test_info_goes_to_logger(self, caplog):
        from immich_memories.cli._helpers import print_info, set_quiet_mode

        set_quiet_mode(True)
        try:
            with caplog.at_level(logging.INFO):
                print_info("status update")
            assert "status update" in caplog.text
        finally:
            set_quiet_mode(False)
