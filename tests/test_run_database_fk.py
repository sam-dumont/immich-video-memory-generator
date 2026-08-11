"""Tests for RunDatabase FK constraint handling when run_id is missing."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.cache import database as cache_database
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.tracking.models import PhaseStats, RunMetadata
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


def _make_completed_run(
    run_id: str,
    created_at: datetime,
    *,
    memory_key: str = "trip:key",
    source: str = "auto",
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        created_at=created_at,
        completed_at=created_at + timedelta(minutes=10),
        status="completed",
        memory_type="trip",
        memory_key=memory_key,
        source=source,
    )


def test_v10_migrates_populated_v9_database_without_losing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The additive v10 migration keeps production-era v9 run records intact."""
    db_path = tmp_path / "v9.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 9)
    RunDatabase(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status,
                memory_type, memory_key, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-v9",
                "2026-07-01T09:00:00",
                "2026-07-01T09:10:00",
                "completed",
                "trip",
                "trip:key",
                "auto",
            ),
        )

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 10)
    migrated = RunDatabase(db_path)
    loaded = migrated.get_run("existing-v9")

    assert loaded is not None
    assert loaded.memory_category is None
    assert loaded.memory_people == ()


def test_v12_fresh_database_has_exact_automation_run_identity(tmp_path: Path) -> None:
    """A fresh database can correlate a pipeline run to one automation attempt."""
    db_path = tmp_path / "fresh.db"
    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        attempt_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(automation_attempts)").fetchall()
        }
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
        run_indexes = {row[1] for row in conn.execute("PRAGMA index_list(pipeline_runs)")}

    assert version == 12
    assert attempt_columns == {
        "id",
        "started_at",
        "finished_at",
        "outcome",
        "reason",
        "candidate_category",
        "memory_type",
        "memory_key",
        "run_id",
        "error",
    }
    assert "automation_attempt_id" in run_columns
    assert "idx_runs_automation_attempt" in run_indexes


