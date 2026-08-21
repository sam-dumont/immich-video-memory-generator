"""Expire the session files NiceGUI persists but never cleans up.

NiceGUI writes `app.storage.user` to `storage-user-<uuid>.json` keyed by a
signed cookie, and expires only *tab* storage. A client that sends no cookie
gets a fresh uuid, so anything that dirties the user dict during an
unauthenticated request leaves a file behind permanently -- one per scanner hit
on an internet-exposed instance.

Removing the writes stops new junk accruing; this clears what is already there,
and the sessions that are simply abandoned. Age is the right axis because
NiceGUI rewrites a file whenever the session changes, so mtime is last-use, and
the auth layer already treats a session older than `session_ttl_hours` as
expired.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# General storage is app-wide state, and tab storage has NiceGUI's own 30-day
# TTL. Only the per-user files are unmanaged.
_USER_STORAGE_GLOB = "storage-user-*.json"


def sweep_expired_user_storage(directory: Path, *, ttl_hours: int) -> int:
    """Delete user-storage files untouched for longer than ttl_hours.

    Returns the number of files removed. A ttl of zero or less would expire
    every session at once, which is a config error rather than an instruction
    to log everyone out, so it sweeps nothing.
    """
    if ttl_hours <= 0 or not directory.is_dir():
        return 0

    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for path in directory.glob(_USER_STORAGE_GLOB):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1

    if removed:
        logger.info("Expired %d abandoned session file(s) from %s", removed, directory)
    return removed
