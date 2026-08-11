"""SQL execution helpers for transactional schema migrations."""

from __future__ import annotations

import sqlite3


def execute_migration_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a multi-statement script without leaving the caller's transaction."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        raise sqlite3.ProgrammingError("Incomplete SQL statement in migration script")
