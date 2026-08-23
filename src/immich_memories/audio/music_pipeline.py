"""Multi-provider music generation pipeline.

Orchestrates music generation and optional stem separation:
1. ACE-Step — generation in direct-library or API mode
2. MusicGen — generation behind ACE-Step in the fallback chain, and remote
   Demucs stems

Stem separation is decoupled from generation via the StemSeparator protocol:
- DemucsLocalBackend: in-process, no server needed (Apple Silicon / CUDA / CPU)
- MusicGenBackend: remote API with Demucs endpoint
- Auto-detected: if demucs package is installed, uses local; otherwise MusicGen API
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
    StemSeparator,
)
from immich_memories.audio.music_generator_models import (
    GeneratedMusic,
    MusicGenerationResult,
    MusicStems,
    VideoTimeline,
)

logger = logging.getLogger(__name__)


class MusicPipeline:
    """Music generation pipeline with ordered-backend fallback support.

    Tries every configured generator in order, so a backend that is enabled but
    failing costs the run its first choice rather than its music. Stem
    separation is decoupled via local Demucs or the MusicGen API.
    """

    def __init__(
        self,
        generators: list[MusicGenerator],
        stem_separator: StemSeparator | None = None,
    ):
        self._generators = generators
        self._stem_separator = stem_separator

    async def __aenter__(self):
        for gen in self._generators:
            await gen.__aenter__()
        sep = self._stem_separator
        if sep and sep not in self._generators and hasattr(sep, "__aenter__"):
            await sep.__aenter__()
        return self

    async def __aexit__(self, *args):
        for gen in self._generators:
            await gen.__aexit__(*args)
        sep = self._stem_separator
        if sep and sep not in self._generators and hasattr(sep, "__aexit__"):
            await sep.__aexit__(*args)

    async def generate_music_for_video(
        self,
        timeline: VideoTimeline,
        output_dir: Path,
        num_versions: int = 3,
        progress_callback: Any | None = None,
        crossfade_duration: float = 2.0,
        hemisphere: str = "north",
        memory_type: str | None = None,
        photo_cadence_seconds: float | None = None,
    ) -> MusicGenerationResult:
        """Generate music using the first available backend, with fallback."""
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
                photo_cadence_seconds=photo_cadence_seconds,
            )

            result = await self._try_generate(request, progress_callback, i, num_versions)

            if result is None:
                backend_names = ", ".join(g.name for g in self._generators)
                logger.error(
                    "All music backends failed for version %d/%d (tried: %s)",
                    i + 1,
                    num_versions,
                    backend_names,
                )
                continue

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
                # WHY broad: the point of a backend chain is that one backend
                # failing is not the run failing, and each backend fails in its
                # own vocabulary — a hosted one raises httpx.HTTPStatusError,
                # which is neither RuntimeError nor OSError and used to escape
                # the chain entirely. CancelledError is a BaseException and
                # still propagates.
                # No exc_info: a backend's exception text can carry its URL or
                # key, and these logs are kept deliberately quiet about that.
                logger.warning("Music backend %s failed; trying next backend", gen.name)
                continue

        return None

    async def _try_separate_stems(
        self,
        result: GenerationResult,
        request: GenerationRequest,
        progress_callback: Any | None,
        version_idx: int,
    ) -> MusicStems | None:
        """Separate stems using any StemSeparator (local Demucs or MusicGen API)."""
        if self._stem_separator is None:
            return None

        def _progress(status, progress, detail):
            if progress_callback:
                scaled = 80 + progress * 0.2
                progress_callback(version_idx, f"Separating stems: {status}", scaled, detail)

        try:
            if not await self._stem_separator.is_available():
                logger.warning("Stem separator unavailable, skipping")
                return None

            # WHY a directory per version: Demucs writes fixed stem filenames
            # (vocals.wav, drums.wav...), so three versions separating into one
            # directory left one set of stems belonging to whichever finished
            # last, silently attributed to all three.
            stem_dir = request.output_dir / f"version_{version_idx}"
            stem_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"Separating stems with {self._stem_separator.name}")
            return await self._stem_separator.separate_stems(
                result.audio_path,
                output_dir=stem_dir,
                progress_callback=_progress,
            )

        except Exception:
            # WHY broad: same reasoning as the backend chain — stems are a
            # bonus, and no way of failing to produce them should cost the
            # music that was already generated.
            # No exc_info, for the same reason as the backend chain above.
            logger.warning("Stem separation failed; continuing without stems")
            return None


def create_pipeline(app_config, *, separate_stems: bool = True) -> MusicPipeline:
    """Create a MusicPipeline from the application config.

    Reads musicgen and ace_step sections from the app config to build
    the generator priority list and stem separator.

    Stem separation priority:
    1. MusicGen API (if enabled) — established, supports 2-stem and 4-stem
    2. Local Demucs (if demucs package installed) — zero-config fallback

    ``separate_stems=False`` skips the separator entirely. Demucs is minutes of
    CPU per version, and only the UI's 4-stem ducking consumes the result; the
    CLI mix path masters the full mix instead, so it was paying for stems it
    then dropped (#499).
    """
    from immich_memories.audio.generators.factory import create_generator

    generators: list[MusicGenerator] = []
    stem_separator: StemSeparator | None = None

    # ACE-Step as primary generator (if enabled)
    ace_step_enabled = getattr(app_config, "ace_step", None) and app_config.ace_step.enabled
    if ace_step_enabled:
        generators.append(create_generator("ace_step", app_config.ace_step))
        logger.info(f"Pipeline: ACE-Step enabled (mode={app_config.ace_step.mode})")

    # MusicGen: stem separator always, and generation behind ACE-Step in the chain.
    # It used to be added as a generator only when ACE-Step was off, which left
    # `generators` one element long in every configuration and made the fallback
    # chain unreachable (#499).
    if getattr(app_config, "musicgen", None) and app_config.musicgen.enabled:
        musicgen = create_generator("musicgen", app_config.musicgen)
        if separate_stems:
            # MusicGenBackend satisfies StemSeparator (has separate_stems method)
            stem_separator = musicgen  # type: ignore[assignment]
        generators.append(musicgen)
        role = "fallback generation" if ace_step_enabled else "generation"
        logger.info(
            "Pipeline: MusicGen enabled (%s%s)",
            role,
            " + Demucs stems" if separate_stems else "",
        )

    # Auto-detect local Demucs when no MusicGen configured
    if separate_stems and stem_separator is None:
        stem_separator = _try_local_demucs()

    if not generators:
        raise ValueError(
            "No music generation backends enabled. "
            "Enable at least one of: musicgen.enabled, ace_step.enabled"
        )

    return MusicPipeline(generators=generators, stem_separator=stem_separator)


def _try_local_demucs() -> StemSeparator | None:
    """Create a local Demucs backend if the package is installed."""
    from contextlib import suppress

    with suppress(ImportError):
        from immich_memories.audio.generators.demucs_local import (
            DemucsLocalBackend,
            _is_demucs_importable,
        )

        if _is_demucs_importable():
            logger.info("Pipeline: Local Demucs detected — using for stem separation")
            return DemucsLocalBackend()

    logger.info("Pipeline: No stem separator available (install demucs or enable musicgen)")
    return None
