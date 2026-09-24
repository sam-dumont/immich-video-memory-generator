"""One writer at a time for a file that several runs read, change and write back.

The pipeline lock covers assembly only, so two cuts, a cut and idle fill, or the web UI and the
CLI read and rewrite the same bank files at once. A rewrite from a stale read drops whatever the
other writer added in between; holding this lock across read, merge and replace closes that gap.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold `path`'s exclusive lock for the block, waiting while another writer holds it.

    The lock lives in a hidden sibling file, never in `path` itself: the writers replace `path`
    by rename, and a lock on the replaced inode would guard nothing. It excludes other processes
    and other threads of this one alike, since each call opens its own descriptor.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.with_name(f".{path.name}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)
