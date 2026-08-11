"""SQLite persistence for smart automation attempts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from immich_memories.automation.models import AutomationAttempt, AutoOutcome
from immich_memories.cache.database import VideoAnalysisCache


class AttemptAlreadyFinishedError(RuntimeError):
    """Raised when a caller tries to replace an attempt's terminal result."""


def _row_to_attempt(row: sqlite3.Row) -> AutomationAttempt:
    return AutomationAttempt(
        id=row["id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=(datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None),
        outcome=AutoOutcome(row["outcome"]),
        reason=row["reason"],
        candidate_category=row["candidate_category"],
        memory_type=row["memory_type"],
        memory_key=row["memory_key"],
        run_id=row["run_id"],
        error=row["error"],
    )


class AutomationStateStore:
    """Read and update automation attempt state without replacing start rows."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        VideoAnalysisCache(self.db_path)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def start_attempt(
        self,
        reason: str,
        *,
        candidate_category: str | None = None,
        memory_type: str | None = None,
        memory_key: str | None = None,
    ) -> AutomationAttempt:
        """Insert and return a new running attempt."""
        attempt = AutomationAttempt(
            id=str(uuid4()),
            started_at=datetime.now(tz=UTC),
            finished_at=None,
            outcome=AutoOutcome.RUNNING,
            reason=reason,
            candidate_category=candidate_category,
            memory_type=memory_type,
            memory_key=memory_key,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO automation_attempts (
                    id, started_at, finished_at, outcome, reason,
                    candidate_category, memory_type, memory_key, run_id, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.started_at.isoformat(),
                    None,
                    attempt.outcome.value,
                    attempt.reason,
                    attempt.candidate_category,
                    attempt.memory_type,
                    attempt.memory_key,
                    None,
                    None,
                ),
            )
            conn.commit()
        return attempt

    def finish_attempt(
        self,
        attempt_id: str,
        outcome: AutoOutcome,
        reason: str,
        *,
        candidate_category: str | None = None,
        memory_type: str | None = None,
        memory_key: str | None = None,
        run_id: str | None = None,
        error: str | None = None,
    ) -> AutomationAttempt:
        """Set terminal fields once.

        Raises:
            KeyError: If ``attempt_id`` does not exist.
            AttemptAlreadyFinishedError: If the attempt is already terminal.
        """
        if outcome is AutoOutcome.RUNNING:
            raise ValueError("RUNNING is not a terminal automation outcome")

        finished_at = datetime.now(tz=UTC)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE automation_attempts
                SET finished_at = ?, outcome = ?, reason = ?,
                    candidate_category = COALESCE(?, candidate_category),
                    memory_type = COALESCE(?, memory_type),
                    memory_key = COALESCE(?, memory_key),
                    run_id = COALESCE(?, run_id),
                    error = ?
                WHERE id = ? AND outcome = ?
                """,
                (
                    finished_at.isoformat(),
                    outcome.value,
                    reason,
                    candidate_category,
                    memory_type,
                    memory_key,
                    run_id,
                    error,
                    attempt_id,
                    AutoOutcome.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                existing = conn.execute(
                    "SELECT outcome FROM automation_attempts WHERE id = ?", (attempt_id,)
                ).fetchone()
                if existing is None:
                    raise KeyError(f"Unknown automation attempt: {attempt_id}")
                raise AttemptAlreadyFinishedError(
                    f"Automation attempt already finished: {attempt_id} ({existing['outcome']})"
                )
            row = conn.execute(
                "SELECT * FROM automation_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            conn.commit()

        assert row is not None
        return _row_to_attempt(row)

    def get_last_attempt(self) -> AutomationAttempt | None:
        """Return the most recently started automation attempt."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM automation_attempts
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()
        return _row_to_attempt(row) if row else None
