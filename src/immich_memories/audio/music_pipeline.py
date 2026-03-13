"""Multi-provider music generation pipeline.

Orchestrates music generation across multiple backends with fallback:
1. ACE-Step (couronne-01, T1000) — preferred for generation
2. MusicGen (couronne-03, GTX 1070) — fallback for generation + Demucs stems

Each component is independently enable/disable-able. If ACE-Step fails or
is unavailable, generation falls back to MusicGen. Stem separation always
uses MusicGen's Demucs endpoint (ACE-Step doesn't provide it).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)
from immich_memories.audio.music_generator_models import (
    GeneratedMusic,
    MusicGenerationResult,
    MusicStems,
    VideoTimeline,
)

logger = logging.getLogger(__name__)


class MusicPipeline:
    """Multi-backend music generation pipeline with fallback.

    Tries backends in priority order for generation, uses MusicGen
    for Demucs stem separation regardless of which backend generated.
    """

    def __init__(
        self,
        generators: list[MusicGenerator],
        stem_separator: MusicGenerator | None = None,
    ):
        """Initialize pipeline.

        Args:
            generators: Backends to try for generation, in priority order.
            stem_separator: Backend to use for stem separation (typically MusicGen).
                           If None, stems are skipped.
        """
        self._generators = generators
        self._stem_separator = stem_separator

    async def __aenter__(self):
        for gen in self._generators:
            await gen.__aenter__()
        if self._stem_separator and self._stem_separator not in self._generators:
            await self._stem_separator.__aenter__()
        return self

    async def __aexit__(self, *args):
        for gen in self._generators:
            await gen.__aexit__(*args)
        if self._stem_separator and self._stem_separator not in self._generators:
            await self._stem_separator.__aexit__(*args)

    async def generate_music_for_video(
        self,
        timeline: VideoTimeline,
        output_dir: Path,
        num_versions: int = 3,
        progress_callback: Any | None = None,
        crossfade_duration: float = 2.0,
        hemisphere: str = "north",
        memory_type: str | None = None,
    ) -> MusicGenerationResult:
        """Generate music using the first available backend, with fallback.

        Args:
            timeline: Video timeline with per-clip mood data.
            output_dir: Directory for output files.
            num_versions: Number of music versions to generate.
            progress_callback: Optional callback(version_idx, status, progress, detail).
            crossfade_duration: Crossfade between scenes (seconds).
            hemisphere: "north" or "south" for seasonal prompts.

        Returns:
            MusicGenerationResult with generated versions.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = timeline.build_scenes(hemisphere=hemisphere)
        total_duration = sum(s["duration"] for s in scenes)
        primary_mood = scenes[0]["mood"] if scenes else "calm"

        logger.info(f"Pipeline: {len(scenes)} scenes, {total_duration}s, {num_versions} versions")

        versions: list[GeneratedMusic] = []

        for i in range(num_versions):
            logger.info(f"Generating version {i + 1}/{num_versions}")

            request = GenerationRequest(
                prompt=primary_mood,
                scenes=scenes,
                duration_seconds=int(total_duration),
                variation_index=i,
                crossfade_duration=crossfade_duration,
                output_dir=output_dir,
                memory_type=memory_type,
            )

            # Try each generator in priority order
            result = await self._try_generate(request, progress_callback, i, num_versions)

            if result is None:
                logger.error(f"All backends failed for version {i + 1}")
                continue

            # Separate stems (always via MusicGen Demucs if available)
            stems = await self._try_separate_stems(result, request, progress_callback, i)

            versions.append(
                GeneratedMusic(
                    version_id=i,
                    full_mix=result.audio_path,
                    stems=stems,
                    duration=float(total_duration),
                    prompt=result.prompt,
                    mood=primary_mood,
                )
            )

        if not versions:
            raise RuntimeError("All music generation backends failed for all versions")

        return MusicGenerationResult(
            versions=versions,
            timeline=timeline,
            mood=primary_mood,
        )

    async def _try_generate(
        self,
        request: GenerationRequest,
        progress_callback: Any | None,
        version_idx: int,
        num_versions: int,
    ) -> GenerationResult | None:
        """Try each generator in priority order until one succeeds."""

        def _progress(status, progress, detail):
            if progress_callback:
                # Generation covers 0-80% of overall progress
                scaled = progress * 0.8
                progress_callback(version_idx, status, scaled, detail)

        for gen in self._generators:
            try:
                if not await gen.is_available():
                    logger.info(f"{gen.name} unavailable, trying next...")
                    continue

                logger.info(f"Generating with {gen.name}")
                return await gen.generate(request, _progress)

            except Exception:
                logger.exception(f"{gen.name} failed")
                continue

        return None

    async def _try_separate_stems(
        self,
        result: GenerationResult,
        request: GenerationRequest,
        progress_callback: Any | None,
        version_idx: int,
    ) -> MusicStems | None:
        """Attempt stem separation on the generated audio file.

        Uses MusicGen's Demucs endpoint to separate the already-generated
        audio into vocal/accompaniment stems for intelligent audio ducking.
        """
        if self._stem_separator is None:
            return None

        def _progress(status, progress, detail):
            if progress_callback:
                # Stem separation covers 80-100% of overall progress
                scaled = 80 + progress * 0.2
                progress_callback(version_idx, f"Separating stems: {status}", scaled, detail)

        try:
            if not await self._stem_separator.is_available():
                logger.warning("Stem separator unavailable, skipping")
                return None

            logger.info(f"Separating stems with {self._stem_separator.name}")

            # Use the MusicGen client's separate_stems directly on the audio file
            from immich_memories.audio.generators.musicgen_backend import MusicGenBackend

            if isinstance(self._stem_separator, MusicGenBackend):
                client = self._stem_separator._client
                if client is not None:
                    return await client.separate_stems(
                        result.audio_path,
                        output_dir=request.output_dir,
                        progress_callback=_progress,
                    )

            # Fallback: use generate_with_stems if not MusicGen
            logger.warning("Stem separator is not MusicGen, skipping")
            return None

        except Exception:
            logger.exception("Stem separation failed, continuing without stems")
            return None


def create_pipeline(app_config) -> MusicPipeline:
    """Create a MusicPipeline from the application config.

    Reads musicgen and ace_step sections from the app config to build
    the generator priority list and stem separator.

    Args:
        app_config: Application config with .musicgen and .ace_step attributes.

    Returns:
        Configured MusicPipeline ready for use with `async with`.
    """
    from immich_memories.audio.generators.factory import create_generator

    generators: list[MusicGenerator] = []
    stem_separator: MusicGenerator | None = None

    # ACE-Step as primary generator (if enabled)
    ace_step_enabled = getattr(app_config, "ace_step", None) and app_config.ace_step.enabled
    if ace_step_enabled:
        generators.append(create_generator("ace_step", app_config.ace_step))
        logger.info(f"Pipeline: ACE-Step enabled (mode={app_config.ace_step.mode})")

    # MusicGen: stem separator always, generation fallback only if ACE-Step is off
    if getattr(app_config, "musicgen", None) and app_config.musicgen.enabled:
        musicgen = create_generator("musicgen", app_config.musicgen)
        stem_separator = musicgen
        if ace_step_enabled:
            logger.info("Pipeline: MusicGen enabled (Demucs stems only)")
        else:
            generators.append(musicgen)
            logger.info("Pipeline: MusicGen enabled (generation + Demucs stems)")

    if not generators:
        raise ValueError(
            "No music generation backends enabled. "
            "Enable at least one of: musicgen.enabled, ace_step.enabled"
        )

    return MusicPipeline(generators=generators, stem_separator=stem_separator)
