"""Schema v14: persist the last public operational phase."""

from __future__ import annotations

import sqlite3


def migrate_operational_phases(conn: sqlite3.Connection) -> None:
    """Add phase state without rewriting existing run or attempt rows."""
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN last_phase TEXT")
    conn.execute("ALTER TABLE automation_attempts ADD COLUMN last_phase TEXT")
