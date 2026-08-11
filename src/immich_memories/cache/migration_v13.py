"""Schema v13: separate local artifact completion from Immich delivery."""

from __future__ import annotations

import sqlite3


def migrate_delivery_state(conn: sqlite3.Connection) -> None:
    """Add durable delivery state to existing pipeline runs without rewriting rows."""
    conn.execute(
        "ALTER TABLE pipeline_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'not_requested'"
    )
    conn.execute(
        "ALTER TABLE pipeline_runs ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN delivery_error TEXT")
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN immich_asset_id TEXT")
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN delivery_album TEXT")
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        """
        CREATE INDEX idx_runs_pending_delivery
        ON pipeline_runs(delivery_status, source, status)
        """
    )
