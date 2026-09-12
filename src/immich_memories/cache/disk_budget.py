"""Keep a cache directory inside a size budget, oldest first.

The video cache has enforced a cap since it was written; `preview-cache/`,
`thumbnails/` and the pipeline's `previews/` never had one. On a real library
that was 9 GB of 19 unbounded, while the cache page reported thumbnails as
"x / 500 MB" -- a limit nothing applied.

Eviction is least-recently-used by mtime rather than by age alone: a cap answers
"how much disk may this cost", which is the question a user actually has, and a
file that is still being read keeps earning its place.

That last part only holds if readers refresh mtime -- otherwise this is write-
FIFO wearing an LRU label. A caller whose working set exceeds its budget must
also keep those active files protected: deleting them makes a bounded cache
silently change analysis results. `run_started_at` is that protection boundary.

The overflow it reports is not logged here. One directory's overflow is a
missing setting in the user's config file, and only the caller knows which key
that is, or how often it is worth saying so.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
CacheEntry = tuple[float, int, Path]


@dataclass(frozen=True)
class Eviction:
    """What one pass reclaimed, and what it could not."""

    freed_bytes: int
    active_files: int
    overflow_bytes: int

    @property
    def overflowed(self) -> bool:
        return self.overflow_bytes > 0


def evict_to_budget(
    directory: Path,
    *,
    max_bytes: int,
    pattern: str = "*",
    run_started_at: float | None = None,
) -> Eviction:
    """Delete the least recently used files until the directory fits.

    Stops as soon as the budget is met -- evicting past it throws away work
    that would have been reused.

    Pass `run_started_at` (a wall-clock timestamp) to protect the caller's
    current working set. Older cache entries are still reclaimed first. If the
    active files alone exceed the budget, they temporarily overflow it rather
    than disappearing underneath the running analysis, and the returned
    `overflow_bytes` says by how much.
    """
    if max_bytes < 0 or not directory.exists():
        return Eviction(0, 0, 0)

    entries, total = _cache_entries(directory, pattern)
    if total <= max_bytes:
        return Eviction(0, 0, 0)
    freed, active_kept = _evict_oldest(
        entries,
        total=total,
        max_bytes=max_bytes,
        run_started_at=run_started_at,
    )

    if freed:
        logger.info(
            "Evicted %.1f MB from %s (budget %.1f MB)",
            freed / 1_000_000,
            directory.name,
            max_bytes / 1_000_000,
        )
    remaining = total - freed
    return Eviction(
        freed_bytes=freed,
        active_files=active_kept,
        overflow_bytes=remaining - max_bytes if remaining > max_bytes and active_kept else 0,
    )


def _cache_entries(directory: Path, pattern: str) -> tuple[list[CacheEntry], int]:
    entries: list[CacheEntry] = []
    total = 0
    for path in directory.rglob(pattern):
        if not path.is_file():
            continue
        with contextlib.suppress(OSError):
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
    return entries, total


def _evict_oldest(
    entries: list[CacheEntry],
    *,
    total: int,
    max_bytes: int,
    run_started_at: float | None,
) -> tuple[int, int]:
    freed = 0
    active_kept = 0
    for mtime, size, path in sorted(entries):
        if total - freed <= max_bytes:
            break
        if run_started_at is not None and mtime >= run_started_at:
            active_kept += 1
            continue
        try:
            path.unlink()
        except OSError:  # noqa: PERF203 - a file that vanished is already evicted
            continue
        freed += size
    return freed, active_kept
