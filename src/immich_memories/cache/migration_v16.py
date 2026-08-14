"""Schema v16: identify the model that produced cached video semantics."""

from __future__ import annotations

import sqlite3


def migrate_video_analysis_model_version(conn: sqlite3.Connection) -> None:
    """Add nullable model identity; existing analyses remain explicitly stale."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'video_analysis'"
    ).fetchone()
    if table_exists is None:
        return

    columns = {row[1] for row in conn.execute("PRAGMA table_info(video_analysis)")}
    if "model_version" in columns:
        return

    conn.execute("ALTER TABLE video_analysis ADD COLUMN model_version TEXT")
