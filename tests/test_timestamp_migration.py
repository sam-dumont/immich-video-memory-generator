"""Regression tests for UTC timestamp and legacy automation identity migration."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import AutoRunner, _cooldown_status
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.cache import database as cache_database
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.config_loader import Config
from immich_memories.tracking.run_database import RunDatabase


@pytest.fixture
def brussels_machine_timezone() -> Iterator[None]:
    """Run a migration with Brussels as the process-local historical timezone."""
    if not hasattr(time, "tzset"):
        pytest.skip("process-local timezone switching is unavailable")
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Brussels"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _create_minimal_v10_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT,
                description TEXT
            );
            INSERT INTO schema_migrations (version) VALUES (10);

            CREATE TABLE pipeline_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                source TEXT,
                memory_type TEXT,
                memory_category TEXT,
                memory_people_json TEXT,
                person_name TEXT
            );
            CREATE TABLE phase_stats (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE automation_attempts (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            """
        )


def test_future_automation_attempt_timestamps_are_aware_utc(tmp_path: Path) -> None:
    store = AutomationStateStore(tmp_path / "attempts.db")
    attempt = store.start_attempt(reason="daily wake")

    finished = store.finish_attempt(attempt.id, AutoOutcome.SKIPPED, reason="no candidates")

    assert attempt.started_at.tzinfo is UTC
    assert finished.finished_at is not None
    assert finished.finished_at.tzinfo is UTC


def test_v11_normalizes_all_timestamps_to_canonical_utc(
    tmp_path: Path,
    brussels_machine_timezone: None,
) -> None:
    """Each naive wall time uses Brussels DST rules for its own calendar date."""
    db_path = tmp_path / "legacy-v10.db"
    _create_minimal_v10_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status, source,
                memory_type, memory_category, memory_people_json, person_name
            ) VALUES (?, ?, ?, 'completed', 'auto', ?, ?, ?, ?)
            """,
            [
                (
                    "winter",
                    "2026-01-10T09:00:00",
                    "2026-01-10T10:00:00",
                    "year_in_review",
                    None,
                    "[]",
                    None,
                ),
                (
                    "summer",
                    "2026-08-10T09:00:00",
                    "2026-08-10T10:00:00",
                    "monthly_highlights",
                    None,
                    "[]",
                    None,
                ),
                (
                    "aware",
                    "2026-08-10T09:00:00+02:00",
                    "2026-08-10T10:00:00+02:00",
                    "trip",
                    "explicit-category",
                    '["Existing Person"]',
                    "Ignored Person",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO phase_stats (id, started_at, completed_at) VALUES (?, ?, ?)",
            [
                (1, "2026-08-10T09:00:00", "2026-08-10T10:00:00"),
                (2, "2026-08-10T09:00:00+02:00", "2026-08-10T10:00:00+02:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO automation_attempts (id, started_at, finished_at) VALUES (?, ?, ?)",
            [
                ("legacy", "2026-01-10T09:00:00", "2026-01-10T10:00:00"),
                ("aware", "2026-01-10T09:00:00+01:00", "2026-01-10T10:00:00+01:00"),
            ],
        )

    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        runs = {
            row[0]: row[1:]
            for row in conn.execute("SELECT run_id, created_at, completed_at FROM pipeline_runs")
        }
        phases = conn.execute(
            "SELECT started_at, completed_at FROM phase_stats ORDER BY id"
        ).fetchall()
        attempts = conn.execute(
            "SELECT started_at, finished_at FROM automation_attempts ORDER BY id"
        ).fetchall()

    assert runs["winter"] == ("2026-01-10T08:00:00+00:00", "2026-01-10T09:00:00+00:00")
    assert runs["summer"] == ("2026-08-10T07:00:00+00:00", "2026-08-10T08:00:00+00:00")
    assert runs["aware"] == ("2026-08-10T07:00:00+00:00", "2026-08-10T08:00:00+00:00")
    assert phases == [
        ("2026-08-10T07:00:00+00:00", "2026-08-10T08:00:00+00:00"),
        ("2026-08-10T07:00:00+00:00", "2026-08-10T08:00:00+00:00"),
    ]
    assert attempts == [
        ("2026-01-10T08:00:00+00:00", "2026-01-10T09:00:00+00:00"),
        ("2026-01-10T08:00:00+00:00", "2026-01-10T09:00:00+00:00"),
    ]


def test_v11_canonical_utc_keeps_completion_order_and_cooldown_chronological(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different explicit offsets cannot invert completion history after migration."""
    db_path = tmp_path / "offset-order-v10.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 10)
    RunDatabase(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status, source,
                memory_type, memory_category, memory_people_json
            ) VALUES (?, ?, ?, 'completed', 'auto', ?, ?, '[]')
            """,
            [
                (
                    "older-plus-two",
                    "2026-08-10T09:00:00+02:00",
                    "2026-08-10T09:30:00+02:00",
                    "trip",
                    "trip",
                ),
                (
                    "newer-utc",
                    "2026-08-10T07:45:00+00:00",
                    "2026-08-10T08:00:00+00:00",
                    "monthly_highlights",
                    "monthly_review",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO automation_attempts (
                id, started_at, finished_at, outcome, reason
            ) VALUES (?, ?, ?, 'skipped', 'test')
            """,
            [
                (
                    "older-plus-two",
                    "2026-08-10T09:20:00+02:00",
                    "2026-08-10T09:21:00+02:00",
                ),
                (
                    "newer-utc",
                    "2026-08-10T08:05:00+00:00",
                    "2026-08-10T08:06:00+00:00",
                ),
            ],
        )

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 11)
    migrated = RunDatabase(db_path)
    runs = migrated.list_runs(
        status="completed",
        source="auto",
        order_by_completion=True,
    )

    assert [run.run_id for run in runs] == ["newer-utc", "older-plus-two"]
    assert runs[0].memory_category == "monthly_review"
    assert runs[0].completed_at == datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    assert runs[1].completed_at == datetime(2026, 8, 10, 7, 30, tzinfo=UTC)
    assert _cooldown_status(
        runs[0],
        24,
        now=datetime(2026, 8, 11, 7, 45, tzinfo=UTC),
    ).active
    last_attempt = AutomationStateStore(db_path).get_last_attempt()
    assert last_attempt is not None
    assert last_attempt.id == "newer-utc"
    assert last_attempt.started_at == datetime(2026, 8, 10, 8, 5, tzinfo=UTC)


