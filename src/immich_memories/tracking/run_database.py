"""Database operations for run history."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from immich_memories.tracking.models import PhaseStats, RunMetadata, SystemInfo

logger = logging.getLogger(__name__)


class RunDatabase:
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

            run = self._row_to_run(row)
            run.phases = self.get_phase_stats(run_id)
            return run

    def _row_to_run(self, row: sqlite3.Row) -> RunMetadata:
        """Convert database row to RunMetadata."""
        system_info = None
        if row["system_info"]:
            system_info = SystemInfo.from_json(row["system_info"])

        return RunMetadata(
            run_id=row["run_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            status=row["status"],
            person_name=row["person_name"],
            person_id=row["person_id"],
            date_range_start=(
                date.fromisoformat(row["date_range_start"]) if row["date_range_start"] else None
            ),
            date_range_end=(
                date.fromisoformat(row["date_range_end"]) if row["date_range_end"] else None
            ),
            target_duration_minutes=row["target_duration_minutes"] or 10,
            output_path=row["output_path"],
            output_size_bytes=row["output_size_bytes"] or 0,
            output_duration_seconds=row["output_duration_seconds"] or 0.0,
            clips_analyzed=row["clips_analyzed"] or 0,
            clips_selected=row["clips_selected"] or 0,
            errors_count=row["errors_count"] or 0,
            system_info=system_info,
        )

    def get_phase_stats(self, run_id: str) -> list[PhaseStats]:
        """Get all phase stats for a run.

        Args:
            run_id: The run ID.

        Returns:
            List of PhaseStats for the run.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM phase_stats
                WHERE run_id = ?
                ORDER BY started_at
                """,
                (run_id,),
            ).fetchall()

            return [self._row_to_phase_stats(row) for row in rows]

    def _row_to_phase_stats(self, row: sqlite3.Row) -> PhaseStats:
        """Convert database row to PhaseStats."""
        return PhaseStats(
            phase_name=row["phase_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            duration_seconds=row["duration_seconds"] or 0.0,
            items_processed=row["items_processed"] or 0,
            items_total=row["items_total"] or 0,
            errors=json.loads(row["errors"]) if row["errors"] else [],
            extra_metrics=json.loads(row["extra_metrics"]) if row["extra_metrics"] else {},
        )

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        person_name: str | None = None,
        status: str | None = None,
    ) -> list[RunMetadata]:
        """List runs with optional filtering.

        Args:
            limit: Maximum number of runs to return.
            offset: Number of runs to skip.
            person_name: Filter by person name.
            status: Filter by status.

        Returns:
            List of RunMetadata.
        """
        with self._get_connection() as conn:
            query = "SELECT * FROM pipeline_runs WHERE 1=1"
            params: list = []

            if person_name:
                query += " AND person_name = ?"
                params.append(person_name)

            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            runs = []

            for row in rows:
                run = self._row_to_run(row)
                run.phases = self.get_phase_stats(run.run_id)
                runs.append(run)

            return runs

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

    def get_aggregate_stats(self) -> dict:
        """Get aggregate statistics across all runs.

        Returns:
            Dictionary with aggregate stats.
        """
        with self._get_connection() as conn:
            total_runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]

            completed_runs = conn.execute(
                "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'completed'"
            ).fetchone()[0]

            failed_runs = conn.execute(
                "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'failed'"
            ).fetchone()[0]

            total_output_seconds = conn.execute(
                "SELECT COALESCE(SUM(output_duration_seconds), 0) FROM pipeline_runs"
            ).fetchone()[0]

            total_clips = conn.execute(
                "SELECT COALESCE(SUM(clips_selected), 0) FROM pipeline_runs"
            ).fetchone()[0]

            # Calculate total processing time from phase_stats
            total_processing_seconds = conn.execute(
                "SELECT COALESCE(SUM(duration_seconds), 0) FROM phase_stats"
            ).fetchone()[0]

            avg_run_seconds = 0.0
            if completed_runs > 0:
                # Average processing time per completed run
                avg_result = conn.execute(
                    """
                    SELECT AVG(total_duration) FROM (
                        SELECT SUM(duration_seconds) as total_duration
                        FROM phase_stats ps
                        JOIN pipeline_runs pr ON ps.run_id = pr.run_id
                        WHERE pr.status = 'completed'
                        GROUP BY ps.run_id
                    )
                    """
                ).fetchone()
                avg_run_seconds = avg_result[0] or 0.0

            avg_clips = 0.0
            if total_runs > 0:
                avg_clips = total_clips / total_runs

            return {
                "total_runs": total_runs,
                "completed_runs": completed_runs,
                "failed_runs": failed_runs,
                "total_output_seconds": total_output_seconds,
                "total_processing_seconds": total_processing_seconds,
                "avg_run_seconds": avg_run_seconds,
                "avg_clips": avg_clips,
                "total_clips": total_clips,
            }

    def get_people_with_runs(self) -> list[str]:
        """Get list of distinct person names with runs.

        Returns:
            List of person names.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT person_name FROM pipeline_runs
                WHERE person_name IS NOT NULL
                ORDER BY person_name
                """
            ).fetchall()
            return [row["person_name"] for row in rows]

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

    # =========================================================================
    # Active Jobs Management (for browser reconnection & cancellation)
    # =========================================================================

    def _ensure_active_jobs_table(self) -> None:
        """Create active_jobs table if it doesn't exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_jobs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    phase TEXT,
                    progress_pct REAL DEFAULT 0,
                    progress_message TEXT,
                    cancel_requested INTEGER DEFAULT 0,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    selected_clips TEXT,
                    clip_segments TEXT,
                    generation_options TEXT
                )
                """
            )
            conn.commit()

    def create_job(
        self,
        run_id: str,
        selected_clips: list[str],
        clip_segments: dict[str, tuple[float, float]],
        generation_options: dict,
    ) -> None:
        """Create a new active job for tracking.

        Args:
            run_id: Unique run identifier.
            selected_clips: List of asset IDs selected for the video.
            clip_segments: Dict mapping asset_id to (start, end) times.
            generation_options: Dict of generation options from UI.
        """
        self._ensure_active_jobs_table()
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO active_jobs (
                    run_id, status, phase, progress_pct, progress_message,
                    cancel_requested, started_at, updated_at,
                    selected_clips, clip_segments, generation_options
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "running",
                    "starting",
                    0.0,
                    "Initializing...",
                    0,
                    now,
                    now,
                    json.dumps(selected_clips),
                    json.dumps(clip_segments),
                    json.dumps(generation_options),
                ),
            )
            conn.commit()
        logger.info(f"Created active job: {run_id}")

    def update_job_progress(
        self,
        run_id: str,
        phase: str,
        progress_pct: float,
        message: str,
    ) -> None:
        """Update job progress for UI polling.

        Args:
            run_id: The run ID to update.
            phase: Current phase (analysis, assembly, music, etc.).
            progress_pct: Progress percentage (0-100).
            message: Human-readable status message.
        """
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE active_jobs
                SET phase = ?, progress_pct = ?, progress_message = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (phase, progress_pct, message, now, run_id),
            )
            conn.commit()

    def request_cancel(self, run_id: str) -> bool:
        """Request cancellation of a running job.

        Args:
            run_id: The run ID to cancel.

        Returns:
            True if job was found and cancel requested, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE active_jobs
                SET cancel_requested = 1, updated_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (datetime.now().isoformat(), run_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Cancel requested for job: {run_id}")
                return True
            return False

    def is_cancel_requested(self, run_id: str) -> bool:
        """Check if cancellation has been requested for a job.

        Args:
            run_id: The run ID to check.

        Returns:
            True if cancel was requested, False otherwise.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM active_jobs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return bool(row and row["cancel_requested"])

    def get_active_job(self) -> dict | None:
        """Get the currently active job (if any).

        Returns:
            Dict with job info or None if no active job.
        """
        self._ensure_active_jobs_table()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM active_jobs
                WHERE status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()

            if not row:
                return None

            return {
                "run_id": row["run_id"],
                "status": row["status"],
                "phase": row["phase"],
                "progress_pct": row["progress_pct"],
                "progress_message": row["progress_message"],
                "cancel_requested": bool(row["cancel_requested"]),
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "selected_clips": json.loads(row["selected_clips"])
                if row["selected_clips"]
                else [],
                "clip_segments": json.loads(row["clip_segments"]) if row["clip_segments"] else {},
                "generation_options": json.loads(row["generation_options"])
                if row["generation_options"]
                else {},
            }

    def complete_job(self, run_id: str, status: str = "completed") -> None:
        """Mark a job as completed (or failed/cancelled).

        Args:
            run_id: The run ID to complete.
            status: Final status ('completed', 'failed', 'cancelled').
        """
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE active_jobs
                SET status = ?, progress_pct = 100, updated_at = ?
                WHERE run_id = ?
                """,
                (status, now, run_id),
            )
            conn.commit()
        logger.info(f"Job {run_id} marked as {status}")
