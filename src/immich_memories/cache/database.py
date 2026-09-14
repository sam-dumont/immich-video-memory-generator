"""SQLite connection and schema initialization for run and automation state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from immich_memories.cache.schema_migrator import SchemaMigrator
from immich_memories.cache.versions import SCHEMA_VERSION


class VideoAnalysisCache:
    """SQLite connection and schema initialization for run and automation state."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_db_exists()
        SchemaMigrator(self._get_connection).migrate_to(SCHEMA_VERSION)

    def _ensure_db_exists(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,  # busy_timeout=5000ms — retry on concurrent access
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrent access
        try:
            yield conn
        finally:
            conn.close()
