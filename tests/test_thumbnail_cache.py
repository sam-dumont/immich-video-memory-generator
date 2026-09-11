"""Tests for thumbnail cache."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from immich_memories.cache import thumbnail_cache as thumbnail_cache_module
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
        assert stats["max_size_mb"] == 10_000.0

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
    """A budget smaller than the run's working set may overflow temporarily;
    it must not degrade clustering, burst dedup, or photo scoring (#512).
    """

    @staticmethod
    def _warnings(caplog):
        return [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_reclaiming_an_earlier_run_s_thumbnails_stays_quiet(self, tmp_path, caplog):
        """Evicting a previous run's leftovers is the budget working, not failing."""
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=0.001)
        for i in range(4):
            _set_age(cache.put(f"asset-{i}", "preview", b"x" * 600), seconds=3600)

        with caplog.at_level(logging.WARNING):
            freed = cache.enforce_budget()

        assert freed > 0  # eviction really happened; it just was not self-eviction
        assert self._warnings(caplog) == []


def test_finite_cache_still_evicts_at_the_automatic_check(tmp_path):
    cache = ThumbnailCache(tmp_path, max_size_mb=0.001)
    old_path = cache.put("existing", "preview", b"old" * 200)
    _set_age(old_path, seconds=3600)
    for number in range(199):
        cache.put(f"new-{number}", "preview", b"x" * 600)

    assert not cache.has("existing", "preview")
    assert 0 < cache.get_stats()["total_size_bytes"] <= 1000


class TestAWorkingSetLargerThanTheBudget:
    """A story-first run annotates every candidate in scope, so the thumbnail
    working set is a function of library size. Measured on a real library:
    12,159 previews averaging 315 KB = 3.92 GB, against a 500 MB default.

    The budget reclaims *previous* runs. It must never delete a preview this
    run still needs -- captions, DINOv2 heads, contact sheets and picture
    facts all read those bytes back, and a vanished preview is recorded as a
    missing fact, not re-fetched.
    """

    @staticmethod
    def _cache(tmp_path: Path, max_size_mb: float) -> ThumbnailCache:
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=max_size_mb)
        cache.begin_run()
        return cache

    def test_this_run_s_previews_survive_a_budget_they_do_not_fit(self, tmp_path):
        cache = self._cache(tmp_path, max_size_mb=0.001)  # 1 KB
        for i in range(4):
            cache.put(f"asset-{i}", "preview", b"x" * 600)  # 2.4 KB of working set

        cache.enforce_budget()

        assert all(cache.has(f"asset-{i}", "preview") for i in range(4))

    def test_an_earlier_run_s_previews_are_reclaimed_first(self, tmp_path):
        cache = self._cache(tmp_path, max_size_mb=0.001)
        _set_age(cache.put("last-run", "preview", b"x" * 600), seconds=3600)
        cache.put("this-run", "preview", b"x" * 600)

        cache.enforce_budget()

        assert not cache.has("last-run", "preview")
        assert cache.has("this-run", "preview")

    def test_a_clock_ahead_of_the_filesystem_still_spares_this_run(self, tmp_path, monkeypatch):
        """The run boundary and the mtimes it is compared against must come from
        the same clock. Filesystems that truncate timestamps to the second stamp
        a preview written a moment from now *earlier* than `time.time()` reads
        here, and the run then evicts the previews it is about to read back.
        A clock one second ahead reproduces that mismatch on any filesystem.
        """
        ahead = time.time() + 1.0
        # WHY: the system clock is the boundary this replaces; nothing else in
        # the cache reads it.
        monkeypatch.setattr(thumbnail_cache_module.time, "time", lambda: ahead)

        cache = self._cache(tmp_path, max_size_mb=0.001)
        for i in range(4):
            cache.put(f"asset-{i}", "preview", b"x" * 600)

        cache.enforce_budget()

        assert all(cache.has(f"asset-{i}", "preview") for i in range(4))

    def test_the_overflow_is_announced_once_and_names_the_setting(self, tmp_path, caplog):
        cache = self._cache(tmp_path, max_size_mb=0.001)
        for i in range(4):
            cache.put(f"asset-{i}", "preview", b"x" * 600)

        with caplog.at_level(logging.WARNING):
            cache.enforce_budget()
            cache.enforce_budget()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "thumbnail_cache_max_size_mb" in warnings[0].getMessage()


def test_the_default_budget_holds_a_real_memory_s_working_set():
    """500 MB was sized for the per-clip scorer, which only ever fetched the
    selected clips. Story-first prepares an annotation per candidate in scope:
    one run reported 10,793 candidates, and the previews already on disk
    measured 315 KB each. The default has to cover that, or every overlapping
    memory re-downloads the previews and re-captions the assets whose caption
    evidence no longer validates against bytes that are gone.
    """
    from immich_memories.config_models import CacheConfig

    measured_preview_bytes = 315_000
    candidates_in_one_memory_scope = 10_793
    working_set_mb = measured_preview_bytes * candidates_in_one_memory_scope / 1_000_000

    assert CacheConfig().thumbnail_cache_max_size_mb >= working_set_mb
