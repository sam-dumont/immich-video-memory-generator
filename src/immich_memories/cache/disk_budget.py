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
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
CacheEntry = tuple[float, int, Path]


def evict_to_budget(
    directory: Path,
    *,
    max_bytes: int,
    pattern: str = "*",
    run_started_at: float | None = None,
) -> int:
    """Delete the least recently used files until the directory fits.

    Returns the number of bytes freed. Stops as soon as the budget is met --
    evicting past it throws away work that would have been reused.

    Pass `run_started_at` (a wall-clock timestamp) to protect the caller's
    current working set. Older cache entries are still reclaimed first. If the
    active files alone exceed the budget, they temporarily overflow it rather
    than disappearing underneath the running analysis.
    """
    if max_bytes < 0 or not directory.exists():
        return 0

    entries, total = _cache_entries(directory, pattern)
    if total <= max_bytes:
        return 0
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
    if total - freed > max_bytes and active_kept:
        logger.warning(
            "Keeping %d active file(s) above the %s budget: the %.0f MB limit "
            "is smaller than this run's working set. They remain available for "
            "this analysis and become reclaimable on the next run.",
            active_kept,
            directory.name,
            max_bytes / 1_000_000,
        )
    return freed


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
