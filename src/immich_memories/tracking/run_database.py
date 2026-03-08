"""Database operations for run history."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from immich_memories.tracking.active_jobs_mixin import ActiveJobsMixin
from immich_memories.tracking.models import PhaseStats, RunMetadata
from immich_memories.tracking.run_queries import RunQueriesMixin, row_to_run

logger = logging.getLogger(__name__)


class RunDatabase(RunQueriesMixin, ActiveJobsMixin):
    """Database operations for pipeline run history."""

    def __init__(self, db_path: Path | None = None):
        """Initialize the run database.

        Args:
            db_path: Path to SQLite database. Uses config default if not specified.
        """
        if db_path is None:
            from immich_memories.config import get_config

            config = get_config()
            db_path = config.cache.database_path

        self.db_path = Path(db_path)

        # Ensure migrations are run (this will create the tables if needed)
        from immich_memories.cache.database import VideoAnalysisCache

        VideoAnalysisCache(db_path)  # This triggers migrations

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def save_run(self, run: RunMetadata) -> None:
        """Insert or update a run record.

        Args:
            run: The run metadata to save.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pipeline_runs (
                    run_id, created_at, completed_at, status,
                    person_name, person_id, date_range_start, date_range_end,
                    target_duration_minutes, output_path, output_size_bytes,
                    output_duration_seconds, clips_analyzed, clips_selected,
                    errors_count, system_info
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.created_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.status,
                    run.person_name,
                    run.person_id,
                    run.date_range_start.isoformat() if run.date_range_start else None,
                    run.date_range_end.isoformat() if run.date_range_end else None,
                    run.target_duration_minutes,
                    run.output_path,
                    run.output_size_bytes,
                    run.output_duration_seconds,
                    run.clips_analyzed,
                    run.clips_selected,
                    run.errors_count,
                    run.system_info.to_json() if run.system_info else None,
                ),
            )
            conn.commit()

    def save_phase_stats(self, run_id: str, stats: PhaseStats) -> None:
        """Save phase timing statistics.

        Args:
            run_id: The run ID to associate with.
            stats: The phase statistics to save.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO phase_stats (
                    run_id, phase_name, started_at, completed_at,
                    duration_seconds, items_processed, items_total,
                    errors, extra_metrics
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stats.phase_name,
                    stats.started_at.isoformat(),
                    stats.completed_at.isoformat() if stats.completed_at else None,
                    stats.duration_seconds,
                    stats.items_processed,
                    stats.items_total,
                    json.dumps(stats.errors) if stats.errors else None,
                    json.dumps(stats.extra_metrics) if stats.extra_metrics else None,
                ),
            )
            conn.commit()

    def get_run(self, run_id: str) -> RunMetadata | None:
        """Get a single run by ID.

        Args:
            run_id: The run ID to fetch.

        Returns:
            RunMetadata or None if not found.
        """
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()

            if not row:
                return None

            run = row_to_run(row)
            run.phases = self.get_phase_stats(run_id)
            return run

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its stats.

        Args:
            run_id: The run ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        with self._get_connection() as conn:
            # Phase stats are deleted via CASCADE
            cursor = conn.execute("DELETE FROM pipeline_runs WHERE run_id = ?", (run_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_run_status(
        self,
        run_id: str,
        status: str,
        completed_at: datetime | None = None,
        output_path: str | None = None,
        output_size_bytes: int | None = None,
        output_duration_seconds: float | None = None,
        clips_analyzed: int | None = None,
        clips_selected: int | None = None,
        errors_count: int | None = None,
    ) -> None:
        """Update run status and optionally other fields.

        Args:
            run_id: The run ID to update.
            status: New status.
            completed_at: Completion timestamp.
            output_path: Path to output file.
            output_size_bytes: Size of output file.
            output_duration_seconds: Duration of output video.
            clips_analyzed: Number of clips analyzed.
            clips_selected: Number of clips selected.
            errors_count: Number of errors.
        """
        with self._get_connection() as conn:
            updates = ["status = ?"]
            params: list = [status]

            if completed_at is not None:
                updates.append("completed_at = ?")
                params.append(completed_at.isoformat())

            if output_path is not None:
                updates.append("output_path = ?")
                params.append(output_path)

            if output_size_bytes is not None:
                updates.append("output_size_bytes = ?")
                params.append(output_size_bytes)

            if output_duration_seconds is not None:
                updates.append("output_duration_seconds = ?")
                params.append(output_duration_seconds)

            if clips_analyzed is not None:
                updates.append("clips_analyzed = ?")
                params.append(clips_analyzed)

            if clips_selected is not None:
                updates.append("clips_selected = ?")
                params.append(clips_selected)

            if errors_count is not None:
                updates.append("errors_count = ?")
                params.append(errors_count)

            params.append(run_id)

            conn.execute(
                f"UPDATE pipeline_runs SET {', '.join(updates)} WHERE run_id = ?",  # noqa: S608 - column names are hardcoded, values are parameterized
                params,
            )
            conn.commit()

    def mark_stale_runs_as_interrupted(self) -> int:
        """Mark any 'running' runs as 'interrupted' (startup cleanup).

        Returns:
            Number of runs marked as interrupted.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'interrupted'
                WHERE status = 'running'
                """
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Marked {count} stale run(s) as interrupted")
            return count
