"""Tests for multi-provider music generation pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)
from immich_memories.audio.music_generator_models import VideoTimeline
from immich_memories.audio.music_pipeline import MusicPipeline


class FakeGenerator(MusicGenerator):
    """Fake music generator for testing."""

    def __init__(self, name: str = "Fake", available: bool = True, fail: bool = False):
        self._name = name
        self._available = available
        self._fail = fail
        self.generate_called = False

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return self._available

    async def generate(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        if self._fail:
            raise RuntimeError(f"{self._name} generation failed")
        self.generate_called = True
        # Create a fake output file
        out = request.output_dir / f"fake_{request.variation_index}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF" + b"\x00" * 40)
        return GenerationResult(
            audio_path=out,
            duration_seconds=float(request.duration_seconds),
            prompt=request.prompt,
            backend_name=self._name,
        )


class TestMusicPipeline:
    def test_init_with_generators(self):
        gen = FakeGenerator()
        pipeline = MusicPipeline(generators=[gen])
        assert pipeline._generators == [gen]

    @pytest.mark.asyncio
    async def test_first_available_backend_used(self, tmp_path):
        primary = FakeGenerator("Primary")
        fallback = FakeGenerator("Fallback")
        pipeline = MusicPipeline(generators=[primary, fallback])

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        assert primary.generate_called
        assert not fallback.generate_called

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, tmp_path):
        primary = FakeGenerator("Primary", fail=True)
        fallback = FakeGenerator("Fallback")
        pipeline = MusicPipeline(generators=[primary, fallback])

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        assert fallback.generate_called

    @pytest.mark.asyncio
    async def test_fallback_on_unavailable(self, tmp_path):
        primary = FakeGenerator("Primary", available=False)
        fallback = FakeGenerator("Fallback")
        pipeline = MusicPipeline(generators=[primary, fallback])

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        assert not primary.generate_called
        assert fallback.generate_called

    @pytest.mark.asyncio
    async def test_all_backends_fail_raises(self, tmp_path):
        gen1 = FakeGenerator("One", fail=True)
        gen2 = FakeGenerator("Two", available=False)
        pipeline = MusicPipeline(generators=[gen1, gen2])

        async with pipeline:
            with pytest.raises(RuntimeError, match="All music generation backends failed"):
                await pipeline.generate_music_for_video(
                    timeline=VideoTimeline(),
                    output_dir=tmp_path,
                    num_versions=1,
                )

    @pytest.mark.asyncio
    async def test_multiple_versions(self, tmp_path):
        gen = FakeGenerator("Gen")
        pipeline = MusicPipeline(generators=[gen])

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=3,
            )

        assert len(result.versions) == 3

    @pytest.mark.asyncio
    async def test_stem_separation_skipped_when_no_separator(self, tmp_path):
        gen = FakeGenerator("Gen")
        pipeline = MusicPipeline(generators=[gen], stem_separator=None)

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert result.versions[0].stems is None


class TestCreatePipeline:
    def test_no_backends_enabled_raises(self):
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = False
        config.musicgen.enabled = False

        with pytest.raises(ValueError, match="No music generation backends enabled"):
            create_pipeline(config)
