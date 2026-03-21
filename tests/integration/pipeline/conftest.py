"""Shared fixtures for pipeline integration tests.

Session-scoped Immich fixtures ensure clips are fetched once per test run,
and the analysis cache is populated once and reused across all test modules.
"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session-scoped Immich fixtures — fetched once, shared across all modules
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def immich_clips():
    """Broad set of clips from Immich (2025) for density budget tests.

    Returns (clips, config, client). Fetches once per session so the analysis
    cache is populated and reused across all pipeline test modules.
    """
    from datetime import date

    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    config = Config.from_yaml(Config.get_default_path())
    config.defaults.target_duration_seconds = 60
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    dr = DateRange(start=date(2025, 1, 1), end=date(2025, 12, 31))
    assets = client.get_videos_for_date_range(dr)

    if len(assets) < 10:
        pytest.skip("Need at least 10 videos in Immich for density budget test")

    clips = assets_to_clips(assets)
    logger.info(f"[session] Loaded {len(clips)} clips from Immich (2025)")
    return clips, config, client


@pytest.fixture(scope="session")
def immich_short_clips():
    """Short clips (≤15s) from Immich for generate_memory() tests.

    Returns (clips[:3], config, client). Session-scoped to avoid refetching.
    """
    from tests.integration.immich_fixtures import find_short_clips, make_immich_client

    client, config = make_immich_client()
    short = find_short_clips(client)

    if len(short) < 2:
        pytest.skip("Need at least 2 short clips (≤60s) in Immich")

    logger.info(f"[session] Found {len(short)} short clips from Immich")
    return short[:3], config, client
