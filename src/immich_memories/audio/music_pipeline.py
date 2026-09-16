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
import math
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
    StemSeparator,
)
from immich_memories.audio.mastering import master_music_track
from immich_memories.audio.mixer import assemble_music
from immich_memories.audio.music_generator_models import (
    GeneratedMusic,
    MusicGenerationResult,
    MusicStems,
    VideoTimeline,
)

if TYPE_CHECKING:
    from immich_memories.audio.mood_analyzer import VideoMood
    from immich_memories.audio.track_quality import TrackQuality

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
        *,
        block_seconds: int = 120,
        max_blocks: int = 3,
    ):
        self._generators = generators
        self._stem_separator = stem_separator
        self._block_seconds = block_seconds
        self._max_blocks = max_blocks

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
        *,
        mood_detail: VideoMood | None = None,
        quality_gate: Callable[[Path], TrackQuality | None] | None = None,
    ) -> MusicGenerationResult:
        """Generate music using the first available backend, with fallback.

        ``mood_detail`` carries the reader's full music judgment (energy, tempo,
        genre yields) to backends that can use richer prompts than a bare mood
        word. ``quality_gate`` turns the version loop into a bounded regenerate:
        each take's full mix is scored once it is mastered, the loop stops early
        as soon as a take is not flagged, and stems are separated only for the
        best-scored take. Without it the loop generates every version and
        separates stems for each, which is what the UI preview needs.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = timeline.build_scenes(hemisphere=hemisphere)
        total_duration = sum(s["duration"] for s in scenes)
        primary_mood = scenes[0]["mood"] if scenes else "calm"

        logger.info(f"Pipeline: {len(scenes)} scenes, {total_duration}s, {num_versions} versions")

        if quality_gate is not None:
            return await self._generate_gated(
                timeline=timeline,
                scenes=scenes,
                total_duration=total_duration,
                primary_mood=primary_mood,
                output_dir=output_dir,
                num_versions=num_versions,
                progress_callback=progress_callback,
                crossfade_duration=crossfade_duration,
                memory_type=memory_type,
                photo_cadence_seconds=photo_cadence_seconds,
                mood_detail=mood_detail,
                quality_gate=quality_gate,
            )

        versions: list[GeneratedMusic] = []

        for i in range(num_versions):
            logger.info(f"Generating version {i + 1}/{num_versions}")

            made = await self._generate_full_mix(
                scenes=scenes,
                primary_mood=primary_mood,
                total_duration=total_duration,
                output_dir=output_dir,
                crossfade_duration=crossfade_duration,
                memory_type=memory_type,
                photo_cadence_seconds=photo_cadence_seconds,
                mood_detail=mood_detail,
                candidate_index=i,
                num_versions=num_versions,
                progress_callback=progress_callback,
            )
            if made is None:
                backend_names = ", ".join(g.name for g in self._generators)
                logger.error(
                    "All music backends failed for version %d/%d (tried: %s)",
                    i + 1,
                    num_versions,
                    backend_names,
                )
                continue

            result, request = made
            stems = await self._try_separate_stems(result, request, progress_callback, i)

            versions.append(
                GeneratedMusic(
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

    async def _generate_gated(
        self,
        timeline: VideoTimeline,
        scenes: list[dict[str, Any]],
        total_duration: float,
        primary_mood: str,
        output_dir: Path,
        num_versions: int,
        progress_callback: Any | None,
        crossfade_duration: float,
        memory_type: str | None,
        photo_cadence_seconds: float | None,
        mood_detail: VideoMood | None,
        quality_gate: Callable[[Path], TrackQuality | None],
    ) -> MusicGenerationResult:
        """Bounded regenerate: score each take, stop early, separate the winner."""

        candidates: list[tuple[GenerationRequest, GenerationResult, TrackQuality | None]] = []

        for i in range(num_versions):
            logger.info(f"Generating version {i + 1}/{num_versions}")

            made = await self._generate_full_mix(
                scenes=scenes,
                primary_mood=primary_mood,
                total_duration=total_duration,
                output_dir=output_dir,
                crossfade_duration=crossfade_duration,
                memory_type=memory_type,
                photo_cadence_seconds=photo_cadence_seconds,
                mood_detail=mood_detail,
                candidate_index=i,
                num_versions=num_versions,
                progress_callback=progress_callback,
            )
            if made is None:
                continue

            result, request = made
            quality = quality_gate(result.audio_path)
            candidates.append((request, result, quality))
            logger.info(
                "Version %d scored %s",
                i + 1,
                "no verdict"
                if quality is None
                else f"periodicity={quality.periodicity:.2f} "
                f"spikiness={quality.spikiness:.2f} flagged={quality.flagged}",
            )

            if quality is not None and not quality.flagged:
                break  # accepted; do not spend further regenerations

        if not candidates:
            raise RuntimeError("All music generation backends failed for all versions")

        request, result, quality = _best_candidate(candidates)
        stems = await self._try_separate_stems(result, request, progress_callback, 0)

        versions = [
            GeneratedMusic(
                full_mix=result.audio_path,
                stems=stems,
                duration=float(total_duration),
                prompt=result.prompt,
                mood=primary_mood,
            )
        ]

        return MusicGenerationResult(
            versions=versions,
            timeline=timeline,
            mood=primary_mood,
            selected_version=0,
        )

    async def _generate_full_mix(
        self,
        *,
        scenes: list[dict[str, Any]],
        primary_mood: str,
        total_duration: float,
        output_dir: Path,
        crossfade_duration: float,
        memory_type: str | None,
        photo_cadence_seconds: float | None,
        mood_detail: VideoMood | None,
        candidate_index: int,
        num_versions: int,
        progress_callback: Any | None,
    ) -> tuple[GenerationResult, GenerationRequest] | None:
        """One mastered full-length mix (and its request), pre-stem.

        Up to ``block_seconds`` this is a single take at the video length. Longer,
        one long take reads as a metronomic ramble, so instead up to ``max_blocks``
        distinct takes share the caption (fresh seed each, so same genre/key but
        different music) and are folded together with crossfades, then tiled to the
        full length. The result is mastered once, so stems and ducking see a
        balanced track.
        """
        if total_duration <= self._block_seconds:
            request = GenerationRequest(
                prompt=primary_mood,
                scenes=scenes,
                duration_seconds=int(total_duration),
                variation_index=candidate_index,
                crossfade_duration=crossfade_duration,
                output_dir=output_dir,
                memory_type=memory_type,
                photo_cadence_seconds=photo_cadence_seconds,
                mood_detail=mood_detail,
            )
            result = await self._try_generate(
                request, progress_callback, candidate_index, num_versions
            )
            if result is None:
                return None
            result.audio_path = master_music_track(
                result.audio_path, output_dir / f"mastered_version_{candidate_index}.wav"
            )
            return result, request

        n_blocks = min(
            self._max_blocks,
            max(1, int(math.ceil(total_duration / self._block_seconds))),
        )
        blocks: list[Path] = []
        prompt = primary_mood
        backend_name = ""
        for b in range(n_blocks):
            block_request = GenerationRequest(
                prompt=primary_mood,
                scenes=scenes,
                duration_seconds=self._block_seconds,
                variation_index=b,
                crossfade_duration=crossfade_duration,
                output_dir=output_dir,
                memory_type=memory_type,
                photo_cadence_seconds=photo_cadence_seconds,
                mood_detail=mood_detail,
            )
            block_result = await self._try_generate(block_request, progress_callback, b, n_blocks)
            if block_result is None:
                logger.warning("Music block %d/%d failed; skipping", b + 1, n_blocks)
                continue
            blocks.append(block_result.audio_path)
            prompt = block_result.prompt or prompt
            backend_name = block_result.backend_name or backend_name
            logger.info("Music block %d/%d ready", len(blocks), n_blocks)

        if not blocks:
            return None

        chained = output_dir / f"chained_version_{candidate_index}.wav"
        assemble_music(blocks, total_duration, chained, crossfade_seconds=crossfade_duration)
        mastered = master_music_track(
            chained, output_dir / f"mastered_version_{candidate_index}.wav"
        )

        request = GenerationRequest(
            prompt=primary_mood,
            scenes=scenes,
            duration_seconds=int(total_duration),
            variation_index=candidate_index,
            crossfade_duration=crossfade_duration,
            output_dir=output_dir,
            memory_type=memory_type,
            photo_cadence_seconds=photo_cadence_seconds,
            mood_detail=mood_detail,
        )
        result = GenerationResult(
            audio_path=mastered,
            duration_seconds=float(total_duration),
            prompt=prompt,
            backend_name=backend_name,
        )
        return result, request

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


def _candidate_rank(
    candidate: tuple[GenerationRequest, GenerationResult, TrackQuality | None],
) -> float:
    """Degeneracy for ranking, with an unscorable take ranked worst."""
    quality = candidate[2]
    return quality.score if quality is not None else 1.0


def _best_candidate(
    candidates: list[tuple[GenerationRequest, GenerationResult, TrackQuality | None]],
) -> tuple[GenerationRequest, GenerationResult, TrackQuality | None]:
    """The least tick-like take, falling back to the first when none can be scored."""
    scored = [c for c in candidates if c[2] is not None]
    pool = scored if scored else candidates
    return min(pool, key=_candidate_rank)


def create_pipeline(app_config, *, separate_stems: bool = True) -> MusicPipeline:
    """Create a MusicPipeline from the application config.

    Reads musicgen and ace_step sections from the app config to build
    the generator priority list and stem separator.

    Stem separation priority:
    1. MusicGen API (if enabled) — established, supports 2-stem and 4-stem
    2. Local Demucs (if demucs package installed) — zero-config fallback

    ``separate_stems=False`` skips the separator for callers that only need a
    full track. CLI and UI generation retain stems for the final mix.
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

    return MusicPipeline(
        generators=generators,
        stem_separator=stem_separator,
        block_seconds=_block_seconds(app_config),
        max_blocks=_max_blocks(app_config),
    )


def _block_seconds(app_config) -> int:
    """The longest single take before auto mode chains distinct takes."""
    audio = getattr(app_config, "audio", None)
    value = getattr(audio, "music_block_seconds", None)
    return value if isinstance(value, int) and value > 0 else 120


def _max_blocks(app_config) -> int:
    """Distinct takes to chain for a video longer than the block length."""
    audio = getattr(app_config, "audio", None)
    value = getattr(audio, "max_music_blocks", None)
    return value if isinstance(value, int) and value > 0 else 3


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
