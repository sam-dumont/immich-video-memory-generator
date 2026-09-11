"""SQLite-based cache for video analysis results."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.cache.database_models import CachedSegment, CachedVideoAnalysis
from immich_memories.cache.database_rows import row_to_analysis, row_to_segment
from immich_memories.cache.schema_migrator import SchemaMigrator
from immich_memories.cache.versions import SCHEMA_VERSION

if TYPE_CHECKING:
    pass


class VideoAnalysisCache:
    """SQLite-based cache for video analysis results."""

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

    # =========================================================================
    # Core CRUD Methods
    # =========================================================================

    def clear_all(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM video_analysis")
            count = cursor.rowcount
            conn.commit()
            return count

    # =========================================================================
    # Query Methods (from DatabaseQueryMixin)
    # =========================================================================

    def get_analysis(
        self,
        asset_id: str,
        include_segments: bool = True,
    ) -> CachedVideoAnalysis | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM video_analysis WHERE asset_id = ?", (asset_id,)
            ).fetchone()

            if not row:
                return None

            analysis = row_to_analysis(row)

            if include_segments:
                analysis.segments = self._load_segments(conn, asset_id)

            return analysis

    def _load_segments(self, conn: sqlite3.Connection, asset_id: str) -> list[CachedSegment]:
        rows = conn.execute(
            """
            SELECT * FROM video_segments
            WHERE asset_id = ?
            ORDER BY segment_index
        """,
            (asset_id,),
        ).fetchall()

        return [row_to_segment(row) for row in rows]

    def get_stats(self) -> dict:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM video_analysis").fetchone()[0]

            with_hash = conn.execute(
                "SELECT COUNT(*) FROM video_analysis WHERE perceptual_hash IS NOT NULL"
            ).fetchone()[0]

            total_segments = conn.execute("SELECT COUNT(*) FROM video_segments").fetchone()[0]

            oldest = conn.execute("SELECT MIN(analysis_timestamp) FROM video_analysis").fetchone()[
                0
            ]

            newest = conn.execute("SELECT MAX(analysis_timestamp) FROM video_analysis").fetchone()[
                0
            ]

            return {
                "total_videos": total,
                "videos_with_hash": with_hash,
                "total_segments": total_segments,
                "oldest_analysis": oldest,
                "newest_analysis": newest,
                "database_size_bytes": (
                    self.db_path.stat().st_size if self.db_path.exists() else 0
                ),
            }
