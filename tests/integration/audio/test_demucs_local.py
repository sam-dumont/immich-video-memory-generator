"""Integration tests for local Demucs stem separation.

Runs the REAL Demucs htdemucs model on test audio.
Model cache: ~/.cache/torch/hub/ (~80MB first run)

Run: make test-integration-audio
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.audio.conftest import requires_demucs

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("audio_ml")]


@requires_demucs
class TestDemucsLocalSeparation:
    """Real Demucs inference tests — expensive, needs demucs package."""

    @pytest.mark.asyncio
    async def test_separate_produces_4_stems(self, test_audio_5s: Path, tmp_path: Path):
        """htdemucs separates into drums, bass, other, vocals."""
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        backend = DemucsLocalBackend(model_name="htdemucs")
        stems = await backend.separate_stems(test_audio_5s, tmp_path)

        assert stems.vocals.exists()
        assert stems.drums is not None and stems.drums.exists()
        assert stems.bass is not None and stems.bass.exists()
        assert stems.other is not None and stems.other.exists()
        assert stems.has_full_stems

        # Each stem should be a non-empty WAV
        for stem_path in [stems.vocals, stems.drums, stems.bass, stems.other]:
            assert stem_path.stat().st_size > 100, f"{stem_path.name} is suspiciously small"

    @pytest.mark.asyncio
    async def test_separate_preserves_duration(self, test_audio_5s: Path, tmp_path: Path):
        """Output stems should have roughly the same duration as input."""
        import json
        import subprocess

        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        backend = DemucsLocalBackend(model_name="htdemucs")
        stems = await backend.separate_stems(test_audio_5s, tmp_path)

        # Check vocal stem duration matches input (~5 seconds)
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(stems.vocals)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        info = json.loads(probe.stdout)
        duration = float(info["format"]["duration"])
        assert 4.5 < duration < 5.5, f"Expected ~5s, got {duration}s"

    @pytest.mark.asyncio
    async def test_health_check_with_real_package(self):
        """health_check reports correct state when demucs is installed."""
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        backend = DemucsLocalBackend()
        health = await backend.health_check()
        assert health["available"] is True
        assert health["loaded"] is False

    @pytest.mark.asyncio
    async def test_release_frees_model_memory(self, test_audio_5s: Path, tmp_path: Path):
        """After release(), model is unloaded."""
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        backend = DemucsLocalBackend(model_name="htdemucs")
        await backend.separate_stems(test_audio_5s, tmp_path)
        assert backend._model is not None

        backend.release()
        assert backend._model is None
