"""Integration test: full local pipeline (ACE-Step lib → Demucs local).

Validates the end-to-end flow that runs on Sam's laptop with no API servers.
Generates music, separates stems, verifies the pipeline wires everything.

Run: make test-integration-audio

Uses LOW QUALITY settings:
- turbo variant (8 steps) + short duration (10s) to keep it fast
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.audio.conftest import requires_acestep, requires_demucs

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("audio_ml")]


@requires_acestep
@requires_demucs
class TestLocalPipelineEndToEnd:
    """Full local pipeline: ACE-Step generate → Demucs separate."""

    @pytest.mark.asyncio
    async def test_generate_then_separate(self, tmp_path: Path):
        """ACE-Step generates audio, Demucs separates it into 4 stems."""
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        # Generate with turbo (fast)
        config = ACEStepConfig(mode="lib", model_variant="turbo", bf16=True)
        backend = ACEStepBackend(config)

        request = GenerationRequest(
            prompt="happy upbeat",
            duration_seconds=10,
            output_dir=tmp_path / "gen",
        )

        result = await backend.generate(request)
        assert result.audio_path.exists()

        # Release ACE-Step before loading Demucs
        await backend.__aexit__()

        # Separate stems
        demucs = DemucsLocalBackend(model_name="htdemucs")
        stems = await demucs.separate_stems(result.audio_path, tmp_path / "stems")

        assert stems.has_full_stems
        assert stems.vocals.exists()
        assert stems.drums.exists()
        assert stems.bass.exists()
        assert stems.other.exists()

        demucs.release()

    @pytest.mark.asyncio
    async def test_music_pipeline_with_local_backends(self, tmp_path: Path):
        """MusicPipeline orchestrates ACE-Step + Demucs locally."""

        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend
        from immich_memories.audio.music_generator_models import VideoTimeline
        from immich_memories.audio.music_pipeline import MusicPipeline

        ace_config = ACEStepConfig(mode="lib", model_variant="turbo", bf16=True)
        generator = ACEStepBackend(ace_config)
        separator = DemucsLocalBackend(model_name="htdemucs")

        pipeline = MusicPipeline(generators=[generator], stem_separator=separator)

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        version = result.versions[0]
        assert version.full_mix.exists()
        assert version.stems is not None
        assert version.stems.has_full_stems

    @pytest.mark.asyncio
    async def test_structured_captions_used_in_lib_mode(self, tmp_path: Path):
        """Lib mode uses dense structured captions, not bare mood strings."""
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest

        config = ACEStepConfig(mode="lib", model_variant="turbo", bf16=True)
        backend = ACEStepBackend(config)

        request = GenerationRequest(
            prompt="happy nostalgic",
            duration_seconds=10,
            output_dir=tmp_path,
            memory_type="monthly_highlights",
        )

        result = await backend.generate(request)

        # Metadata should contain the structured caption, not just "happy nostalgic"
        assert "caption" in result.metadata
        assert "instrumental" in result.metadata["caption"].lower()
        assert result.metadata.get("infer_step") == 8  # turbo = 8

        await backend.__aexit__()


@requires_acestep
class TestCreatePipelineLocalDetection:
    """Test that create_pipeline correctly wires local backends."""

    def test_ace_step_lib_with_local_demucs(self):
        """When ace_step mode=lib and musicgen disabled, local Demucs is auto-detected."""
        from unittest.mock import MagicMock

        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "lib"
        config.musicgen.enabled = False

        # Demucs IS installed (we're in integration tests)
        pipeline = create_pipeline(config)

        assert isinstance(pipeline._stem_separator, DemucsLocalBackend)
        assert len(pipeline._generators) == 1
