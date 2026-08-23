"""Tests for thumbnail cache."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from immich_memories.cache.thumbnail_cache import ThumbnailCache


def _set_age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


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


class TestReadingAThumbnailKeepsItAlive:
    """Eviction is oldest-mtime-first. Reading has to count as use, or a run
    whose working set exceeds the budget evicts the thumbnails it is still
    using and re-fetches them (#512).
    """

    def test_a_thumbnail_read_this_run_outlives_an_older_unread_one(self, tmp_path):
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=0.001)
        payload = b"x" * 600  # two of these overflow the 1 KB budget by one file
        read_again = cache.put("kept", "preview", payload)
        never_read = cache.put("dropped", "preview", payload)
        _set_age(read_again, seconds=300)
        _set_age(never_read, seconds=100)

        cache.get("kept", "preview")
        cache.enforce_budget()

        assert cache.has("kept", "preview")
        assert not cache.has("dropped", "preview")


class TestSelfEvictionIsAnnounced:
    """A budget smaller than the run's working set degrades selection in
    silence -- clustering skips, burst dedup skips hashless photos, photo
    scores fall back to neutral. The run has to say it is happening (#512).
    """

    @staticmethod
    def _warnings(caplog):
        return [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_evicting_thumbnails_this_run_fetched_warns_once_per_pass(self, tmp_path, caplog):
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=0.001)
        for i in range(4):  # 2.4 KB against a 1 KB budget: three files have to go
            cache.put(f"asset-{i}", "preview", b"x" * 600)

        with caplog.at_level(logging.WARNING):
            cache.enforce_budget()

        assert len(self._warnings(caplog)) == 1

    def test_reclaiming_an_earlier_run_s_thumbnails_stays_quiet(self, tmp_path, caplog):
        """Evicting a previous run's leftovers is the budget working, not failing."""
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=0.001)
        for i in range(4):
            _set_age(cache.put(f"asset-{i}", "preview", b"x" * 600), seconds=3600)

        with caplog.at_level(logging.WARNING):
            freed = cache.enforce_budget()

        assert freed > 0  # eviction really happened; it just was not self-eviction
        assert self._warnings(caplog) == []
