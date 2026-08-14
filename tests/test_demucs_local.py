"""Tests for local Demucs stem separation backend.

Unit tests only — no actual model loading or inference.
Real separation tests live in tests/integration/demucs/.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf
import torch

from immich_memories.audio.generators.demucs_local import (
    DemucsLocalBackend,
    _is_demucs_importable,
    _load_audio,
    _save_audio,
)


class TestDemucsImportability:
    """Test demucs package detection."""

    def test_importable_when_installed(self):
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            assert _is_demucs_importable() is True

    def test_not_importable_when_missing(self):
        with patch("importlib.util.find_spec", return_value=None):
            assert _is_demucs_importable() is False

    def test_not_importable_on_import_error(self):
        with patch("importlib.util.find_spec", side_effect=ImportError):
            assert _is_demucs_importable() is False


class TestDemucsLocalBackendAvailability:
    """Test is_available() detection."""

    @pytest.mark.asyncio
    async def test_available_when_demucs_installed(self):
        backend = DemucsLocalBackend()
        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=True,
        ):
            assert await backend.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_demucs_missing(self):
        backend = DemucsLocalBackend()
        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=False,
        ):
            assert await backend.is_available() is False

    def test_name_default(self):
        backend = DemucsLocalBackend()
        assert backend.name == "Demucs (local, htdemucs)"

    def test_name_custom_model(self):
        backend = DemucsLocalBackend(model_name="htdemucs_ft")
        assert backend.name == "Demucs (local, htdemucs_ft)"


class TestSoundFileCodec:
    """Local Demucs WAV I/O must not depend on TorchCodec."""

    def test_load_audio_returns_channel_first_float_tensor(self, tmp_path: Path):
        audio_path = tmp_path / "input.wav"
        samples = np.column_stack(
            (
                np.linspace(-0.5, 0.5, 80, dtype=np.float32),
                np.linspace(0.5, -0.5, 80, dtype=np.float32),
            )
        )
        sf.write(audio_path, samples, 16_000, subtype="FLOAT")

        waveform, sample_rate = _load_audio(audio_path)

        assert sample_rate == 16_000
        assert waveform.shape == (2, 80)
        assert waveform.dtype == torch.float32
        np.testing.assert_allclose(waveform.numpy().T, samples, atol=1e-6)

    def test_save_audio_writes_readable_stereo_wav(self, tmp_path: Path):
        audio_path = tmp_path / "stem.wav"
        waveform = torch.stack(
            (
                torch.linspace(-0.5, 0.5, 80),
                torch.linspace(0.5, -0.5, 80),
            )
        )

        _save_audio(audio_path, waveform, 16_000)
        saved, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)

        assert sample_rate == 16_000
        assert saved.shape == (80, 2)
        np.testing.assert_allclose(saved, waveform.numpy().T, atol=4e-5)


class TestHealthCheck:
    """Test health_check() reporting."""

    @pytest.mark.asyncio
    async def test_health_check_reports_state(self):
        backend = DemucsLocalBackend(model_name="htdemucs", device="cpu")
        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=True,
        ):
            health = await backend.health_check()

        assert health["backend"] == "Demucs (local, htdemucs)"
        assert health["available"] is True
        assert health["model"] == "htdemucs"
        assert health["loaded"] is False

    @pytest.mark.asyncio
    async def test_health_check_when_loaded(self):
        backend = DemucsLocalBackend(device="cpu")
        backend._model = MagicMock()  # Simulate loaded model
        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=True,
        ):
            health = await backend.health_check()
        assert health["loaded"] is True


class TestRelease:
    """Test model memory release."""

    def test_release_clears_model(self):
        backend = DemucsLocalBackend()
        backend._model = MagicMock()
        backend.release()
        assert backend._model is None

    def test_release_idempotent(self):
        backend = DemucsLocalBackend()
        backend.release()  # No model loaded
        assert backend._model is None
