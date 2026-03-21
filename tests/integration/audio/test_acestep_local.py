"""Integration tests for ACE-Step local (lib mode) generation.

Runs the REAL ACE-Step pipeline on Apple Silicon / CUDA.
Model cache: ~/.cache/huggingface/hub/ (~2-8GB depending on lm_model_size)

Run: make test-integration-audio

Uses LOW QUALITY settings to avoid blowing up Linux runners:
- model_variant="turbo" (8 steps, fastest)
- lm_model_size="0.6B" (smallest LM)
- duration=10s (short)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.audio.conftest import requires_acestep

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("audio_ml")]


@requires_acestep
class TestACEStepLocalGeneration:
    """Real ACE-Step lib-mode tests — expensive, needs acestep package."""

    @pytest.mark.asyncio
    async def test_generate_produces_wav(self, tmp_path: Path):
        """Turbo variant generates a valid WAV file."""
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest

        # WHY: turbo + 0.6B = fastest possible config for CI safety
        config = ACEStepConfig(
            mode="lib",
            model_variant="turbo",
            lm_model_size="0.6B",
            use_lm=True,
            bf16=True,
        )
        backend = ACEStepBackend(config)

        request = GenerationRequest(
            prompt="upbeat happy",
            duration_seconds=10,
            output_dir=tmp_path,
        )

        result = await backend.generate(request)
        assert result.audio_path.exists()
        assert result.audio_path.stat().st_size > 1000
        assert result.backend_name.startswith("ACE-Step")

    @pytest.mark.asyncio
    async def test_is_available_lib_mode(self):
        """ACE-Step reports available when package is installed."""
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )

        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        assert await backend.is_available() is True

    @pytest.mark.asyncio
    async def test_pipeline_releases_memory(self, tmp_path: Path):
        """Pipeline memory is released after __aexit__."""
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest

        config = ACEStepConfig(
            mode="lib",
            model_variant="turbo",
            lm_model_size="0.6B",
            use_lm=False,  # Skip LM entirely for speed
            bf16=True,
        )
        backend = ACEStepBackend(config)

        request = GenerationRequest(
            prompt="calm ambient",
            duration_seconds=10,
            output_dir=tmp_path,
        )

        await backend.generate(request)
        assert backend._pipeline is not None

        await backend.__aexit__()
        assert backend._pipeline is None
