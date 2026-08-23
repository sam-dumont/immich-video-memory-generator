"""Keep a cache directory inside a size budget, oldest first.

The video cache has enforced a cap since it was written; `preview-cache/`,
`thumbnails/` and the pipeline's `previews/` never had one. On a real library
that was 9 GB of 19 unbounded, while the cache page reported thumbnails as
"x / 500 MB" -- a limit nothing applied.

Eviction is least-recently-used by mtime rather than by age alone: a cap answers
"how much disk may this cost", which is the question a user actually has, and a
file that is still being read keeps earning its place.

That last part only holds if readers refresh mtime -- otherwise this is write-
FIFO wearing an LRU label, and a caller whose working set exceeds its budget
evicts the very files it is still reading. `ThumbnailCache.get` had to be taught
to touch on read for that reason (#512); callers that pass `run_started_at` also
get told when the budget is too small to hold what they are working on.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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

    Pass `run_started_at` (a wall-clock timestamp) to have the caller's own
    working set watched: evicting anything used since the run began means the
    budget is smaller than what the run needs, and it will re-fetch what it
    just deleted. That is worth saying out loud rather than degrading quietly.
    """
    if max_bytes < 0 or not directory.exists():
        return 0

    entries = []
    total = 0
    for path in directory.rglob(pattern):
        if not path.is_file():
            continue
        with contextlib.suppress(OSError):
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size

    if total <= max_bytes:
        return 0

    freed = 0
    still_wanted = 0
    for mtime, size, path in sorted(entries):
        if total - freed <= max_bytes:
            break
        try:
            path.unlink()
        except OSError:  # noqa: PERF203 - a file that vanished is already evicted
            continue
        freed += size
        if run_started_at is not None and mtime >= run_started_at:
            still_wanted += 1

    if freed:
        logger.info(
            "Evicted %.1f MB from %s (budget %.1f MB)",
            freed / 1_000_000,
            directory.name,
            max_bytes / 1_000_000,
        )
    if still_wanted:
        logger.warning(
            "Evicted %d file(s) this run is still using from %s: the %.0f MB "
            "budget is smaller than this run's working set, so cached work is "
            "being deleted and re-fetched. Raise the budget for large runs.",
            still_wanted,
            directory.name,
            max_bytes / 1_000_000,
        )
    return freed
