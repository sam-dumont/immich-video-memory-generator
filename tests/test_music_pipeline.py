"""Tests for multi-provider music generation pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
    StemSeparator,
)
from immich_memories.audio.music_generator_models import MusicStems, VideoTimeline
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


class TestMusicPipelineEdgeCases:
    """Edge cases for music pipeline."""

    @pytest.mark.asyncio
    async def test_zero_versions_raises(self, tmp_path):
        """Requesting 0 versions raises because no versions are produced."""
        gen = FakeGenerator("Gen")
        pipeline = MusicPipeline(generators=[gen])

        async with pipeline:
            with pytest.raises(RuntimeError, match="All music generation backends failed"):
                await pipeline.generate_music_for_video(
                    timeline=VideoTimeline(),
                    output_dir=tmp_path,
                    num_versions=0,
                )

    @pytest.mark.asyncio
    async def test_single_generator_failure_raises(self, tmp_path):
        """Single failing generator raises without fallback."""
        gen = FakeGenerator("Only", fail=True)
        pipeline = MusicPipeline(generators=[gen])

        async with pipeline:
            with pytest.raises(RuntimeError, match="All music generation backends failed"):
                await pipeline.generate_music_for_video(
                    timeline=VideoTimeline(),
                    output_dir=tmp_path,
                    num_versions=1,
                )


class FakeStemSeparator:
    """Fake stem separator satisfying the StemSeparator protocol."""

    def __init__(self, available: bool = True, fail: bool = False):
        self._available = available
        self._fail = fail
        self.separate_called = False

    @property
    def name(self) -> str:
        return "FakeStemSep"

    async def is_available(self) -> bool:
        return self._available

    async def separate_stems(
        self,
        audio_path: Path,
        output_dir: Path,
        progress_callback: Any | None = None,
    ) -> MusicStems:
        if self._fail:
            raise RuntimeError("Separation failed")
        self.separate_called = True
        output_dir.mkdir(parents=True, exist_ok=True)
        vocals = output_dir / "vocals.wav"
        drums = output_dir / "drums.wav"
        bass = output_dir / "bass.wav"
        other = output_dir / "other.wav"
        for p in [vocals, drums, bass, other]:
            p.write_bytes(b"RIFF" + b"\x00" * 40)
        return MusicStems(vocals=vocals, drums=drums, bass=bass, other=other)


class TestStemSeparatorProtocol:
    """Test that the pipeline works with any StemSeparator."""

    def test_fake_satisfies_protocol(self):
        sep = FakeStemSeparator()
        assert isinstance(sep, StemSeparator)

    @pytest.mark.asyncio
    async def test_stem_separation_via_protocol(self, tmp_path):
        gen = FakeGenerator("Gen")
        sep = FakeStemSeparator()
        pipeline = MusicPipeline(generators=[gen], stem_separator=sep)

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert sep.separate_called
        assert result.versions[0].stems is not None
        assert result.versions[0].stems.has_full_stems

    @pytest.mark.asyncio
    async def test_separation_failure_returns_none_stems(self, tmp_path):
        gen = FakeGenerator("Gen")
        sep = FakeStemSeparator(fail=True)
        pipeline = MusicPipeline(generators=[gen], stem_separator=sep)

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert result.versions[0].stems is None

    @pytest.mark.asyncio
    async def test_unavailable_separator_skipped(self, tmp_path):
        gen = FakeGenerator("Gen")
        sep = FakeStemSeparator(available=False)
        pipeline = MusicPipeline(generators=[gen], stem_separator=sep)

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert not sep.separate_called
        assert result.versions[0].stems is None


class TestCreatePipelineAutoDemucs:
    """Test that create_pipeline auto-detects local Demucs."""

    def test_auto_detects_local_demucs(self):
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "lib"
        config.musicgen.enabled = False

        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=True,
        ):
            pipeline = create_pipeline(config)

        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend

        assert isinstance(pipeline._stem_separator, DemucsLocalBackend)

    def test_no_demucs_no_musicgen_means_no_separator(self):
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "lib"
        config.musicgen.enabled = False

        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=False,
        ):
            pipeline = create_pipeline(config)

        assert pipeline._stem_separator is None


class TestCreatePipelineAPIMode:
    """Test pipeline wiring for Linux/GPU deployments (external API servers)."""

    def test_musicgen_api_as_stem_separator(self):
        """When musicgen enabled + ace_step enabled, MusicGen is stem separator only."""
        from immich_memories.audio.generators.musicgen_backend import MusicGenBackend
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "api"
        config.musicgen.enabled = True
        config.musicgen.base_url = "http://gpu-server:8000"
        config.musicgen.api_key = ""
        config.musicgen.timeout_seconds = 3600
        config.musicgen.num_versions = 1

        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=False,
        ):
            pipeline = create_pipeline(config)

        # MusicGen should be stem separator (not a generator — ACE-Step handles that)
        assert isinstance(pipeline._stem_separator, MusicGenBackend)
        assert len(pipeline._generators) == 1  # Only ACE-Step

    def test_musicgen_api_preferred_over_local_demucs(self):
        """When musicgen is enabled, it takes priority for stems even if demucs is installed."""
        from immich_memories.audio.generators.musicgen_backend import MusicGenBackend
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "api"
        config.musicgen.enabled = True
        config.musicgen.base_url = "http://gpu-server:8000"
        config.musicgen.api_key = ""
        config.musicgen.timeout_seconds = 3600
        config.musicgen.num_versions = 1

        # Even with demucs importable, MusicGen API takes priority
        pipeline = create_pipeline(config)
        assert isinstance(pipeline._stem_separator, MusicGenBackend)

    def test_musicgen_only_mode(self):
        """When only musicgen enabled, it handles both generation and stems."""
        from immich_memories.audio.generators.musicgen_backend import MusicGenBackend
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = False
        config.musicgen.enabled = True
        config.musicgen.base_url = "http://gpu-server:8000"
        config.musicgen.api_key = ""
        config.musicgen.timeout_seconds = 3600
        config.musicgen.num_versions = 1

        with patch(
            "immich_memories.audio.generators.demucs_local._is_demucs_importable",
            return_value=False,
        ):
            pipeline = create_pipeline(config)

        # MusicGen is BOTH generator and stem separator
        assert len(pipeline._generators) == 1
        assert isinstance(pipeline._generators[0], MusicGenBackend)
        assert isinstance(pipeline._stem_separator, MusicGenBackend)
