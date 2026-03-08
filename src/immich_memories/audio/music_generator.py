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


def _get_base_prompt(variation: int = 0, seed: int | None = None) -> str:
    """Generate a simple prompt for music generation.

    Uses simple, clear prompts to avoid artifacts from overly complex descriptions.

    Args:
        variation: Variation index for deterministic variety
        seed: Optional random seed for reproducibility

    Returns:
        Simple prompt string for MusicGen
    """
    # Use seed for reproducibility if provided
    rng = random.Random(seed + variation) if seed is not None else random.Random(variation * 42)
    return rng.choice(MUSIC_PROMPTS)


async def generate_music_for_video(
    timeline: VideoTimeline,
    output_dir: Path,
    config: MusicGenConfig | None = None,
    progress_callback: callable | None = None,
    crossfade_duration: float = 2.0,
    hemisphere: str = "north",
) -> MusicGenerationResult:
    """Generate multiple music versions for a video with per-clip moods.

    Uses the soundtrack endpoint to generate music that transitions
    between moods matching each clip in the video.

    Args:
        timeline: Video timeline with per-clip mood information
        output_dir: Directory for output files
        config: MusicGen API configuration
        progress_callback: Optional callback(version, status, progress, detail)
        crossfade_duration: Duration of crossfade between mood sections
        hemisphere: "north" or "south" for seasonal prompt generation

    Returns:
        MusicGenerationResult with multiple versions
    """
    config = config or MusicGenConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build scenes from timeline (handles title, transitions, ending, buffer, seasonal prompts)
    scenes = timeline.build_scenes(hemisphere=hemisphere)
    total_duration = sum(s["duration"] for s in scenes)

    logger.info(f"Building soundtrack with {len(scenes)} scenes, total {total_duration}s")
    for i, scene in enumerate(scenes):
        logger.info(f"  Scene {i + 1}: {scene['mood']} ({scene['duration']}s)")

    versions: list[GeneratedMusic] = []

    # Determine primary mood for result (most common or first)
    primary_mood = scenes[0]["mood"] if scenes else "calm"

    async with MusicGenClient(config) as client:
        # Check API health
        health = await client.health_check()
        logger.info(f"MusicGen API: {health['status']}, device: {health['device']}")

        for i in range(config.num_versions):
            logger.info(f"Generating version {i + 1}/{config.num_versions}")

            base_prompt = _get_base_prompt(variation=i)

            def version_progress(status, progress, detail, version_idx=i):
                if progress_callback:
                    progress_callback(version_idx, status, progress, detail)

            # Always use soundtrack endpoint for mood transitions
            music_path = await client.generate_soundtrack(
                base_prompt=base_prompt,
                scenes=scenes,
                output_dir=output_dir,
                progress_callback=version_progress,
                crossfade_duration=crossfade_duration,
            )

            # Separate stems for intelligent ducking
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
