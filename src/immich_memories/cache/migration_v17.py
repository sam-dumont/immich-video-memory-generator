"""Schema v17: store what was said in a segment.

SCORING_VERSION is deliberately not bumped alongside this. No scoring path reads
a transcript, so every cached score stays valid; bumping would invalidate every
scored segment in a library to record a change that cannot move a number.
"""

from __future__ import annotations

import sqlite3

_COLUMNS = (
    ("transcript", "TEXT"),
    ("transcript_language", "TEXT"),
    ("transcript_confidence", "REAL"),
)


def migrate_segment_transcripts(conn: sqlite3.Connection) -> None:
    """Add nullable transcript columns; existing segments keep NULLs."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'video_segments'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row[1] for row in conn.execute("PRAGMA table_info(video_segments)")}
    for name, column_type in _COLUMNS:
        if name in existing:
            continue
        conn.execute(
            f"ALTER TABLE video_segments ADD COLUMN {name} {column_type}"
        )  # nosemgrep: sqlalchemy-execute-raw-query — name/column_type come from _COLUMNS
