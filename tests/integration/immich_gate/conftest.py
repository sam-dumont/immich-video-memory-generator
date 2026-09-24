"""Fixtures for the real-Immich PR gate (`make test-immich-gate`).

The gate is a small, stable subset on purpose: connect, paged search, people,
albums, upload, and one rules-tier selection over the CC0 fixture month. The
wider real-Immich suites (pipeline/, cli/, live_photos/) stay where they are.

The Makefile starts a digest-pinned Immich, seeds it (seed.py) and points HOME
at the config the seed wrote, so `Config.get_default_path()` is the gate's own
config and never a developer's.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime

import pytest

from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.integration.immich_fixtures import has_immich, immich_unavailable

FIXTURE_MONTH = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30, 23, 59, 59))
BULK_YEAR = DateRange(start=datetime(2019, 1, 1), end=datetime(2019, 12, 31, 23, 59, 59))


@pytest.fixture(scope="session")
def gate_version() -> str:
    """The Immich major the Makefile started: 'v2' or 'v3'."""
    version = os.environ.get("IMMICH_GATE_VERSION", "")
    if version not in {"v2", "v3"}:
        pytest.fail(f"IMMICH_GATE_VERSION must be v2 or v3, got {version!r}")
    return version


@pytest.fixture(scope="session")
def gate_config() -> Config:
    if not has_immich():
        immich_unavailable(f"Immich from {Config.get_default_path()} is not reachable")
    return Config.from_yaml(Config.get_default_path())


@pytest.fixture(scope="session")
def gate_client(gate_config: Config) -> Generator[SyncImmichClient]:
    client = SyncImmichClient(base_url=gate_config.immich.url, api_key=gate_config.immich.api_key)
    yield client
    client.close()
