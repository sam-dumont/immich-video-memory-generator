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
            timeout=5.0,  # busy_timeout=5000ms — retry on concurrent access
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get_asset_score(self, asset_id: str) -> dict | None:
        """The most recent look banked for an asset, whichever version wrote it.

        An asset now holds a row per model+prompt version, so this answers with
        the newest of them. Callers that need the answer a *particular* version
        gave must ask for it by version through ``get_asset_scores_batch``.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM asset_scores WHERE asset_id = ?"
                " ORDER BY analyzed_at DESC, rowid DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            if row:
                return dict(row)
        return None

    def get_asset_scores_batch(
        self,
        asset_ids: list[str],
        *,
        model_version: str | None = None,
    ) -> dict[str, dict]:
        """Look up cached scores, optionally restricted to an exact model.

        Without a version this answers with each asset's newest look, since an
        asset may now hold one per model+prompt version.
        """
        if not asset_ids:
            return {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(asset_ids))
            query = f"SELECT * FROM asset_scores WHERE asset_id IN ({placeholders})"  # noqa: S608
            params: list[str] = asset_ids.copy()
            if model_version is not None:
                query += " AND model_version = ?"
                params.append(model_version)
            # Oldest first, so the newest row is the one left standing per asset.
            query += " ORDER BY analyzed_at, rowid"
            rows = conn.execute(query, params).fetchall()
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
        """Bank a look under its version, replacing only that version's answer.

        A save under a new version leaves what earlier versions said in place —
        a prompt edit re-asks the model, it does not throw the corpus away.
        Rows saved without a version share the one empty-string version, which
        is what the pre-versioning rows migrate onto.
        """
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
                    model_version or "",
                ),
            )
            conn.commit()

    def failed_looks(self, asset_ids: list[str], *, model_version: str) -> dict[str, dict]:
        """What failed for these assets under this exact version, and how often.

        Answers `{asset_id: {"kind": ..., "attempts": ...}}` for the assets that
        have one. Whether that many attempts is enough to stop asking is the
        caller's policy, not the cache's.
        """
        if not asset_ids:
            return {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(asset_ids))
            rows = conn.execute(
                "SELECT asset_id, kind, attempts FROM asset_look_failures"  # noqa: S608
                f" WHERE asset_id IN ({placeholders}) AND model_version = ?",
                [*asset_ids, model_version],
            ).fetchall()
            return {row["asset_id"]: dict(row) for row in rows}

    def record_failed_look(self, asset_id: str, model_version: str, kind: str) -> None:
        """Bank one failed look, counting how often it has failed this way."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO asset_look_failures (
                    asset_id, model_version, kind, attempts, last_attempt_at
                ) VALUES (?, ?, ?, 1, datetime('now'))
                ON CONFLICT(asset_id, model_version) DO UPDATE SET
                    kind = excluded.kind,
                    attempts = attempts + 1,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (asset_id, model_version, kind),
            )
            conn.commit()

    def get_cache_stats(self) -> dict:
        """Statistics for the `cache stats` CLI command.

        `total` counts banked looks and `assets` counts the assets they are
        about — the two differ once a prompt version bump leaves an asset
        holding an answer from each version, which is the point of doing so.
        """
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM asset_scores").fetchone()[0]
            assets = conn.execute("SELECT COUNT(DISTINCT asset_id) FROM asset_scores").fetchone()[0]
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
            "assets": assets,
            "by_type": {row["asset_type"]: row["cnt"] for row in by_type},
            "with_llm": with_llm,
            "oldest": oldest,
            "newest": newest,
        }
