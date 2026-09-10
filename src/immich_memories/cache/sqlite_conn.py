"""One SQLite connection per thread, WAL where the filesystem allows it.

The per-call connect this replaces was the thread-safety story: every call paid
mkdir + connect + DDL + close, and the default rollback journal serialized
readers behind writers under the pair-batch thread pool. Safety here comes from
OWNERSHIP instead: each thread only ever uses the connection it opened.
`check_same_thread=False` exists solely so `close()` can release every
connection from the closing thread — never so a live connection crosses one.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path


class ThreadOwnedConnections:
    """Lazy per-thread connections over one database file, closed together.

    A connection that raises is forgotten on the spot, so the next call from
    that thread reconnects — a broken handle heals instead of poisoning the
    thread. After `close()`, a generation bump makes every thread reopen
    rather than touch its stale handle: callers keep their fail-open shape
    and never see a closed connection.
    """

    def __init__(self, db_path: Path, schema: str) -> None:
        self.db_path = Path(db_path)
        self._schema = schema
        self._local = threading.local()
        self._lock = threading.Lock()
        self._all: list[sqlite3.Connection] = []
        self._generation = 0

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "generation", -1) != self._generation:
            conn = self._open()
        try:
            yield conn
        except sqlite3.Error:
            self._forget(conn)
            raise

    def _open(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        # WAL lets readers and the writer proceed together. Some filesystems
        # (network mounts) refuse it; whatever mode comes back still works.
        with contextlib.suppress(sqlite3.Error):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(self._schema)
        self._local.conn = conn
        self._local.generation = self._generation
        with self._lock:
            self._all.append(conn)
        return conn

    def _forget(self, conn: sqlite3.Connection) -> None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()
        with self._lock, contextlib.suppress(ValueError):
            self._all.remove(conn)
        self._local.conn = None

    def close(self) -> None:
        """Release every thread's connection; later calls reopen quietly."""
        with self._lock:
            connections, self._all = self._all, []
            self._generation += 1
        for conn in connections:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
