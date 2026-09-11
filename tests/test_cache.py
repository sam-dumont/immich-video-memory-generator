"""Tests for the video analysis cache."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

try:
    from immich_memories.cache.database import (
        CachedSegment,
        CachedVideoAnalysis,
        VideoAnalysisCache,
    )
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

    def test_get_nonexistent(self, cache):
        """Should return None for nonexistent asset."""
        analysis = cache.get_analysis("nonexistent-id")
        assert analysis is None


class TestCachedSegment:
    """Tests for CachedSegment."""

    def test_duration(self):
        """Should calculate duration."""
        segment = CachedSegment(
            segment_index=0,
            start_time=5.0,
            end_time=10.0,
        )
        assert segment.duration == 5.0


class TestCachedVideoAnalysis:
    """Tests for CachedVideoAnalysis."""

    def test_get_best_segment(self):
        """Should return highest scoring segment."""
        analysis = CachedVideoAnalysis(
            asset_id="test",
            checksum=None,
            analysis_timestamp=datetime.now(),
            segments=[
                CachedSegment(segment_index=0, start_time=0, end_time=5, total_score=0.5),
                CachedSegment(segment_index=1, start_time=5, end_time=10, total_score=0.8),
                CachedSegment(segment_index=2, start_time=10, end_time=15, total_score=0.6),
            ],
        )

        best = analysis.get_best_segment()

        assert best is not None
        assert best.segment_index == 1
        assert best.total_score == 0.8

    def test_get_best_segment_empty(self):
        """Should return None when no segments."""
        analysis = CachedVideoAnalysis(
            asset_id="test",
            checksum=None,
            analysis_timestamp=datetime.now(),
        )

        assert analysis.get_best_segment() is None


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
