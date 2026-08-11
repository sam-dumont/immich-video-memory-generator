"""Durable state tests for smart automation attempts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.state_store import AutomationStateStore


def test_automation_attempt_round_trip_preserves_start_row(tmp_path: Path) -> None:
    """Finishing an attempt updates the original row instead of replacing it."""
    db_path = tmp_path / "cache.db"
    store = AutomationStateStore(db_path)
    attempt = store.start_attempt(reason="daily wake")

    store.finish_attempt(
        attempt.id,
        AutoOutcome.SKIPPED,
        reason="cooldown",
        candidate_category="trip",
        memory_type="trip",
        memory_key="trip:key",
        run_id="run-123",
        error="not generated",
    )

    saved = store.get_last_attempt()
    assert saved is not None
    assert saved.id == attempt.id
    assert saved.started_at == attempt.started_at
    assert saved.finished_at is not None
    assert saved.outcome is AutoOutcome.SKIPPED
    assert saved.reason == "cooldown"
    assert saved.candidate_category == "trip"
    assert saved.memory_type == "trip"
    assert saved.memory_key == "trip:key"
    assert saved.run_id == "run-123"
    assert saved.error == "not generated"

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0]
    assert count == 1


def test_running_attempt_round_trips_enum_and_optional_fields(tmp_path: Path) -> None:
    """A newly-started attempt is immediately observable as running."""
    store = AutomationStateStore(tmp_path / "cache.db")

    attempt = store.start_attempt(
        reason="daily wake",
        candidate_category="person_spotlight",
        memory_type="person_spotlight",
        memory_key="person:key",
    )

    assert attempt.outcome is AutoOutcome.RUNNING
    assert attempt.finished_at is None
    assert store.get_last_attempt() == attempt
