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
