"""Keeping a cache directory inside a size budget.

Three cache directories had no cap and no TTL at all. On a real library that was
5.2 GB of preview-cache and 3.5 GB of thumbnails -- 9 of 19 GB unbounded -- while
the cache page told the user thumbnails were capped at 500 MB, a number nothing
enforced.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from immich_memories.cache.disk_budget import evict_to_budget


def _file(directory: Path, name: str, size: int, age_seconds: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = 1_000_000.0 - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_a_directory_inside_its_budget_is_left_alone(tmp_path: Path):
    kept = _file(tmp_path, "a.bin", 1000, age_seconds=100)

    freed = evict_to_budget(tmp_path, max_bytes=10_000)

    assert freed == 0
    assert kept.exists()


def test_the_oldest_files_go_first(tmp_path: Path):
    oldest = _file(tmp_path, "old.bin", 1000, age_seconds=300)
    middle = _file(tmp_path, "mid.bin", 1000, age_seconds=200)
    newest = _file(tmp_path, "new.bin", 1000, age_seconds=100)

    evict_to_budget(tmp_path, max_bytes=2000)

    assert not oldest.exists()
    assert middle.exists() and newest.exists()


def test_it_stops_as_soon_as_the_budget_is_met(tmp_path: Path):
    """Evicting more than necessary throws away work that would be reused."""
    for i in range(5):
        _file(tmp_path, f"f{i}.bin", 1000, age_seconds=500 - i * 10)

    evict_to_budget(tmp_path, max_bytes=3000)

    assert len(list(tmp_path.glob("*.bin"))) == 3


def test_it_reports_what_it_freed(tmp_path: Path):
    _file(tmp_path, "old.bin", 4000, age_seconds=300)
    _file(tmp_path, "new.bin", 1000, age_seconds=100)

    assert evict_to_budget(tmp_path, max_bytes=1000) == 4000


def test_a_missing_directory_is_not_an_error(tmp_path: Path):
    assert evict_to_budget(tmp_path / "never-created", max_bytes=1000) == 0


def test_only_matching_files_are_considered(tmp_path: Path):
    """A cache directory may hold bookkeeping that is not cache content."""
    _file(tmp_path, "old.jpg", 4000, age_seconds=300)
    manifest = _file(tmp_path, "manifest.json", 4000, age_seconds=400)

    evict_to_budget(tmp_path, max_bytes=1000, pattern="*.jpg")

    assert manifest.exists()


def test_a_caller_that_does_not_track_a_run_gets_no_self_eviction_warning(tmp_path: Path, caplog):
    """The preview directories evict on their own schedule with no run to
    speak of, so the warning is opt-in rather than "anything recent".
    """
    for name in ("a.bin", "b.bin"):
        (tmp_path / name).write_bytes(b"x" * 4000)  # written now, so mtime is now

    with caplog.at_level(logging.WARNING):
        evict_to_budget(tmp_path, max_bytes=4000)

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_nested_files_count_toward_the_budget(tmp_path: Path):
    """Caches shard into subdirectories; a per-directory view would miss most."""
    _file(tmp_path / "ab", "old.jpg", 4000, age_seconds=300)
    newest = _file(tmp_path / "cd", "new.jpg", 1000, age_seconds=100)

    freed = evict_to_budget(tmp_path, max_bytes=1000, pattern="*.jpg")

    assert freed == 4000
    assert newest.exists()


class TestThumbnailCacheStaysInsideItsBudget:
    """The cache page reported thumbnails as "x / 500 MB" while nothing applied
    that limit; the directory reached 3.5 GB on a real library.
    """

    @staticmethod
    def _cache(tmp_path: Path, max_size_mb: float):
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        return ThumbnailCache(cache_dir=tmp_path / "thumbnails", max_size_mb=max_size_mb)

    def test_the_reported_limit_is_the_configured_one(self, tmp_path: Path):
        """The number shown to the user has to be the number enforced."""
        cache = self._cache(tmp_path, max_size_mb=250.0)

        assert cache.get_stats()["max_size_mb"] == 250.0

    def test_writing_past_the_budget_evicts_the_oldest(self, tmp_path: Path):
        cache = self._cache(tmp_path, max_size_mb=0.05)  # 50 KB
        payload = b"x" * 10_000

        for i in range(20):
            cache.put(f"asset-{i:04d}", "preview", payload)
        cache.enforce_budget()

        assert cache.get_stats()["total_size_bytes"] <= 50_000

    def test_a_cache_inside_its_budget_keeps_everything(self, tmp_path: Path):
        cache = self._cache(tmp_path, max_size_mb=10.0)
        cache.put("asset-1", "preview", b"x" * 1000)

        cache.enforce_budget()

        assert cache.get("asset-1", "preview") is not None
