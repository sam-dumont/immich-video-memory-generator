"""Database operations for run history."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn

from immich_memories.tracking.models import (
    DeliveryStatus,
    PhaseStats,
    RunMetadata,
    SystemInfo,
    normalize_memory_people,
)

logger = logging.getLogger(__name__)


class InvalidRunLifecycleError(RuntimeError):
    """Raised when a run's lifecycle state forbids a requested transition."""


class DuplicateRunError(RuntimeError):
    """Raised when a new tracker tries to claim an existing run identity."""


def _raise_invalid_delivery_transition(
    conn: sqlite3.Connection,
    run_id: str,
) -> NoReturn:
    row = conn.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown pipeline run: {run_id}")
    raise InvalidRunLifecycleError(
        f"Delivery transition requires a completed run; run '{run_id}' is '{row['status']}'"
    )


def _row_value(row: sqlite3.Row, column: str, default: Any = None) -> Any:
    """Read a column that may be absent on legacy compatibility rows."""
    try:
        return row[column]
    except IndexError:
        return default


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
        memory_type=_row_value(row, "memory_type"),
        memory_key=_row_value(row, "memory_key"),
        memory_category=_row_value(row, "memory_category"),
        memory_people=(
            tuple(json.loads(_row_value(row, "memory_people_json")))
            if _row_value(row, "memory_people_json")
            else ()
        ),
        source=_row_value(row, "source", "manual"),
        automation_attempt_id=_row_value(row, "automation_attempt_id"),
        person_name=row["person_name"],
        person_id=row["person_id"],
        date_range_start=(
            date.fromisoformat(row["date_range_start"]) if row["date_range_start"] else None
        ),
        date_range_end=(
            date.fromisoformat(row["date_range_end"]) if row["date_range_end"] else None
        ),
        target_duration_seconds=(row["target_duration_minutes"] or 10) * 60,
        output_path=row["output_path"],
        output_size_bytes=row["output_size_bytes"] or 0,
        output_duration_seconds=row["output_duration_seconds"] or 0.0,
        delivery_status=DeliveryStatus(
            _row_value(row, "delivery_status") or DeliveryStatus.NOT_REQUESTED
        ),
        delivery_attempts=_row_value(row, "delivery_attempts", 0) or 0,
        delivery_error=_row_value(row, "delivery_error"),
        immich_asset_id=_row_value(row, "immich_asset_id"),
        delivery_album=_row_value(row, "delivery_album"),
        warnings=(
            json.loads(_row_value(row, "warnings_json")) if _row_value(row, "warnings_json") else []
        ),
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


