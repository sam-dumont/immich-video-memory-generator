"""Shared Immich test fixtures with smart asset discovery.

Finds short, simple test clips dynamically from any Immich library.
Uses well-known date ranges as hints but falls back progressively
so tests work with any user's data.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

logger = logging.getLogger(__name__)

# Preferred date ranges, tried in order. The first match wins.
# Rationale: Jan 2025 = quiet month (static-ish data for Sam),
# but any user's library will have SOMETHING in these windows.
_SHORT_CLIP_RANGES = [
    (date(2025, 1, 1), date(2025, 1, 31)),  # Quiet month — short home clips
    (date(2024, 12, 1), date(2025, 2, 28)),  # Widen to winter
    (date(2024, 6, 1), date(2024, 12, 31)),  # Second half 2024
    (date(2024, 1, 1), date(2025, 12, 31)),  # Full 2 years — last resort
]

# For trip detection: look for a cluster of assets in a short window (max 4 days)
_TRIP_HINT_RANGES = [
    (date(2025, 5, 1), date(2025, 5, 31)),  # May 2025 — Ostende trip for Sam
    (date(2025, 6, 1), date(2025, 8, 31)),  # Summer 2025
    (date(2024, 6, 1), date(2024, 8, 31)),  # Summer 2024
    (date(2024, 1, 1), date(2025, 12, 31)),  # Full range — last resort
]

MAX_CLIP_DURATION = 15  # 15s max — keeps downloads small and tests fast
MIN_CLIPS_NEEDED = 2


def find_short_clips(
    client,
    min_count: int = MIN_CLIPS_NEEDED,
    max_duration: float = MAX_CLIP_DURATION,
    max_return: int = 3,
):
    """Find short video clips from Immich, trying preferred date ranges."""
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    for start, end in _SHORT_CLIP_RANGES:
        date_range = DateRange(start=start, end=end)
        assets = client.get_videos_for_date_range(date_range)

        if not assets:
            continue

        clips = assets_to_clips(assets)
        short = [c for c in clips if (c.duration_seconds or 0) <= max_duration]
        # Sort by duration (shortest first) to avoid downloading 250MB 4K clips
        short.sort(key=lambda c: c.duration_seconds or 0)

        if len(short) >= min_count:
            logger.info(f"Found {len(short)} short clips (≤{max_duration}s) in {start} → {end}")
            return short[:max_return]

    return []


def find_trip_clips(
    client,
    max_trip_days: int = 4,
    min_clips: int = 2,
    max_duration: float = MAX_CLIP_DURATION,
):
    """Find clips from a short trip (clustered assets within a few days).

    Scans preferred date ranges for a dense cluster of videos that looks
    like a trip — multiple clips within max_trip_days.
    """
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    for start, end in _TRIP_HINT_RANGES:
        date_range = DateRange(start=start, end=end)
        assets = client.get_videos_for_date_range(date_range)

        if not assets:
            continue

        clips = assets_to_clips(assets)
        short = [c for c in clips if (c.duration_seconds or 0) <= max_duration]

        if len(short) < min_clips:
            continue

        # Sort by date and look for a dense cluster
        short.sort(key=lambda c: c.asset.file_created_at or date.min)

        best_cluster = _find_densest_cluster(short, max_trip_days)
        if best_cluster and len(best_cluster) >= min_clips:
            first = best_cluster[0].asset.file_created_at
            last = best_cluster[-1].asset.file_created_at
            logger.info(
                f"Found trip cluster: {len(best_cluster)} clips "
                f"from {first} → {last} (in {start} → {end})"
            )
            return best_cluster

    return []


def _find_densest_cluster(clips, max_days: int):
    """Sliding window: find the densest group of clips within max_days."""
    from datetime import timedelta

    if not clips:
        return []

    best = []
    for i, start_clip in enumerate(clips):
        start_date = start_clip.asset.file_created_at
        if not start_date:
            continue
        window_end = start_date + timedelta(days=max_days)
        cluster = [
            c
            for c in clips[i:]
            if c.asset.file_created_at and c.asset.file_created_at <= window_end
        ]
        if len(cluster) > len(best):
            best = cluster

    return best


def make_immich_client(target_duration_seconds: float = 60.0):
    """Create a SyncImmichClient + config from user's config, or skip.

    Caps target_duration_seconds to keep integration tests fast (default 60s).
    """
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config

    config = Config.from_yaml(Config.get_default_path())
    if not config.immich.url or not config.immich.api_key:
        pytest.skip("Immich not configured")

    # Cap duration so tests don't generate 3+ minute videos
    config.defaults.target_duration_seconds = target_duration_seconds

    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    # Verify connectivity with a lightweight request
    try:
        import httpx

        resp = httpx.get(f"{config.immich.url}/api/server/ping", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip("Immich not reachable")

    return client, config
