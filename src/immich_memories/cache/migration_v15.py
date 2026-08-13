"""Schema v15: persist notification delivery health and cooldown state."""

from __future__ import annotations

import sqlite3


def migrate_notification_health(conn: sqlite3.Connection) -> None:
    """Create one durable notification-health row without storing provider details."""
    conn.execute(
        """
        CREATE TABLE notification_health (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_attempt_at TEXT,
            last_success_at TEXT,
            last_failure_at TEXT,
            failure_category TEXT,
            failure_message TEXT
        )
        """
    )
