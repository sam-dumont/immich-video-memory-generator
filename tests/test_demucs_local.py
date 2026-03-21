"""Tests for local Demucs stem separation backend.

Unit tests only — no actual model loading or inference.
Real separation tests live in tests/integration/demucs/.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from immich_memories.audio.generators.demucs_local import (
    DemucsLocalBackend,
    _is_demucs_importable,
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
