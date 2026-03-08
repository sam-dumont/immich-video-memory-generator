"""Active jobs management mixin for RunDatabase.

Handles browser reconnection and job cancellation tracking
via a separate active_jobs table in SQLite.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ActiveJobsMixin:
    """Mixin providing active job tracking for RunDatabase.

    Methods for creating, updating, cancelling, and completing
    active pipeline jobs. Used by the UI for progress polling
    and browser reconnection.
    """

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
