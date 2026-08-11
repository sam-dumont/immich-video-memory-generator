"""Migration helpers for automation history normalization."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


def _timestamp_to_canonical_utc(value: str) -> str:
    """Convert local-naive or offset-aware timestamp text to canonical UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC).isoformat()


def _is_empty_people_json(value: str | None) -> bool:
    """Return whether a legacy people field contains no identities."""
    if value is None or not value.strip():
        return True
    try:
        return json.loads(value) == []
    except (TypeError, ValueError):
        return False


def migrate_automation_history(conn: sqlite3.Connection) -> None:
    """Normalize legacy timestamps and restore conservative auto-run identity."""
    timestamp_columns = {
        "pipeline_runs": ("run_id", ("created_at", "completed_at")),
        "phase_stats": ("id", ("started_at", "completed_at")),
        "automation_attempts": ("id", ("started_at", "finished_at")),
    }
    for table, (key_column, columns) in timestamp_columns.items():
        selected = ", ".join((key_column, *columns))
        for row in conn.execute(f"SELECT {selected} FROM {table}").fetchall():  # noqa: S608
            updates = {
                column: _timestamp_to_canonical_utc(row[index + 1])
                for index, column in enumerate(columns)
                if row[index + 1]
            }
            assignments = ", ".join(f"{column} = ?" for column in updates)
            if assignments:
                conn.execute(
                    f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",  # noqa: S608
                    (*updates.values(), row[0]),
                )

    category_by_type = {
        "year_in_review": "year_in_review",
        "trip": "trip",
        "multi_person": "multi_person",
        "on_this_day": "on_this_day",
        "person_spotlight": "person_spotlight",
        "monthly_highlights": "monthly_review",
    }
    for memory_type, category in category_by_type.items():
        conn.execute(
            """
            UPDATE pipeline_runs
            SET memory_category = ?
            WHERE status = 'completed' AND source = 'auto'
              AND memory_category IS NULL AND memory_type = ?
            """,
            (category, memory_type),
        )

    rows = conn.execute(
        """
        SELECT run_id, person_name, memory_people_json
        FROM pipeline_runs
        WHERE status = 'completed' AND source = 'auto'
          AND person_name IS NOT NULL
        """
    ).fetchall()
    for run_id, person_name, people_json in rows:
        if not _is_empty_people_json(people_json):
            continue
        normalized_person = " ".join(person_name.split()).casefold()
        if normalized_person:
            conn.execute(
                "UPDATE pipeline_runs SET memory_people_json = ? WHERE run_id = ?",
                (json.dumps([normalized_person]), run_id),
            )
