"""v24: retain operational events, including their elapsed-time samples."""

from __future__ import annotations

import sqlite3


def migrate_phase_events(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN phase_events TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        "ALTER TABLE automation_attempts ADD COLUMN phase_events TEXT NOT NULL DEFAULT '[]'"
    )