def test_v11_conservatively_backfills_legacy_auto_identity(tmp_path: Path) -> None:
    """Only unambiguous completed-auto category and empty people fields are filled."""
    db_path = tmp_path / "identity-v10.db"
    _create_minimal_v10_database(db_path)
    mappings = {
        "year_in_review": "year_in_review",
        "trip": "trip",
        "multi_person": "multi_person",
        "on_this_day": "on_this_day",
        "person_spotlight": "person_spotlight",
        "monthly_highlights": "monthly_review",
    }
    with sqlite3.connect(db_path) as conn:
        for index, memory_type in enumerate(mappings):
            people_json = (None, "", "[]")[index % 3]
            conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, created_at, completed_at, status, source,
                    memory_type, memory_category, memory_people_json, person_name
                ) VALUES (?, '2026-08-10T09:00:00+00:00',
                          '2026-08-10T10:00:00+00:00', 'completed', 'auto', ?, NULL, ?, ?)
                """,
                (memory_type, memory_type, people_json, "  Straße\t Example "),
            )
        conn.executemany(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status, source,
                memory_type, memory_category, memory_people_json, person_name
            ) VALUES (?, '2026-08-10T09:00:00+00:00',
                      '2026-08-10T10:00:00+00:00', ?, ?, ?, ?, ?, ?)
            """,
            [
                ("explicit", "completed", "auto", "trip", "birthday", '["Keep Me"]', "Other"),
                ("manual", "completed", "manual", "trip", None, "[]", "Manual Person"),
                ("failed", "failed", "auto", "trip", None, "[]", "Failed Person"),
                ("unknown", "completed", "auto", "unknown", None, "[]", "Unknown Person"),
            ],
        )

    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT run_id, memory_category, memory_people_json FROM pipeline_runs"
            )
        }

    for memory_type, category in mappings.items():
        assert rows[memory_type] == (category, json.dumps(["strasse example"]))
    assert rows["explicit"] == ("birthday", '["Keep Me"]')
    assert rows["manual"] == (None, "[]")
    assert rows["failed"] == (None, "[]")
    assert rows["unknown"] == (None, json.dumps(["unknown person"]))


def test_brussels_daily_auto_run_is_not_inside_24_hour_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brussels_machine_timezone: None,
) -> None:
    """Local 09:00 on consecutive summer days is exactly 24 hours apart."""
    db_path = tmp_path / "cooldown-v10.db"
    current_schema_version = cache_database.SCHEMA_VERSION
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 10)
    RunDatabase(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status, source, memory_people_json
            ) VALUES ('daily-auto', '2026-08-10T08:50:00', '2026-08-10T09:00:00',
                      'completed', 'auto', '[]')
            """
        )
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", current_schema_version)
    config = Config(
        immich={"url": "http://immich.test:2283", "api_key": "test-key"},
        cache={"database": str(db_path), "directory": str(tmp_path / "cache")},
    )
    runner = AutoRunner(config)

    with (
        patch("immich_memories.automation.runner.datetime", wraps=datetime) as clock,
        patch.object(runner, "suggest", return_value=[]),
    ):
        clock.now.return_value = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
        result = runner.run_one(cooldown_hours=24)

    assert result.outcome is AutoOutcome.SKIPPED
    assert result.reason == "no eligible candidates"
