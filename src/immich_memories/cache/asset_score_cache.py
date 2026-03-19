"""Cache for asset-level scores (videos and photos).

Operates on the `asset_scores` table in the shared analysis database.
Separated from VideoAnalysisCache for cohesion — this handles pre-filtering
scores while VideoAnalysisCache handles per-segment analysis results.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class AssetScoreCache:
    """Cache for asset-level scores used in cache-first LLM scoring."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get_asset_score(self, asset_id: str) -> dict | None:
        """Look up cached score for an asset. Returns None if not cached."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM asset_scores WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if row:
                return dict(row)
        return None

    def get_asset_scores_batch(self, asset_ids: list[str]) -> dict[str, dict]:
        """Look up cached scores for multiple assets at once."""
        if not asset_ids:
            return {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(asset_ids))
            rows = conn.execute(
                f"SELECT * FROM asset_scores WHERE asset_id IN ({placeholders})",  # noqa: S608
                asset_ids,
            ).fetchall()
            return {row["asset_id"]: dict(row) for row in rows}

    def save_asset_score(
        self,
        asset_id: str,
        asset_type: str,
        metadata_score: float,
        combined_score: float,
        llm_interest: float | None = None,
        llm_quality: float | None = None,
        llm_emotion: str | None = None,
        llm_description: str | None = None,
        model_version: str | None = None,
    ) -> None:
        """Save or update a cached asset score."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO asset_scores (
                    asset_id, asset_type, metadata_score, combined_score,
                    llm_interest, llm_quality, llm_emotion, llm_description,
                    analyzed_at, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                """,
                (
                    asset_id,
                    asset_type,
                    metadata_score,
                    combined_score,
                    llm_interest,
                    llm_quality,
                    llm_emotion,
                    llm_description,
                    model_version,
                ),
            )
            conn.commit()

    def get_cache_stats(self) -> dict:
        """Get cache statistics for the `cache stats` CLI command."""
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM asset_scores").fetchone()[0]
            by_type = conn.execute(
                "SELECT asset_type, COUNT(*) as cnt FROM asset_scores GROUP BY asset_type"
            ).fetchall()
            oldest = conn.execute("SELECT MIN(analyzed_at) FROM asset_scores").fetchone()[0]
            newest = conn.execute("SELECT MAX(analyzed_at) FROM asset_scores").fetchone()[0]
            with_llm = conn.execute(
                "SELECT COUNT(*) FROM asset_scores WHERE llm_interest IS NOT NULL"
            ).fetchone()[0]
        return {
            "total": total,
            "by_type": {row["asset_type"]: row["cnt"] for row in by_type},
            "with_llm": with_llm,
            "oldest": oldest,
            "newest": newest,
        }
