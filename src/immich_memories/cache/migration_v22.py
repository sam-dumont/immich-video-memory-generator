"""v22: an asset banks one look per version, not one look total (#698).

`asset_scores` keyed on `asset_id` alone holds exactly one row per asset while
reads filter on the version in force, so editing the prompt made every banked
row unreadable and the next run overwrote it in place. Measured on one real
cache: 8375 rows, 3545 of them reachable — the rest stranded under keys nobody
asks for any more.

The key becomes `(asset_id, model_version)`. SQLite cannot alter a primary key
in place, so the table is rebuilt and every existing row copied across.

Rows written before versions existed carry NULL, and a rowid table does not
enforce NOT NULL on primary key columns — two NULL-version rows for one asset
would both insert and `INSERT OR REPLACE` would stop replacing either. They
land under `''` instead, which is a version like any other: addressable, and
unique per asset.
"""

from __future__ import annotations

import sqlite3

# The v7 shape, which nothing since has added to. Copying the intersection with
# what the database actually has keeps a cache built by an older or hand-rolled
# path from failing the whole ladder.
_COLUMNS = (
    "asset_id",
    "asset_type",
    "llm_interest",
    "llm_quality",
    "llm_emotion",
    "llm_description",
    "llm_category",
    "safe_cut_gaps",
    "metadata_score",
    "combined_score",
    "analyzed_at",
    "model_version",
)

_REBUILT_TABLE = """
    CREATE TABLE asset_scores_v22 (
        asset_id TEXT NOT NULL,
        asset_type TEXT NOT NULL,
        llm_interest REAL,
        llm_quality REAL,
        llm_emotion TEXT,
        llm_description TEXT,
        llm_category TEXT,
        safe_cut_gaps TEXT,
        metadata_score REAL NOT NULL,
        combined_score REAL NOT NULL,
        analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
        model_version TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (asset_id, model_version)
    )
"""


def migrate_asset_score_version_key(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'asset_scores'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row[1] for row in conn.execute("PRAGMA table_info(asset_scores)")}
    carried = [column for column in _COLUMNS if column in existing]
    if "asset_id" not in carried:
        return

    conn.execute("DROP TABLE IF EXISTS asset_scores_v22")
    conn.execute(_REBUILT_TABLE)
    _copy_rows(conn, carried)
    conn.execute("DROP TABLE asset_scores")
    conn.execute("ALTER TABLE asset_scores_v22 RENAME TO asset_scores")
    conn.execute("CREATE INDEX idx_asset_scores_type ON asset_scores(asset_type)")
    conn.execute("CREATE INDEX idx_asset_scores_combined ON asset_scores(combined_score DESC)")


def _copy_rows(conn: sqlite3.Connection, carried: list[str]) -> None:
    """Move every banked row across, NULL versions folded onto ``''``."""
    selected = ", ".join(
        "COALESCE(model_version, '')" if column == "model_version" else column for column in carried
    )
    order = " ORDER BY analyzed_at" if "analyzed_at" in carried else ""
    # WHY OR REPLACE: the old primary key rules out duplicate asset ids, so this
    # cannot fire on a cache this ladder built. On one it did not, losing the
    # older of two rows beats raising and leaving a real multi-GB bank unopenable.
    conn.execute(
        f"INSERT OR REPLACE INTO asset_scores_v22 ({', '.join(carried)}) "  # noqa: S608
        f"SELECT {selected} FROM asset_scores{order}"
    )
