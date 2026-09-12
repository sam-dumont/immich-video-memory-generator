"""Tests for the video analysis cache."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

try:
    from immich_memories.cache.database import VideoAnalysisCache
except ImportError:
    pytest.skip("cache module not yet implemented", allow_module_level=True)


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def cache(temp_db_path):
    """Create a cache instance with a temporary database."""
    return VideoAnalysisCache(temp_db_path)


class TestVideoAnalysisCache:
    """Tests for VideoAnalysisCache."""

    def test_database_creation(self, cache, temp_db_path):
        """Database file should be created."""
        assert temp_db_path.exists()


class TestVideoAnalysisCacheEdgeCases:
    """Edge cases for cache operations."""

    def test_clear_empty_cache_returns_zero(self, cache):
        """Clearing an empty cache returns 0."""
        assert cache.clear_all() == 0

    def test_stats_empty_cache(self, cache):
        """Stats on empty cache return zeros."""
        stats = cache.get_stats()
        assert stats["total_videos"] == 0
        assert stats["total_segments"] == 0
