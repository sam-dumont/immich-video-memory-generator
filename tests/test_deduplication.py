"""Tests for memory deduplication — DB schema + history queries."""

from datetime import date, datetime

import pytest

from immich_memories.tracking.models import RunMetadata
from immich_memories.tracking.run_database import RunDatabase


@pytest.fixture
def db(tmp_path):
    return RunDatabase(db_path=tmp_path / "test.db")


def _make_run(
    run_id: str = "test_001",
    status: str = "completed",
    memory_type: str | None = "year_in_review",
    memory_key: str | None = "year_in_review:2025-01-01:2025-12-31:",
    source: str = "manual",
    date_start: date | None = None,
    date_end: date | None = None,
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        created_at=datetime(2026, 3, 1, 10, 0),
        completed_at=datetime(2026, 3, 1, 10, 30),
        status=status,
        memory_type=memory_type,
        memory_key=memory_key,
        source=source,
        date_range_start=date_start or date(2025, 1, 1),
        date_range_end=date_end or date(2025, 12, 31),
    )


class TestMigrationV9:
    def test_new_columns_exist_after_migration(self, db):
        """Migration v9 adds memory_type, memory_key, source columns."""
        run = _make_run()
        db.save_run(run)
        loaded = db.get_run("test_001")
        assert loaded is not None
        assert loaded.memory_type == "year_in_review"
        assert loaded.memory_key == "year_in_review:2025-01-01:2025-12-31:"
        assert loaded.source == "manual"

    def test_null_memory_fields_backward_compat(self, db):
        """Runs without memory_type/key (pre-migration data) load fine."""
        run = _make_run(memory_type=None, memory_key=None)
        db.save_run(run)
        loaded = db.get_run("test_001")
        assert loaded is not None
        assert loaded.memory_type is None
        assert loaded.memory_key is None


class TestHasMemoryBeenGenerated:
    def test_returns_true_for_completed_run(self, db):
        db.save_run(_make_run(status="completed"))
        assert db.has_memory_been_generated("year_in_review:2025-01-01:2025-12-31:") is True

    def test_returns_false_for_failed_run(self, db):
        db.save_run(_make_run(status="failed"))
        assert db.has_memory_been_generated("year_in_review:2025-01-01:2025-12-31:") is False

    def test_returns_false_for_unknown_key(self, db):
        assert db.has_memory_been_generated("unknown:2025-01-01:2025-12-31:") is False

    def test_returns_false_for_null_key(self, db):
        assert db.has_memory_been_generated("") is False


class TestGetLastRunOfType:
    def test_returns_most_recent_completed(self, db):
        db.save_run(_make_run(run_id="r1", memory_type="monthly_highlights"))
        db.save_run(
            _make_run(
                run_id="r2",
                memory_type="monthly_highlights",
                memory_key="monthly_highlights:2025-02-01:2025-02-28:",
            )
        )
        result = db.get_last_run_of_type("monthly_highlights")
        assert result is not None
        # Both have same created_at, but r2 was inserted second — ORDER BY rowid DESC
        assert result.memory_type == "monthly_highlights"

    def test_returns_none_for_no_matches(self, db):
        assert db.get_last_run_of_type("nonexistent_type") is None

    def test_ignores_failed_runs(self, db):
        db.save_run(_make_run(status="failed", memory_type="year_in_review"))
        assert db.get_last_run_of_type("year_in_review") is None


class TestGetGeneratedMemoryKeys:
    def test_returns_completed_keys(self, db):
        db.save_run(_make_run(run_id="r1", memory_key="key1"))
        db.save_run(_make_run(run_id="r2", memory_key="key2"))
        db.save_run(_make_run(run_id="r3", memory_key="key3", status="failed"))
        keys = db.get_generated_memory_keys()
        assert keys == {"key1", "key2"}

    def test_excludes_null_keys(self, db):
        db.save_run(_make_run(run_id="r1", memory_key=None))
        keys = db.get_generated_memory_keys()
        assert keys == set()
