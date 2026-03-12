"""MusicGen API client for AI-generated background music.

This module integrates with the MusicGen API server to:
1. Generate multiple music versions for user selection
2. Separate stems (vocals/accompaniment) for intelligent ducking
3. Handle video timeline-aware duration calculations

All data models are defined in music_generator_models.py.
The API client is defined in music_generator_client.py.
This module re-exports everything for backwards compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

# Re-export client classes for backwards compatibility
from immich_memories.audio.music_generator_client import (
    MusicGenClient,
    MusicGenClientConfig,
    MusicGenConfig,
)

# Re-export all models for backwards compatibility
from immich_memories.audio.music_generator_models import (
    SEASONAL_MOODS,
    ClipMood,
    GeneratedMusic,
    MusicGenerationResult,
    MusicStems,
    StemDuckingConfig,
    VideoTimeline,
    get_seasonal_prompt,
)

__all__ = [
    # Models
    "SEASONAL_MOODS",
    "ClipMood",
    "GeneratedMusic",
    "MusicGenerationResult",
    "MusicStems",
    "StemDuckingConfig",
    "VideoTimeline",
    "get_seasonal_prompt",
    # Client
    "MusicGenClient",
    "MusicGenClientConfig",
    "MusicGenConfig",
    # High-level functions
    "MUSIC_PROMPTS",
    "generate_music_for_video",
    "generate_music_sync",
    "_get_base_prompt",
]

logger = logging.getLogger(__name__)


# =============================================================================
# High-Level Music Generation
# =============================================================================

# Music generation parameters for variety
# Keep prompts SIMPLE but SPECIFIC - emphasize modern/upbeat sound
# Explicitly avoid classical/orchestral to prevent mellow output
MUSIC_PROMPTS = [
    "upbeat lo-fi hip hop beat, bouncy drums, warm synths, feel-good groove, no vocals",
    "modern pop electronic, punchy drums, synth bass, bright and fun, instrumental",
    "happy indie electronic, driving beat, synth melody, uplifting energy, no singing",
    "feel-good future bass, energetic drops, warm pads, joyful and bouncy, instrumental",
    "upbeat chillwave pop, groovy bassline, sparkly synths, positive vibes, no vocals",
    "modern tropical house, steel drums, bouncy beat, sunny and fun, instrumental only",
]


# Memory type-specific MusicGen prompts: picks a style that fits the preset
_MEMORY_TYPE_PROMPTS: dict[str, list[str]] = {
    "trip": [
        "modern tropical house, steel drums, bouncy beat, sunny and fun, instrumental only",
        "upbeat acoustic guitar, light percussion, carefree summer vibes, no vocals",
    ],
    "person_spotlight": [
        "warm acoustic guitar, soft percussion, gentle and heartfelt, instrumental",
        "gentle indie folk, fingerpicked guitar, warm piano, intimate feel, no vocals",
    ],
    "on_this_day": [
        "upbeat lo-fi hip hop beat, bouncy drums, warm synths, feel-good groove, no vocals",
        "nostalgic chillwave, warm analog synths, tape hiss, dreamy and reflective, instrumental",
    ],
    "year_in_review": [
        "cinematic orchestral, strings and piano, emotional and powerful, instrumental",
        "epic cinematic, building strings, triumphant brass, emotional journey, no vocals",
    ],
    "season": [
        "happy indie electronic, driving beat, synth melody, uplifting energy, no singing",
        "modern pop electronic, punchy drums, synth bass, bright and fun, instrumental",
    ],
}


def _get_base_prompt(
    variation: int = 0,
    seed: int | None = None,
    memory_type: str | None = None,
) -> str:
    """Generate a simple prompt for music generation.

    Uses simple, clear prompts to avoid artifacts from overly complex descriptions.
    If memory_type is provided, picks a prompt style that fits the preset.

    Args:
        variation: Variation index for deterministic variety
        seed: Optional random seed for reproducibility
        memory_type: Optional memory type for style-appropriate prompts

    Returns:
        Simple prompt string for MusicGen
    """
    rng = random.Random(seed + variation) if seed is not None else random.Random(variation * 42)

    # Use memory type-specific prompts when available
    if memory_type and memory_type in _MEMORY_TYPE_PROMPTS:
        return rng.choice(_MEMORY_TYPE_PROMPTS[memory_type])

    return rng.choice(MUSIC_PROMPTS)


async def generate_music_for_video(
    timeline: VideoTimeline,
    output_dir: Path,
    config: MusicGenConfig | None = None,
    progress_callback: callable | None = None,
    crossfade_duration: float = 2.0,
    hemisphere: str = "north",
    app_config: object | None = None,
    memory_type: str | None = None,
) -> MusicGenerationResult:
    """Generate multiple music versions for a video with per-clip moods.

    If app_config is provided and has ACE-Step/MusicGen enabled, uses the
    multi-provider pipeline (ACE-Step → MusicGen fallback). Otherwise falls
    back to direct MusicGen client for backwards compatibility.

    Args:
        timeline: Video timeline with per-clip mood information
        output_dir: Directory for output files
        config: MusicGen API configuration (legacy, used when app_config is None)
        progress_callback: Optional callback(version, status, progress, detail)
        crossfade_duration: Duration of crossfade between mood sections
        hemisphere: "north" or "south" for seasonal prompt generation
        app_config: Full app config with .musicgen and .ace_step sections

    Returns:
        MusicGenerationResult with multiple versions
    """
    # Use multi-provider pipeline if app_config available
    if app_config is not None and _has_pipeline_backends(app_config):
        from immich_memories.audio.music_pipeline import create_pipeline

        pipeline = create_pipeline(app_config)
        num_versions = getattr(config, "num_versions", 3) if config else 3
        async with pipeline:
            return await pipeline.generate_music_for_video(
                timeline=timeline,
                output_dir=output_dir,
                num_versions=num_versions,
                progress_callback=progress_callback,
                crossfade_duration=crossfade_duration,
                hemisphere=hemisphere,
                memory_type=memory_type,
            )

    # Legacy path: direct MusicGen client
    config = config or MusicGenConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes = timeline.build_scenes(hemisphere=hemisphere)
    total_duration = sum(s["duration"] for s in scenes)

    logger.info(f"Building soundtrack with {len(scenes)} scenes, total {total_duration}s")
    for i, scene in enumerate(scenes):
        logger.info(f"  Scene {i + 1}: {scene['mood']} ({scene['duration']}s)")

    versions: list[GeneratedMusic] = []
    primary_mood = scenes[0]["mood"] if scenes else "calm"

    async with MusicGenClient(config) as client:
        health = await client.health_check()
        logger.info(f"MusicGen API: {health['status']}, device: {health['device']}")

        for i in range(config.num_versions):
            logger.info(f"Generating version {i + 1}/{config.num_versions}")

            base_prompt = _get_base_prompt(variation=i, memory_type=memory_type)

            def version_progress(status, progress, detail, version_idx=i):
                if progress_callback:
                    progress_callback(version_idx, status, progress, detail)

            music_path = await client.generate_soundtrack(
                base_prompt=base_prompt,
                scenes=scenes,
                output_dir=output_dir,
                progress_callback=version_progress,
                crossfade_duration=crossfade_duration,
            )

            logger.info(f"Separating stems for version {i + 1}")
            stems = await client.separate_stems(
                music_path,
                output_dir=output_dir,
                progress_callback=version_progress,
            )

            versions.append(
                GeneratedMusic(
                    version_id=i,
                    full_mix=music_path,
                    stems=stems,
                    duration=float(total_duration),
                    prompt=base_prompt,
                    mood=primary_mood,
                )
            )

    return MusicGenerationResult(
        versions=versions,
        timeline=timeline,
        mood=primary_mood,
    )


def _has_pipeline_backends(app_config: object) -> bool:
    """Check if app_config has any pipeline backends enabled."""
    ace = getattr(app_config, "ace_step", None)
    mg = getattr(app_config, "musicgen", None)
    return bool((ace and ace.enabled) or (mg and mg.enabled))


# =============================================================================
# Sync Wrapper
# =============================================================================


def generate_music_sync(
    timeline: VideoTimeline,
    mood: str,
    output_dir: Path,
    config: MusicGenConfig | None = None,
    progress_callback: callable | None = None,
) -> MusicGenerationResult:
    """Synchronous wrapper for generate_music_for_video."""
    return asyncio.run(
        generate_music_for_video(
            timeline=timeline,
            mood=mood,
            output_dir=output_dir,
            config=config,
            progress_callback=progress_callback,
        )
    )
