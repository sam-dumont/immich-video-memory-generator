"""Tests for RunDatabase FK constraint handling when run_id is missing."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.tracking.models import PhaseStats
from immich_memories.tracking.run_database import RunDatabase
from immich_memories.tracking.run_tracker import RunTracker


@pytest.fixture
def db(tmp_path):
    return RunDatabase(db_path=tmp_path / "test.db")


def _make_phase_stats() -> PhaseStats:
    return PhaseStats(
        phase_name="analysis",
        started_at=datetime(2026, 3, 27, 10, 0),
        completed_at=datetime(2026, 3, 27, 10, 5),
        duration_seconds=300.0,
        items_processed=42,
        items_total=42,
        errors=[],
        extra_metrics={},
    )


class TestCompletePhaseDBResilience:
    """RunTracker.complete_phase must not crash when DB write fails."""

    # WHY: RunDatabase opens a SQLite connection — isolate tracker logic from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_phase_survives_db_exception(self, mock_db_cls, caplog):
        """complete_phase logs a warning and continues if DB raises any exception."""
        tracker = RunTracker(db_path=Path("/tmp/test.db"))
        tracker.start_phase("analysis", total_items=10)

        # Simulate DB failure (e.g. DB deleted, corruption, etc.)
        tracker.db.save_phase_stats.side_effect = RuntimeError("disk I/O error")

        with caplog.at_level(logging.WARNING):
            tracker.complete_phase(items_processed=10)

        # Phase state should be reset even after failure
        assert tracker._current_phase is None
        assert any("phase stats" in r.message.lower() for r in caplog.records)


class TestSavePhaseStatsFKConstraint:
    """save_phase_stats must not crash when run_id is missing from pipeline_runs."""

    def test_nonexistent_run_id_does_not_raise(self, db):
        """Inserting phase stats for a missing run_id logs a warning instead of raising."""
        stats = _make_phase_stats()
        # Should NOT raise sqlite3.IntegrityError
        db.save_phase_stats("nonexistent_run_id", stats)

    def test_nonexistent_run_id_logs_warning(self, db, caplog):
        """A warning is logged when phase stats are lost due to missing run_id."""
        stats = _make_phase_stats()
        with caplog.at_level(logging.WARNING):
            db.save_phase_stats("nonexistent_run_id", stats)
        assert any("run_id" in record.message.lower() for record in caplog.records)

    def test_valid_run_id_saves_normally(self, db):
        """Phase stats with a valid run_id are saved successfully."""
        from immich_memories.tracking.models import RunMetadata

        run = RunMetadata(
            run_id="valid_run_001",
            created_at=datetime(2026, 3, 27, 10, 0),
            status="running",
        )
        db.save_run(run)

        stats = _make_phase_stats()
        db.save_phase_stats("valid_run_001", stats)

        # Verify the stats were actually persisted
        retrieved = db.get_phase_stats("valid_run_001")
        assert len(retrieved) == 1
        assert retrieved[0].phase_name == "analysis"
