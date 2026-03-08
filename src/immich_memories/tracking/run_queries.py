"""Query and aggregation methods for RunDatabase.

Provides listing, filtering, aggregate statistics, and
people-with-runs queries as a mixin.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from immich_memories.tracking.models import PhaseStats, RunMetadata, SystemInfo

logger = logging.getLogger(__name__)


def row_to_run(row: sqlite3.Row) -> RunMetadata:
    """Convert database row to RunMetadata."""
    system_info = None
    if row["system_info"]:
        system_info = SystemInfo.from_json(row["system_info"])

    return RunMetadata(
        run_id=row["run_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
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


def row_to_phase_stats(row: sqlite3.Row) -> PhaseStats:
    """Convert database row to PhaseStats."""
    return PhaseStats(
        phase_name=row["phase_name"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        duration_seconds=row["duration_seconds"] or 0.0,
        items_processed=row["items_processed"] or 0,
        items_total=row["items_total"] or 0,
        errors=json.loads(row["errors"]) if row["errors"] else [],
        extra_metrics=json.loads(row["extra_metrics"]) if row["extra_metrics"] else {},
    )


class RunQueriesMixin:
    """Mixin providing query and aggregation methods for RunDatabase.

    Methods for listing runs, getting aggregate statistics,
    and querying people with runs.
    """

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

            return [row_to_phase_stats(row) for row in rows]

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
                run = row_to_run(row)
                run.phases = self.get_phase_stats(run.run_id)
                runs.append(run)

            return runs

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

            avg_run_seconds = _compute_avg_run_seconds(conn, completed_runs)

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


def _compute_avg_run_seconds(conn: sqlite3.Connection, completed_runs: int) -> float:
    """Compute average processing time per completed run."""
    if completed_runs <= 0:
        return 0.0

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
    return avg_result[0] or 0.0
