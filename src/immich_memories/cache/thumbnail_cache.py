"""File-based thumbnail cache keyed by asset ID and size."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from immich_memories.cache.disk_budget import evict_to_budget

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Simple file-based cache for Immich thumbnails."""

    # Scanning the tree on every put would make each thumbnail O(cache size).
    # Checking every so often bounds the overshoot to roughly this many files'
    # worth of data, which at thumbnail sizes is a few MB.
    _PUTS_BETWEEN_BUDGET_CHECKS = 200

    def __init__(self, cache_dir: Path, max_size_mb: float = 10_000.0) -> None:
        self.cache_dir = cache_dir
        self.max_size_mb = max_size_mb
        self._puts_since_check = 0
        self._run_started_at: float | None = None
        self._overflow_announced = False
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def begin_run(self) -> None:
        """Mark everything written or read from here on as this run's working set.

        Callers that only report or clear the cache never call this, so the
        budget stays a plain cap for them. An analysis run does: it reads its
        previews back several times -- pixels, heads, contact sheets, captions
        -- and a preview evicted between those stages is recorded as a missing
        fact rather than re-fetched.
        """
        self._run_started_at = self._filesystem_now()
        self._overflow_announced = False

    def _filesystem_now(self) -> float:
        """Now, as this filesystem would stamp a file touched right here.

        Eviction spares an entry whose mtime is at or after the run started, and
        it reads that mtime back off this same filesystem. A filesystem that
        truncates timestamps to the second stamps a preview touched a moment
        from now *earlier* than a `time.time()` taken here, so the run evicts
        the very previews it is reading. Taking the boundary from a real file
        removes the mismatch, whatever the granularity is.
        """
        marker = self.cache_dir / ".run-started"
        try:
            marker.touch()
            return marker.stat().st_mtime
        except OSError:
            return time.time()

    def _path(self, asset_id: str, size: str) -> Path:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        return self.cache_dir / subdir / f"{asset_id}_{size}.jpg"

    def get(self, asset_id: str, size: str) -> bytes | None:
        path = self._path(asset_id, size)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        # Eviction is oldest-mtime-first. Without this a thumbnail the run keeps
        # reading still looks as old as the moment it was written, so a working
        # set larger than the budget evicts its own live entries (#512).
        with contextlib.suppress(OSError):
            os.utime(path)
        return data

    def has(self, asset_id: str, size: str) -> bool:
        return self._path(asset_id, size).exists()

    def cached_ids(self, asset_ids: set[str] | list[str], size: str) -> set[str]:
        """Which of these assets have a cached thumbnail.

        Existence only: callers that just need the set were using `get_batch`
        and discarding the bytes, which reads the whole cache off disk to
        answer a question `stat` already answers.
        """
        return {asset_id for asset_id in asset_ids if self.has(asset_id, size)}

    def put(self, asset_id: str, size: str, data: bytes) -> Path:
        path = self._path(asset_id, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._puts_since_check += 1
        if self._puts_since_check >= self._PUTS_BETWEEN_BUDGET_CHECKS:
            self.enforce_budget()
        return path

    def enforce_budget(self) -> int:
        """Drop the least recently used thumbnails until the cache fits."""
        self._puts_since_check = 0
        if self.max_size_mb == float("inf"):
            return 0
        eviction = evict_to_budget(
            self.cache_dir,
            max_bytes=int(self.max_size_mb * 1_000_000),
            pattern="*.jpg",
            run_started_at=self._run_started_at,
        )
        if eviction.overflowed and not self._overflow_announced:
            self._overflow_announced = True
            logger.warning(
                "This run needs more preview thumbnails than the cache budget holds: "
                "%d file(s) are %.1f MB over the %.0f MB limit. They stay on disk for "
                "this run, but the next run reclaims them, so an overlapping memory "
                "re-downloads every preview and re-captions the assets whose banked "
                "caption failure no longer matches. Raise "
                "cache.thumbnail_cache_max_size_mb -- budget roughly 0.35 MB per "
                "candidate asset a memory's scope can reach.",
                eviction.active_files,
                eviction.overflow_bytes / 1_000_000,
                self.max_size_mb,
            )
        return eviction.freed_bytes

    def clear(self) -> int:
        """Remove all cached thumbnails. Returns count of removed files."""

        self._run_started_at = None
        count = 0
        if self.cache_dir.exists():
            for f in self.cache_dir.rglob("*.jpg"):
                f.unlink(missing_ok=True)
                count += 1
            # Clean empty subdirectories
            for d in sorted(self.cache_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    with contextlib.suppress(OSError):
                        d.rmdir()
        return count

    def get_stats(self) -> dict:
        max_size_mb = self.max_size_mb
        if not self.cache_dir.exists():
            return {"file_count": 0, "total_size_bytes": 0, "max_size_mb": max_size_mb}

        files = list(self.cache_dir.rglob("*.jpg"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "file_count": len(files),
            "total_size_bytes": total_size,
            "max_size_mb": max_size_mb,
        }
