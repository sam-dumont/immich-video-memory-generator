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
    async def test_backend_fallback_log_never_contains_exception_secret(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Fallback logs are controlled messages, never raw backend exceptions."""

        class SecretFailingGenerator(FakeGenerator):
            async def generate(
                self,
                request: GenerationRequest,
                progress_callback: Any | None = None,
            ) -> GenerationResult:
                del request, progress_callback
                raise RuntimeError("backend rejected unlabeled-secret-751")

        primary = SecretFailingGenerator("Primary")
        fallback = FakeGenerator("Fallback")
        pipeline = MusicPipeline(generators=[primary, fallback])
        caplog.set_level("DEBUG")

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        assert "Music backend Primary failed; trying next backend" in caplog.text
        assert "unlabeled-secret-751" not in caplog.text

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
    async def test_missing_optional_stem_decoder_preserves_generated_music(self, tmp_path):
        """A missing local decoder must not discard a valid generated full mix."""

        class MissingDecoderSeparator(FakeStemSeparator):
            async def separate_stems(
                self,
                audio_path: Path,
                output_dir: Path,
                progress_callback: Any | None = None,
            ) -> MusicStems:
                del audio_path, output_dir, progress_callback
                raise ImportError("TorchCodec is not installed")

        pipeline = MusicPipeline(
            generators=[FakeGenerator("Gen")],
            stem_separator=MissingDecoderSeparator(),
        )

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert len(result.versions) == 1
        assert result.versions[0].full_mix.exists()
        assert result.versions[0].stems is None

    @pytest.mark.asyncio
    async def test_stem_fallback_log_never_contains_exception_secret(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Best-effort stem failure logs do not include service exception data."""

        class SecretFailingSeparator(FakeStemSeparator):
            async def separate_stems(
                self,
                audio_path: Path,
                output_dir: Path,
                progress_callback: Any | None = None,
            ) -> MusicStems:
                del audio_path, output_dir, progress_callback
                raise RuntimeError("stem service rejected unlabeled-secret-934")

        pipeline = MusicPipeline(
            generators=[FakeGenerator("Gen")],
            stem_separator=SecretFailingSeparator(),
        )
        caplog.set_level("DEBUG")

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(),
                output_dir=tmp_path,
                num_versions=1,
            )

        assert result.versions[0].stems is None
        assert "Stem separation failed; continuing without stems" in caplog.text
        assert "unlabeled-secret-934" not in caplog.text

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


class TestSeparationIsOptional:
    """Demucs is minutes of CPU. Only ask for it where the stems get used (#499)."""

    def test_the_cli_path_does_not_order_stems_it_cannot_use(self, tmp_path):
        """`auto_generate_music` returns the full mix and the mix path masters it;
        the stems it used to separate were dropped on the floor."""
        from unittest.mock import AsyncMock

        from immich_memories.config_loader import Config
        from immich_memories.generate_music import auto_generate_music

        config = Config()
        config.ace_step.enabled = True

        # WHY: replaces the ACE-Step/MusicGen generation call, which needs a GPU
        # or a running server.
        with patch(
            "immich_memories.audio.music_generator.generate_music_for_video",
            new_callable=AsyncMock,
            return_value=None,
        ) as generate:
            auto_generate_music(config, [], tmp_path, None, transition_overlap=0.0)

        assert generate.await_args.kwargs["separate_stems"] is False

    def test_a_caller_that_cannot_use_stems_gets_no_separator(self):
        from immich_memories.audio.music_pipeline import create_pipeline

        config = MagicMock()
        config.ace_step.enabled = True
        config.ace_step.mode = "api"
        config.musicgen.enabled = True
        config.musicgen.base_url = "http://gpu-server:8000"
        config.musicgen.api_key = ""
        config.musicgen.timeout_seconds = 3600
        config.musicgen.num_versions = 1

        pipeline = create_pipeline(config, separate_stems=False)

        assert pipeline._stem_separator is None
        # Turning stems off must not cost the generator behind ACE-Step.
        assert len(pipeline._generators) == 2


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
        """When musicgen enabled + ace_step enabled, MusicGen supplies the stems."""
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

        assert isinstance(pipeline._stem_separator, MusicGenBackend)
        # ACE-Step leads; MusicGen sits behind it in the chain (see the fallback
        # test below) as well as supplying stems.
        assert len(pipeline._generators) == 2

    def test_musicgen_is_also_the_generation_fallback_behind_ace_step(self):
        """The chain existed but was never wired: with both enabled the factory
        built a one-element list, so a failing ACE-Step fell straight through to
        a bundled track instead of to the configured generator (#499)."""
        from immich_memories.audio.generators.ace_step_backend import ACEStepBackend
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

        pipeline = create_pipeline(config)

        assert [type(g) for g in pipeline._generators] == [ACEStepBackend, MusicGenBackend]

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


class TestBackendChainSurvivesAnyFailure:
    """A chain exists so one backend failing is not the run failing.

    It caught only RuntimeError and OSError, so a hosted backend raising
    httpx.HTTPStatusError — neither of those — escaped the chain and took the
    run with it, without ever trying the next backend.
    """

    async def test_an_http_error_falls_through_to_the_next_backend(self, tmp_path: Path) -> None:
        import httpx

        class HttpFailingGenerator(FakeGenerator):
            async def generate(self, request: Any, progress_callback: Any = None) -> Any:
                del request, progress_callback
                raise httpx.HTTPStatusError(
                    "502 from the music service",
                    request=httpx.Request("POST", "http://example.invalid/generate"),
                    response=httpx.Response(502),
                )

        pipeline = MusicPipeline(
            generators=[HttpFailingGenerator("Hosted"), FakeGenerator("Local")],
            stem_separator=None,
        )

        async with pipeline:
            result = await pipeline.generate_music_for_video(
                timeline=VideoTimeline(), output_dir=tmp_path, num_versions=1
            )

        assert len(result.versions) == 1, "the second backend should have produced the music"

    async def test_each_version_separates_into_its_own_directory(self, tmp_path: Path) -> None:
        """Demucs writes fixed stem filenames, so a shared directory loses versions."""
        seen: list[Path] = []

        class RecordingSeparator(FakeStemSeparator):
            async def separate_stems(
                self,
                audio_path: Path,
                output_dir: Path,
                progress_callback: Any | None = None,
            ) -> MusicStems:
                del progress_callback
                seen.append(output_dir)
                return await FakeStemSeparator.separate_stems(self, audio_path, output_dir)

        pipeline = MusicPipeline(
            generators=[FakeGenerator("Gen")], stem_separator=RecordingSeparator()
        )

        async with pipeline:
            await pipeline.generate_music_for_video(
                timeline=VideoTimeline(), output_dir=tmp_path, num_versions=3
            )

        assert len(seen) >= 2, "expected a separation per version"
        assert len(seen) == len(set(seen)), f"versions shared a stem directory: {seen}"
