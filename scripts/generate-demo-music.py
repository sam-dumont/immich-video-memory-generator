#!/usr/bin/env python3
"""Generate ~12 ACE-Step candidate tracks for the demo video.

Usage: uv run python scripts/generate-demo-music.py
Requires: ACE-Step API server running (configured in config.yaml)
Output: docs-site/static/demo/music-candidates/
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parents[1] / "docs-site" / "static" / "demo" / "music-candidates"
DURATION = 50  # seconds — headroom for trimming to ~42s

MOODS = [
    ("cinematic", "cinematic orchestral sweeping emotional film score"),
    ("cinematic2", "cinematic piano strings gentle emotional soundtrack"),
    ("uplifting", "uplifting positive energetic pop electronic"),
    ("uplifting2", "uplifting acoustic guitar warm happy indie"),
    ("ambient", "ambient atmospheric calm dreamy electronic pad"),
    ("ambient2", "ambient minimal piano gentle reverb spacious"),
    ("minimal", "minimal clean modern electronic soft beat"),
    ("minimal2", "minimal acoustic gentle fingerpick calm"),
    ("warm", "warm cozy nostalgic acoustic folk gentle"),
    ("warm2", "warm cinematic strings tender family memories"),
    ("playful", "playful light fun bouncy electronic pop"),
    ("playful2", "playful acoustic ukulele happy cheerful bright"),
]


async def generate_track(mood_name: str, prompt: str) -> Path | None:
    from immich_memories.audio.generators.ace_step_backend import ACEStepBackend
    from immich_memories.audio.generators.base import GenerationRequest
    from immich_memories.config import get_config

    # WHY: Read ace_step config from user's config.yaml so it picks up
    # the correct mode (lib/api) and api_url.
    app_config = get_config()
    from immich_memories.audio.generators.factory import _app_config_to_ace_step

    ace_config = _app_config_to_ace_step(app_config.ace_step)
    backend = ACEStepBackend(ace_config)

    if not await backend.is_available():
        logger.error("ACE-Step API not available")
        return None

    request = GenerationRequest(
        prompt=prompt,
        scenes=[],
        duration_seconds=DURATION,
        variation_index=0,
        crossfade_duration=0,
        output_dir=OUTPUT_DIR,
    )

    result = await backend.generate(request)
    dest = OUTPUT_DIR / f"{mood_name}.wav"
    result.audio_path.rename(dest)
    logger.info(f"  ✓ {mood_name}: {dest.name}")
    return dest


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Generating {len(MOODS)} tracks to {OUTPUT_DIR}/")

    for mood_name, prompt in MOODS:
        try:
            await generate_track(mood_name, prompt)
        except Exception as e:
            logger.error(f"  ✗ {mood_name}: {e}")

    logger.info(f"\nDone! Listen to tracks in {OUTPUT_DIR}/")
    logger.info("Copy your favorite to docs-site/static/demo/demo-music.wav")


if __name__ == "__main__":
    asyncio.run(main())
