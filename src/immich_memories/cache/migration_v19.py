"""v19: store the run's target duration in seconds, not minutes (#411).

`target_duration_minutes` floors a 25 s target to 0, and the read path's
`(0 or 10) * 60` turned that into the 600 s preset default — run_metadata
claimed 10 minutes for every sub-minute run. Seconds are the unit every
producer already uses; old rows backfill from minutes.
"""

from __future__ import annotations

import sqlite3


def migrate_target_duration_seconds(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_runs'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    if "target_duration_seconds" in existing:
        return

    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN target_duration_seconds INTEGER")
    # WHY the column check: minimal fixture databases (and any schema old
    # enough) may lack the minutes column entirely; they get the old default.
    if "target_duration_minutes" in existing:
        conn.execute(
            "UPDATE pipeline_runs SET target_duration_seconds ="
            " COALESCE(target_duration_minutes, 10) * 60"
        )
    else:
        conn.execute("UPDATE pipeline_runs SET target_duration_seconds = 600")
