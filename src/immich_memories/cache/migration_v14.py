"""Schema v14: persist the latest user-facing pipeline phase."""

from __future__ import annotations

import sqlite3


def migrate_operational_phase(conn: sqlite3.Connection) -> None:
    """Add nullable phase facts without rewriting runs or attempts."""
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN last_phase TEXT")
    conn.execute("ALTER TABLE automation_attempts ADD COLUMN last_phase TEXT")
