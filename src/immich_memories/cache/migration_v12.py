"""Schema v12: exact automation-attempt identity for pipeline runs."""

from __future__ import annotations

import sqlite3


def migrate_automation_attempt_identity(conn: sqlite3.Connection) -> None:
    """Link generated pipeline runs to the exact parent automation attempt."""
    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN automation_attempt_id TEXT")
    conn.execute(
        """
        CREATE INDEX idx_runs_automation_attempt
        ON pipeline_runs(automation_attempt_id)
        """
    )
