"""Keep a cache directory inside a size budget, oldest first.

The video cache has enforced a cap since it was written; `preview-cache/`,
`thumbnails/` and the pipeline's `previews/` never had one. On a real library
that was 9 GB of 19 unbounded, while the cache page reported thumbnails as
"x / 500 MB" -- a limit nothing applied.

Eviction is least-recently-used by mtime rather than by age alone: a cap answers
"how much disk may this cost", which is the question a user actually has, and a
file that is still being read keeps earning its place.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def evict_to_budget(directory: Path, *, max_bytes: int, pattern: str = "*") -> int:
    """Delete the least recently used files until the directory fits.

    Returns the number of bytes freed. Stops as soon as the budget is met --
    evicting past it throws away work that would have been reused.
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
    for _mtime, size, path in sorted(entries):
        if total - freed <= max_bytes:
            break
        try:
            path.unlink()
        except OSError:  # noqa: PERF203 - a file that vanished is already evicted
            continue
        freed += size

    if freed:
        logger.info(
            "Evicted %.1f MB from %s (budget %.1f MB)",
            freed / 1_000_000,
            directory.name,
            max_bytes / 1_000_000,
        )
    return freed
