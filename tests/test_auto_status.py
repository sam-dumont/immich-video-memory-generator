"""Status and history contracts for smart automation."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import AutoRunner, SuggestOutcome, SuggestStatus
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.automation.system_scheduler import SchedulerStatus
from immich_memories.automation.variety import RejectedCandidate, VarietyDecision
from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.tracking.models import RunMetadata
from immich_memories.tracking.run_database import RunDatabase


def _config(tmp_path: Path) -> Config:
    return Config(
        cache={
            "database": str(tmp_path / "status.db"),
            "directory": str(tmp_path / "cache"),
        }
    )


def _invoke(config: Config, args: list[str]):
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return CliRunner().invoke(main, args, catch_exceptions=False)


def test_history_filters_auto_runs_before_applying_limit(tmp_path: Path) -> None:
    """A newer manual completion cannot hide an older automation completion."""
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)
    started = datetime(2026, 8, 10, 9, 0)
    db.save_run(
        RunMetadata(
            run_id="older-auto",
            created_at=started,
            completed_at=started + timedelta(minutes=10),
            status="completed",
            source="auto",
            memory_type="trip",
        )
    )
    db.save_run(
        RunMetadata(
            run_id="newer-manual",
            created_at=started + timedelta(days=1),
            completed_at=started + timedelta(days=1, minutes=10),
            status="completed",
            source="manual",
            memory_type="monthly_highlights",
        )
    )

    result = _invoke(config, ["auto", "history", "--limit", "1"])

    assert result.exit_code == 0
    assert "trip" in result.output
    assert "No auto-generated memories found" not in result.output


def test_status_json_reports_durable_attempt_rotation_and_scheduler(tmp_path: Path) -> None:
    """Status combines durable state without running detection or changing the scheduler."""
    config = _config(tmp_path)
    state = AutomationStateStore(config.cache.database_path)
    attempt = state.start_attempt("daily wake")
    state.finish_attempt(
        attempt.id,
        AutoOutcome.FAILED,
        "Immich preflight failed",
        error="connection refused",
    )
    db = RunDatabase(config.cache.database_path)
    now = datetime.now()
    for run_id, started, completed, category in (
        ("birthday-run", now - timedelta(hours=4), now - timedelta(hours=3), "birthday"),
        ("trip-run", now - timedelta(hours=6), now - timedelta(hours=2), "trip"),
    ):
        db.save_run(
            RunMetadata(
                run_id=run_id,
                created_at=started,
                completed_at=completed,
                status="completed",
                source="auto",
                memory_type=category,
                memory_category=category,
            )
        )
    db.save_run(
        RunMetadata(
            run_id="newest-manual",
            created_at=now - timedelta(hours=1),
            completed_at=now,
            status="completed",
            source="manual",
            memory_type="monthly_highlights",
            memory_category="monthly_review",
        )
    )

    scheduler = SchedulerStatus(
        platform="launchd",
        installed=True,
        active=False,
        paths=(tmp_path / "scheduler.plist",),
    )
    with (
        patch.object(AutoRunner, "suggest", return_value=[]),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ) as get_scheduler,
    ):
        result = _invoke(config, ["auto", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {
        "last_attempt",
        "last_completed_auto_run",
        "cooldown",
        "recent_categories",
        "rejection_reasons",
        "suggestion",
        "scheduler",
    }
    assert payload["last_attempt"]["outcome"] == "failed"
    assert payload["last_attempt"]["error"] == "connection refused"
    assert payload["last_completed_auto_run"]["run_id"] == "trip-run"
    assert payload["recent_categories"] == ["trip", "birthday"]
    assert payload["cooldown"]["hours"] == 24
    assert payload["cooldown"]["active"] is True
    assert payload["cooldown"]["until"] is not None
    assert payload["rejection_reasons"] == []
    assert payload["scheduler"] == {
        "platform": "launchd",
        "installed": True,
        "active": False,
        "state": "inactive",
        "paths": [str(tmp_path / "scheduler.plist")],
    }
    get_scheduler.assert_called_once_with()


def test_status_refreshes_and_reports_current_rejection_reasons(tmp_path: Path) -> None:
    """A fresh status process computes one read-only candidate snapshot for explainability."""
    config = _config(tmp_path)
    RunDatabase(config.cache.database_path)
    rejected = MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2026, 7, 1),
        date_range_end=date(2026, 7, 31),
        person_names=[],
        memory_key="monthly:2026-07",
        score=0.7,
        reason="July",
        asset_count=100,
    )

    def suggest_snapshot(runner: AutoRunner, limit: int) -> list[MemoryCandidate]:
        assert limit == 1
        runner.last_variety_decision = VarietyDecision(
            eligible=[],
            rejected=[
                RejectedCandidate(
                    candidate=rejected,
                    rule="same_category_as_previous",
                )
            ],
        )
        return []

    scheduler = SchedulerStatus(platform="launchd", installed=False, active=None)
    with (
        patch.object(AutoRunner, "suggest", autospec=True, side_effect=suggest_snapshot) as suggest,
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ),
    ):
        result = _invoke(config, ["auto", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["suggestion"] == {"outcome": "ready", "error": None}
    assert payload["rejection_reasons"] == ["same_category_as_previous"]
    suggest.assert_called_once()


def test_status_json_is_one_document_on_a_fresh_database(tmp_path: Path) -> None:
    """First-use schema setup must not contaminate machine-readable output."""
    config = _config(tmp_path)
    scheduler = SchedulerStatus(platform="crontab", installed=False, active=None)
    with (
        patch.object(AutoRunner, "suggest", return_value=[]),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ),
    ):
        result = _invoke(config, ["auto", "status", "--json"])

    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1
    assert json.loads(result.stdout)["scheduler"]["state"] == "unknown"


def test_status_keeps_durable_state_when_suggestion_preflight_fails(tmp_path: Path) -> None:
    """Offline Immich makes suggestions unavailable, not operational status unavailable."""
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)
    completed = datetime.now() - timedelta(hours=3)
    db.save_run(
        RunMetadata(
            run_id="last-good-auto",
            created_at=completed - timedelta(hours=1),
            completed_at=completed,
            status="completed",
            source="auto",
            memory_category="trip",
        )
    )

    def failed_snapshot(runner: AutoRunner, limit: int) -> list[MemoryCandidate]:
        assert limit == 1
        runner.last_suggest_status = SuggestStatus(
            outcome=SuggestOutcome.PREFLIGHT_FAILED,
            error="Immich preflight failed: offline",
        )
        return []

    with (
        patch.object(AutoRunner, "suggest", autospec=True, side_effect=failed_snapshot),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=SchedulerStatus("systemd", True, None),
        ),
    ):
        result = _invoke(config, ["auto", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["last_completed_auto_run"]["run_id"] == "last-good-auto"
    assert payload["recent_categories"] == ["trip"]
    assert payload["rejection_reasons"] == []
    assert payload["suggestion"] == {
        "outcome": "preflight_failed",
        "error": "Immich preflight failed: offline",
    }


def test_cooldown_uses_auto_completion_time_not_start_time(tmp_path: Path) -> None:
    """A long run completed recently even if it started before the cooldown window."""
    config = _config(tmp_path)
    runner = AutoRunner(config)
    now = datetime.now()
    runner.db.save_run(
        RunMetadata(
            run_id="long-auto-run",
            created_at=now - timedelta(hours=30),
            completed_at=now - timedelta(hours=1),
            status="completed",
            source="auto",
        )
    )

    status = runner.status(cooldown_hours=24)

    assert status.cooldown.active is True
    assert status.last_completed_auto_run is not None
    assert status.last_completed_auto_run.run_id == "long-auto-run"


def test_status_human_output_distinguishes_installed_from_unknown_active(tmp_path: Path) -> None:
    """Human status does not describe a mere scheduler file as active."""
    config = _config(tmp_path)
    RunDatabase(config.cache.database_path)
    with (
        patch.object(AutoRunner, "suggest", return_value=[]),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=SchedulerStatus("launchd", True, None),
        ),
    ):
        result = _invoke(config, ["auto", "status"])

    assert result.exit_code == 0
    assert "Scheduler: launchd, installed, unknown" in result.output
    assert "Cooldown: ready (24h)" in result.output