class RunDatabase:
    """Database operations for pipeline run history."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

        # Ensure migrations are run (this will create the tables if needed)
        from immich_memories.cache.database import VideoAnalysisCache

        VideoAnalysisCache(db_path)  # This triggers migrations

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,  # busy_timeout=5000ms — retry on concurrent access
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def save_run(self, run: RunMetadata) -> None:
        """Insert a new run without replacing an existing authoritative identity."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, created_at, completed_at, status,
                    memory_type, memory_key, memory_category, memory_people_json, source,
                    automation_attempt_id,
                    person_name, person_id, date_range_start, date_range_end,
                    target_duration_minutes, output_path, output_size_bytes,
                    output_duration_seconds, clips_analyzed, clips_selected,
                    errors_count, system_info, delivery_status, delivery_attempts,
                    delivery_error, immich_asset_id, delivery_album, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    run.run_id,
                    run.created_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.status,
                    run.memory_type,
                    run.memory_key,
                    run.memory_category,
                    json.dumps(normalize_memory_people(run.memory_people)),
                    run.source,
                    run.automation_attempt_id,
                    run.person_name,
                    run.person_id,
                    run.date_range_start.isoformat() if run.date_range_start else None,
                    run.date_range_end.isoformat() if run.date_range_end else None,
                    run.target_duration_seconds // 60,
                    run.output_path,
                    run.output_size_bytes,
                    run.output_duration_seconds,
                    run.clips_analyzed,
                    run.clips_selected,
                    run.errors_count,
                    run.system_info.to_json() if run.system_info else None,
                    run.delivery_status.value,
                    run.delivery_attempts,
                    run.delivery_error,
                    run.immich_asset_id,
                    run.delivery_album,
                    json.dumps(run.warnings),
                ),
            )
            if cursor.rowcount != 1:
                raise DuplicateRunError(f"Pipeline run already exists: {run.run_id}")
            conn.commit()

    def save_phase_stats(self, run_id: str, stats: PhaseStats) -> None:
        """Save phase timing statistics.

        Gracefully handles missing run_id (e.g. DB was deleted mid-run).
        Phase stats are observability data — losing them is acceptable.
        """
        try:
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
        except sqlite3.IntegrityError:
            logger.warning(
                "Phase stats lost for '%s' — run_id '%s' may no longer exist in database",
                stats.phase_name,
                run_id,
            )

    def get_run(self, run_id: str) -> RunMetadata | None:
        """Get a single run by ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()

            if not row:
                return None

            run = row_to_run(row)
            run.phases = self.get_phase_stats(run_id)
            return run

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its stats."""
        with self._get_connection() as conn:
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
        delivery_album: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        """Update run status and optionally other fields."""
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

            if delivery_album is not None:
                updates.append("delivery_album = ?")
                params.append(delivery_album)

            if warnings is not None:
                updates.append("warnings_json = ?")
                params.append(json.dumps(warnings))

            params.append(run_id)

            conn.execute(
                f"UPDATE pipeline_runs SET {', '.join(updates)} WHERE run_id = ?",  # noqa: S608  # nosemgrep: sqlalchemy-execute-raw-query — column names hardcoded, values parameterized
                params,
            )
            conn.commit()

    def mark_delivery_pending(
        self,
        run_id: str,
        error: str,
        *,
        attempted: bool = True,
    ) -> RunMetadata:
        """Record one failed delivery call without changing artifact completion."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_runs
                SET delivery_status = ?,
                    delivery_attempts = delivery_attempts + ?,
                    delivery_error = ?,
                    immich_asset_id = NULL
                WHERE run_id = ? AND status = 'completed'
                """,
                (DeliveryStatus.PENDING.value, int(attempted), error, run_id),
            )
            if cursor.rowcount != 1:
                _raise_invalid_delivery_transition(conn, run_id)
            conn.commit()

        updated = self.get_run(run_id)
        if updated is None:  # pragma: no cover - row was updated in the transaction above
            raise KeyError(f"Unknown pipeline run: {run_id}")
        return updated

    def complete_artifact(
        self,
        run_id: str,
        *,
        completed_at: datetime,
        output_path: str,
        output_size_bytes: int,
        output_duration_seconds: float,
        delivery_requested: bool,
        delivery_album: str | None,
        warnings: list[str],
        clips_analyzed: int,
        clips_selected: int,
        errors_count: int,
    ) -> RunMetadata:
        """Atomically commit artifact facts and its initial delivery lifecycle."""
        delivery_status = (
            DeliveryStatus.PENDING if delivery_requested else DeliveryStatus.NOT_REQUESTED
        )
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'completed',
                    completed_at = ?,
                    output_path = ?,
                    output_size_bytes = ?,
                    output_duration_seconds = ?,
                    clips_analyzed = ?,
                    clips_selected = ?,
                    errors_count = ?,
                    delivery_status = ?,
                    delivery_attempts = 0,
                    delivery_error = NULL,
                    immich_asset_id = NULL,
                    delivery_album = ?,
                    warnings_json = ?
                WHERE run_id = ?
                """,
                (
                    completed_at.isoformat(),
                    output_path,
                    output_size_bytes,
                    output_duration_seconds,
                    clips_analyzed,
                    clips_selected,
                    errors_count,
                    delivery_status.value,
                    delivery_album,
                    json.dumps(warnings),
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown pipeline run: {run_id}")
            conn.commit()

        completed = self.get_run(run_id)
        if completed is None:  # pragma: no cover - row updated in the transaction above
            raise KeyError(f"Unknown pipeline run: {run_id}")
        return completed

    def mark_delivered(self, run_id: str, asset_id: str) -> RunMetadata:
        """Record one successful delivery call without changing artifact completion."""
        normalized_asset_id = asset_id.strip()
        if not normalized_asset_id:
            raise ValueError("Immich delivery requires a nonempty asset ID")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_runs
                SET delivery_status = ?,
                    delivery_attempts = delivery_attempts + 1,
                    delivery_error = NULL,
                    immich_asset_id = ?
                WHERE run_id = ? AND status = 'completed'
                """,
                (DeliveryStatus.DELIVERED.value, normalized_asset_id, run_id),
            )
            if cursor.rowcount != 1:
                _raise_invalid_delivery_transition(conn, run_id)
            conn.commit()

        updated = self.get_run(run_id)
        if updated is None:  # pragma: no cover - row was updated in the transaction above
            raise KeyError(f"Unknown pipeline run: {run_id}")
        return updated

    def mark_stale_runs_as_interrupted(self) -> int:
        """Mark any 'running' runs as 'interrupted' (startup cleanup)."""
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

    def get_oldest_pending_delivery(self, source: str) -> RunMetadata | None:
        """Return the oldest completed pending delivery for one source."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT run_id, output_path
                FROM pipeline_runs
                WHERE status = 'completed'
                  AND delivery_status = ?
                  AND output_path IS NOT NULL
                  AND source = ?
                ORDER BY COALESCE(completed_at, created_at), created_at, run_id
                """,
                (DeliveryStatus.PENDING.value, source),
            ).fetchall()

        for row in rows:
            output_path = Path(row["output_path"])
            if output_path.is_file():
                return self.get_run(row["run_id"])
            logger.warning(
                "Pending delivery run '%s' cannot be retried because its output file "
                "is missing or not a regular file: %s",
                row["run_id"],
                output_path,
            )
        return None

    # =========================================================================
    # Query Methods (from RunQueriesMixin)
    # =========================================================================

    def get_phase_stats(self, run_id: str) -> list[PhaseStats]:
        """Get all phase stats for a run."""
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
        source: str | None = None,
        order_by_completion: bool = False,
    ) -> list[RunMetadata]:
        """List runs with optional filtering and explicit completion ordering."""
        if order_by_completion and status != "completed":
            msg = "order_by_completion requires status='completed'"
            raise ValueError(msg)

        with self._get_connection() as conn:
            query = "SELECT * FROM pipeline_runs WHERE 1=1"
            params: list = []

            if person_name:
                query += " AND person_name = ?"
                params.append(person_name)

            if status:
                query += " AND status = ?"
                params.append(status)

            if source is not None:
                query += " AND source = ?"
                params.append(source)

            if order_by_completion:
                query += (
                    " ORDER BY COALESCE(completed_at, created_at) DESC,"
                    " created_at DESC, run_id DESC"
                )
            else:
                query += " ORDER BY created_at DESC"
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            runs = []

            for row in rows:
                run = row_to_run(row)
                run.phases = self.get_phase_stats(run.run_id)
                runs.append(run)

            return runs

    def get_aggregate_stats(self) -> dict:
        """Get aggregate statistics across all runs."""
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
        """Get list of distinct person names with runs."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT person_name FROM pipeline_runs
                WHERE person_name IS NOT NULL
                ORDER BY person_name
                """
            ).fetchall()
            return [row["person_name"] for row in rows]

    # =========================================================================
    # Deduplication Queries (for automation)
    # =========================================================================

    def has_memory_been_generated(self, memory_key: str) -> bool:
        """Check if a memory with this key has been successfully generated."""
        if not memory_key:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM pipeline_runs WHERE memory_key = ? AND status = 'completed' LIMIT 1",
                (memory_key,),
            ).fetchone()
            return row is not None

    def get_last_run_of_type(
        self,
        memory_type: str,
        source: str | None = None,
    ) -> RunMetadata | None:
        """Get the most recent completed run of a type, optionally scoped by source."""
        with self._get_connection() as conn:
            query = """
                SELECT * FROM pipeline_runs
                WHERE memory_type = ? AND status = 'completed'
            """
            params = [memory_type]
            if source is not None:
                query += " AND source = ?"
                params.append(source)
            query += (
                " ORDER BY COALESCE(completed_at, created_at) DESC,"
                " created_at DESC, run_id DESC LIMIT 1"
            )
            row = conn.execute(query, params).fetchone()
            return row_to_run(row) if row else None

    def get_generated_memory_keys(self) -> set[str]:
        """Get all memory_keys that have been successfully generated."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT memory_key FROM pipeline_runs
                WHERE status = 'completed' AND memory_key IS NOT NULL
                """
            ).fetchall()
            return {row["memory_key"] for row in rows}

    def get_completed_run_by_identity(
        self,
        memory_key: str,
        source: str,
        created_after: datetime,
    ) -> RunMetadata | None:
        """Find a completed run created after an automation attempt started."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM pipeline_runs
                WHERE memory_key = ?
                  AND source = ?
                  AND status = 'completed'
                  AND created_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (memory_key, source, created_after.isoformat()),
            ).fetchone()
        return row_to_run(row) if row else None

    def get_completed_run_by_automation_attempt(
        self,
        automation_attempt_id: str,
        *,
        memory_key: str,
    ) -> RunMetadata | None:
        """Find the completed auto run created by one exact automation attempt."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pipeline_runs
                WHERE automation_attempt_id = ?
                  AND memory_key = ?
                  AND source = 'auto'
                  AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 2
                """,
                (automation_attempt_id, memory_key),
            ).fetchall()
        if len(rows) > 1:
            raise RuntimeError(
                f"Multiple completed auto runs matched automation attempt {automation_attempt_id}"
            )
        return row_to_run(rows[0]) if rows else None