def test_v12_migrates_populated_v11_database_without_losing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The additive v12 column leaves existing manual and automation rows intact."""
    db_path = tmp_path / "v11.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 11)
    VideoAnalysisCache(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status,
                memory_type, memory_key, memory_category, memory_people_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-v11",
                "2026-07-02T09:00:00+00:00",
                "2026-07-02T09:01:00+00:00",
                "completed",
                "trip",
                "trip:key",
                "trip",
                "[]",
                "auto",
            ),
        )
        conn.commit()

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 12)
    migrated = RunDatabase(db_path)
    loaded = migrated.get_run("existing-v11")

    assert loaded is not None
    assert loaded.run_id == "existing-v11"
    assert loaded.automation_attempt_id is None


def test_run_identity_fields_round_trip_with_normalized_people(db: RunDatabase) -> None:
    """Run identity persists category and canonical Unicode person names."""
    run = _make_completed_run("normalized", datetime(2026, 7, 2, 9, 0))
    run.memory_category = "person_spotlight"
    run.memory_people = ("  ALICE\tSmith ", "Straße   Example")
    run.automation_attempt_id = "attempt-round-trip"
    db.save_run(run)

    loaded = db.get_run("normalized")
    assert loaded is not None
    assert loaded.memory_category == "person_spotlight"
    assert loaded.memory_people == ("alice smith", "strasse example")
    assert loaded.automation_attempt_id == "attempt-round-trip"
    assert loaded.to_dict()["memory_people"] == ["alice smith", "strasse example"]
    assert RunMetadata.from_json(loaded.to_json()).memory_people == (
        "alice smith",
        "strasse example",
    )


def test_list_runs_filters_source_before_limit(db: RunDatabase) -> None:
    """A newer manual run cannot hide an older automation run behind LIMIT."""
    db.save_run(_make_completed_run("auto", datetime(2026, 7, 2, 9, 0), source="auto"))
    db.save_run(_make_completed_run("manual", datetime(2026, 7, 3, 9, 0), source="manual"))

    runs = db.list_runs(limit=1, status="completed", source="auto")

    assert [run.run_id for run in runs] == ["auto"]


def test_list_runs_treats_empty_source_as_a_concrete_filter(db: RunDatabase) -> None:
    """Only None disables source filtering; an empty string remains queryable."""
    db.save_run(_make_completed_run("empty", datetime(2026, 7, 2, 9, 0), source=""))
    db.save_run(_make_completed_run("manual", datetime(2026, 7, 3, 9, 0), source="manual"))

    runs = db.list_runs(status="completed", source="")

    assert [run.run_id for run in runs] == ["empty"]


def test_list_runs_can_order_completed_rows_by_completion_time(db: RunDatabase) -> None:
    """Automation recency follows completion, with legacy rows falling back to creation."""
    completed_first = _make_completed_run(
        "created-last-completed-first",
        datetime(2026, 8, 10, 10, 0),
    )
    completed_first.completed_at = datetime(2026, 8, 10, 10, 30)
    legacy = _make_completed_run("legacy-null-completion", datetime(2026, 8, 10, 11, 30))
    legacy.completed_at = None
    completed_last = _make_completed_run(
        "created-first-completed-last",
        datetime(2026, 8, 10, 9, 0),
    )
    completed_last.completed_at = datetime(2026, 8, 10, 12, 0)
    for run in (completed_first, legacy, completed_last):
        db.save_run(run)

    runs = db.list_runs(
        status="completed",
        source="auto",
        order_by_completion=True,
    )

    assert [run.run_id for run in runs] == [
        "created-first-completed-last",
        "legacy-null-completion",
        "created-last-completed-first",
    ]


def test_completion_order_has_deterministic_tie_breakers(db: RunDatabase) -> None:
    """Equal completion and creation timestamps fall back to descending run ID."""
    created_at = datetime(2026, 8, 10, 9, 0)
    for run_id in ("tie-a", "tie-z"):
        run = _make_completed_run(run_id, created_at)
        db.save_run(run)

    runs = db.list_runs(
        status="completed",
        source="auto",
        order_by_completion=True,
    )

    assert [run.run_id for run in runs] == ["tie-z", "tie-a"]


def test_last_run_of_type_filters_source_before_order_and_limit(db: RunDatabase) -> None:
    """A newer manual run cannot hide the last auto run of the same memory type."""
    completed_last = _make_completed_run(
        "created-first-completed-last",
        datetime(2026, 8, 8, 9, 0),
        source="auto",
    )
    completed_last.completed_at = datetime(2026, 8, 11, 12, 0)
    db.save_run(completed_last)
    db.save_run(_make_completed_run("created-last", datetime(2026, 8, 9, 9, 0), source="auto"))
    db.save_run(
        _make_completed_run(
            "newer-manual-trip",
            datetime(2026, 8, 10, 9, 0),
            source="manual",
        )
    )

    run = db.get_last_run_of_type("trip", source="auto")

    assert run is not None
    assert run.run_id == "created-first-completed-last"


def test_completed_identity_filters_source_and_time(db: RunDatabase) -> None:
    """Exact run lookup rejects wrong-source and pre-attempt completions."""
    started_after = datetime(2026, 7, 2, 9, 0)
    db.save_run(_make_completed_run("old-auto", started_after, source="auto"))
    db.save_run(
        _make_completed_run("new-manual", started_after + timedelta(minutes=1), source="manual")
    )
    expected = _make_completed_run("new-auto", started_after + timedelta(minutes=2), source="auto")
    db.save_run(expected)

    actual = db.get_completed_run_by_identity("trip:key", "auto", started_after)

    assert actual == expected


def test_completed_automation_attempt_identity_is_exact(db: RunDatabase) -> None:
    """A same-key completion from another wake cannot satisfy this parent attempt."""
    wrong = _make_completed_run("wrong-attempt", datetime(2026, 7, 2, 9, 0))
    wrong.automation_attempt_id = "attempt-other"
    expected = _make_completed_run("exact-attempt", datetime(2026, 7, 2, 9, 1))
    expected.automation_attempt_id = "attempt-exact"
    db.save_run(wrong)
    db.save_run(expected)

    actual = db.get_completed_run_by_automation_attempt(
        "attempt-exact",
        memory_key="trip:key",
    )

    assert actual == expected
    assert (
        db.get_completed_run_by_automation_attempt(
            "attempt-other",
            memory_key="different:key",
        )
        is None
    )


def test_completed_automation_attempt_identity_rejects_ambiguity(db: RunDatabase) -> None:
    """Two matching child rows are corruption, not a license to pick one."""
    first = _make_completed_run("duplicate-first", datetime(2026, 7, 2, 9, 0))
    first.automation_attempt_id = "attempt-duplicate"
    second = _make_completed_run("duplicate-second", datetime(2026, 7, 2, 9, 1))
    second.automation_attempt_id = "attempt-duplicate"
    db.save_run(first)
    db.save_run(second)

    with pytest.raises(RuntimeError, match="Multiple completed auto runs"):
        db.get_completed_run_by_automation_attempt(
            "attempt-duplicate",
            memory_key="trip:key",
        )
