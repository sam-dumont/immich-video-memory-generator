"""Tests for thumbnail cache."""

from __future__ import annotations

import pytest

from immich_memories.cache.thumbnail_cache import ThumbnailCache


@pytest.fixture
def cache_dir(tmp_path):
    """Provide a temporary cache directory."""
    return tmp_path / "thumbnails"


@pytest.fixture
def cache(cache_dir):
    """Create a ThumbnailCache with temp directory."""
    return ThumbnailCache(cache_dir=cache_dir)


class TestThumbnailCache:
    """Tests for ThumbnailCache."""

    def test_creates_directory(self, cache, cache_dir):
        """Cache creates its directory on init."""
        assert cache_dir.exists()

    def test_get_stats_empty(self, cache):
        """Empty cache reports zero files and size."""
        stats = cache.get_stats()
        assert stats["file_count"] == 0
        assert stats["total_size_bytes"] == 0
        assert stats["max_size_mb"] == 500.0

    def test_put_and_get(self, cache):
        """Stored thumbnail can be retrieved."""
        data = b"\xff\xd8\xff\xe0fake-jpeg-data"
        cache.put("asset-123", "preview", data)
        assert cache.get("asset-123", "preview") == data

    def test_get_miss(self, cache):
        """Missing thumbnail returns None."""
        assert cache.get("nonexistent", "preview") is None

    def test_get_batch(self, cache):
        """Batch retrieval returns only cached items."""
        cache.put("a1", "preview", b"data-a1")
        cache.put("a2", "preview", b"data-a2")
        result = cache.get_batch(["a1", "a2", "a3"], "preview")
        assert set(result.keys()) == {"a1", "a2"}

    def test_clear(self, cache):
        """Clear removes all thumbnails and returns count."""
        cache.put("a1", "preview", b"data1")
        cache.put("a2", "thumbnail", b"data2")
        count = cache.clear()
        assert count == 2
        assert cache.get("a1", "preview") is None

    def test_get_stats_with_files(self, cache):
        """Stats reflect stored files."""
        cache.put("a1", "preview", b"x" * 1000)
        cache.put("a2", "preview", b"y" * 500)
        stats = cache.get_stats()
        assert stats["file_count"] == 2
        assert stats["total_size_bytes"] == 1500
