"""SQLite row conversion for pipeline run history."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from immich_memories.operations.phases import OperationalPhase
from immich_memories.tracking.models import (
    DeliveryStatus,
    PhaseStats,
    RunMetadata,
    SystemInfo,
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
        last_phase=(
            OperationalPhase(_row_value(row, "last_phase"))
            if _row_value(row, "last_phase")
            else None
        ),
        person_name=row["person_name"],
        person_id=row["person_id"],
        date_range_start=(
            date.fromisoformat(row["date_range_start"]) if row["date_range_start"] else None
        ),
        date_range_end=(
            date.fromisoformat(row["date_range_end"]) if row["date_range_end"] else None
        ),
        # WHY the keys() check: a reader can hold a connection while another
        # process is mid-migration (the concurrent-upgrade tests pin that
        # window), and sqlite3.Row raises on a column that does not exist yet.
        target_duration_seconds=(
            row["target_duration_seconds"]
            if "target_duration_seconds" in row.keys()  # noqa: SIM118 — sqlite3.Row `in` checks values, not keys
            and row["target_duration_seconds"] is not None
            else (row["target_duration_minutes"] or 10) * 60
        ),
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
        llm_metrics=(
            json.loads(_row_value(row, "llm_metrics")) if _row_value(row, "llm_metrics") else {}
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
