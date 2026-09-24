"""v25: record which source produced the film's opening title, on the run row.

The title is decided in several places and each can overrule the one before,
so a finished film does not say whether its title was typed, taken from an
album or a catalogue, written by the model, built from a trip's place, or the
template's dates. One text column holding a `TitleSource` value keeps a
template fallback from being read as the model's work.
"""

from __future__ import annotations

import sqlite3


def migrate_run_title_source(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_runs'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    if "title_source" in existing:
        return

    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN title_source TEXT")
