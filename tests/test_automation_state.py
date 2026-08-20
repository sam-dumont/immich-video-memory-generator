"""Durable state tests for smart automation attempts."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.state_store import (
    AttemptAlreadyFinishedError,
    AutomationStateStore,
)


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
    assert attempt.started_at.utcoffset() == timedelta(0)
    assert attempt.finished_at is None
    assert store.get_last_attempt() == attempt


def test_finished_attempt_uses_aware_utc_timestamp(tmp_path: Path) -> None:
    store = AutomationStateStore(tmp_path / "cache.db")
    attempt = store.start_attempt(reason="daily wake")

    finished = store.finish_attempt(attempt.id, AutoOutcome.SKIPPED, reason="no candidates")

    assert finished.finished_at is not None
    assert finished.finished_at.utcoffset() == timedelta(0)


def test_finish_attempt_rejects_running_without_changing_start_row(tmp_path: Path) -> None:
    """A rejected non-terminal transition leaves durable state untouched."""
    store = AutomationStateStore(tmp_path / "cache.db")
    attempt = store.start_attempt(reason="daily wake")

    with pytest.raises(ValueError, match="RUNNING is not a terminal automation outcome"):
        store.finish_attempt(attempt.id, AutoOutcome.RUNNING, reason="still working")

    assert store.get_last_attempt() == attempt


def test_finish_attempt_cannot_overwrite_a_terminal_attempt(tmp_path: Path) -> None:
    """The first terminal result is immutable, including its completion timestamp."""
    store = AutomationStateStore(tmp_path / "cache.db")
    attempt = store.start_attempt(reason="daily wake", memory_key="trip:first")
    first = store.finish_attempt(
        attempt.id,
        AutoOutcome.COMPLETED,
        reason="generated",
        candidate_category="trip",
        memory_type="trip",
        memory_key="trip:first",
        run_id="run-first",
    )

    with pytest.raises(AttemptAlreadyFinishedError, match=attempt.id):
        store.finish_attempt(
            attempt.id,
            AutoOutcome.FAILED,
            reason="late failure",
            candidate_category="monthly_review",
            memory_type="monthly_highlights",
            memory_key="month:replacement",
            run_id="run-replacement",
            error="must not win",
        )

    assert store.get_last_attempt() == first


def test_finish_attempt_reports_unknown_id_separately(tmp_path: Path) -> None:
    """Missing rows remain a lookup error, not a duplicate-terminal transition."""
    store = AutomationStateStore(tmp_path / "cache.db")

    with pytest.raises(KeyError, match="Unknown automation attempt: missing"):
        store.finish_attempt("missing", AutoOutcome.FAILED, reason="not found")


class TestConsecutiveFailuresByKey:
    """The nightly runner has to know which candidates keep failing.

    Every failure is already recorded with its memory_key, but nothing reads it
    back, so a candidate that cannot succeed is picked again every night -- the
    same one went out nine nights in a row in a real log.
    """

    @staticmethod
    def _attempt(store, key: str, outcome: AutoOutcome) -> None:
        attempt = store.start_attempt(reason="daily wake")
        store.finish_attempt(attempt.id, outcome, reason="test", memory_key=key)

    def test_counts_failures_per_key(self, tmp_path: Path) -> None:
        store = AutomationStateStore(tmp_path / "cache.db")
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)
        self._attempt(store, "trip:alps", AutoOutcome.FAILED)

        failures = store.consecutive_failures_by_key()

        assert failures["monthly:2026-06"].count == 2
        assert failures["trip:alps"].count == 1

    def test_a_success_clears_the_streak(self, tmp_path: Path) -> None:
        """Backoff must not punish a key that has since worked."""
        store = AutomationStateStore(tmp_path / "cache.db")
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)
        self._attempt(store, "monthly:2026-06", AutoOutcome.COMPLETED)

        assert "monthly:2026-06" not in store.consecutive_failures_by_key()

    def test_a_failure_after_a_success_starts_a_new_streak(self, tmp_path: Path) -> None:
        store = AutomationStateStore(tmp_path / "cache.db")
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)
        self._attempt(store, "monthly:2026-06", AutoOutcome.COMPLETED)
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)

        assert store.consecutive_failures_by_key()["monthly:2026-06"].count == 1

    def test_skipped_attempts_are_not_failures(self, tmp_path: Path) -> None:
        """A cooldown skip says nothing about whether the candidate can render."""
        store = AutomationStateStore(tmp_path / "cache.db")
        self._attempt(store, "monthly:2026-06", AutoOutcome.SKIPPED)

        assert "monthly:2026-06" not in store.consecutive_failures_by_key()

    def test_reports_when_the_last_failure_happened(self, tmp_path: Path) -> None:
        store = AutomationStateStore(tmp_path / "cache.db")
        self._attempt(store, "monthly:2026-06", AutoOutcome.FAILED)

        entry = store.consecutive_failures_by_key()["monthly:2026-06"]

        assert entry.last_failed_at is not None
