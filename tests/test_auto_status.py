"""Status and history contracts for smart automation."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from immich_memories.automation.candidate_discovery import ImmichDiscoveryError
from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import AutoRunner
from immich_memories.automation.runtime_provenance import CheckoutDrift, RuntimeProvenance
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.automation.status import SuggestOutcome, SuggestStatus
from immich_memories.automation.system_scheduler import SchedulerStatus
from immich_memories.automation.variety import RejectedCandidate, VarietyDecision
from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.tracking.models import DeliveryStatus, RunMetadata
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
    state.update_phase(
        attempt.id,
        PhaseEvent(OperationalPhase.ANALYSIS, 2, 10, "Analyzing clips", 3.5),
    )
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
        "pending_delivery_count",
        "oldest_pending_delivery",
        "notification_health",
        "runtime",
    }
    assert payload["last_attempt"]["outcome"] == "failed"
    assert payload["last_attempt"]["error"] == "connection refused"
    assert payload["last_attempt"]["last_phase"] == "analysis"
    assert payload["last_completed_auto_run"]["run_id"] == "trip-run"
    assert payload["recent_categories"] == ["trip", "birthday"]
    assert payload["cooldown"]["hours"] == 24
    assert payload["cooldown"]["active"] is True
    assert payload["notification_health"] is None
    assert payload["cooldown"]["until"] is not None
    assert payload["rejection_reasons"] == []
    assert payload["pending_delivery_count"] == 0
    assert payload["oldest_pending_delivery"] is None
    assert payload["scheduler"] == {
        "platform": "launchd",
        "installed": True,
        "active": False,
        "state": "inactive",
        "paths": [str(tmp_path / "scheduler.plist")],
    }
    get_scheduler.assert_called_once_with()


def test_status_reports_pending_queue_when_all_artifacts_are_missing(tmp_path: Path) -> None:
    """A missing file makes a retry unavailable, not the durable queue empty."""
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)
    completed = datetime(2026, 8, 12, 8, 30)
    db.save_run(
        RunMetadata(
            run_id="missing-pending",
            created_at=completed - timedelta(minutes=5),
            completed_at=completed,
            status="completed",
            source="auto",
            output_path=str(tmp_path / "missing-memory.mp4"),
            delivery_status=DeliveryStatus.PENDING,
            delivery_attempts=2,
            delivery_error="network unavailable",
            delivery_album="Daily Memories",
        )
    )
    scheduler = SchedulerStatus(platform="crontab", installed=True, active=True)

    with (
        patch.object(AutoRunner, "suggest", return_value=[]),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ),
    ):
        json_result = _invoke(config, ["auto", "status", "--json"])
        human_result = _invoke(config, ["auto", "status"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["pending_delivery_count"] == 1
    assert payload["oldest_pending_delivery"] is None
    assert human_result.exit_code == 0
    assert "Pending delivery queue: 1 item; no retryable artifact exists" in human_result.output


def test_status_exposes_typed_oldest_retryable_delivery_details(tmp_path: Path) -> None:
    """Operators can identify the oldest actionable item without querying SQLite."""
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)
    output = tmp_path / "retry-me.mp4"
    output.write_bytes(b"video")
    completed = datetime(2026, 8, 12, 8, 30)
    db.save_run(
        RunMetadata(
            run_id="retry-me",
            created_at=completed - timedelta(minutes=5),
            completed_at=completed,
            status="completed",
            source="auto",
            output_path=str(output),
            delivery_status=DeliveryStatus.PENDING,
            delivery_attempts=1,
            delivery_error="temporary error",
            delivery_album="Daily Memories",
        )
    )

    payload = AutoRunner(config).status().to_dict()

    assert payload["pending_delivery_count"] == 1
    assert payload["oldest_pending_delivery"] == {
        "run_id": "retry-me",
        "completed_at": completed.isoformat(),
        "output_path": str(output),
        "delivery_attempts": 1,
        "delivery_error": "temporary error",
        "delivery_album": "Daily Memories",
    }


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


def test_status_names_the_code_that_would_run_and_flags_it_when_stale(tmp_path: Path) -> None:
    """#573 stayed invisible for a week because no surface said which code was running."""
    config = _config(tmp_path)
    scheduler = SchedulerStatus(platform="launchd", installed=True, active=True)
    stale = RuntimeProvenance(
        version="1.2.3",
        checkout=Path("/home/me/.immich-memories/runtime"),
        commit="ea892ad",
        drift=CheckoutDrift(upstream="origin/main", commits_behind=32),
    )
    # WHY: suggest reads the Immich library; get_scheduler_status shells out to launchctl.
    with (
        patch.object(AutoRunner, "suggest", return_value=[]),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ),
        # WHY: runtime_provenance shells out to git against the real checkout
        patch(
            "immich_memories.automation.runtime_provenance.runtime_provenance",
            return_value=stale,
        ),
    ):
        json_result = _invoke(config, ["auto", "status", "--json"])
        human_result = _invoke(config, ["auto", "status"])

    assert json_result.exit_code == 0
    assert json.loads(json_result.output)["runtime"] == {
        "version": "1.2.3",
        "checkout": "/home/me/.immich-memories/runtime",
        "commit": "ea892ad",
        "upstream": "origin/main",
        "commits_behind": 32,
        "stale": True,
    }
    assert human_result.exit_code == 0
    assert "ea892ad" in human_result.output
    assert "32 commit(s)" in human_result.output


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


def test_status_keeps_durable_state_when_live_discovery_fails(tmp_path: Path) -> None:
    """A post-preflight Immich failure degrades only the live suggestion snapshot."""
    from immich_memories.preflight import CheckStatus

    secret = "immich-status-secret"  # noqa: S105 - synthetic credential for redaction test
    config = Config(
        immich={"url": "http://immich.test:2283", "api_key": secret},
        cache={
            "database": str(tmp_path / "status.db"),
            "directory": str(tmp_path / "cache"),
        },
    )
    state = AutomationStateStore(config.cache.database_path)
    prior_attempt = state.start_attempt("previous daily wake")
    state.finish_attempt(
        prior_attempt.id,
        AutoOutcome.SKIPPED,
        "cooldown active",
    )
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
    generate = MagicMock()
    runner = AutoRunner(config, execute=generate)
    stale_candidate = MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2026, 7, 1),
        date_range_end=date(2026, 7, 31),
        person_names=[],
        memory_key="monthly:2026-07",
        score=0.7,
        reason="stale status snapshot",
        asset_count=100,
    )
    runner.last_variety_decision = VarietyDecision(
        eligible=[],
        rejected=[
            RejectedCandidate(
                candidate=stale_candidate,
                rule="same_category_as_previous",
            )
        ],
    )
    scheduler = SchedulerStatus("launchd", True, False, (tmp_path / "scheduler.plist",))
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_time_buckets.side_effect = RuntimeError(
        ("x" * 3000) + f": metadata request rejected key={secret}"
    )

    def row_counts() -> tuple[int, int]:
        with sqlite3.connect(config.cache.database_path) as conn:
            attempts = conn.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0]
            runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
        return attempts, runs

    before = row_counts()
    with (
        patch("immich_memories.preflight.check_immich") as preflight,
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch("immich_memories.automation.runner.AutoRunner", return_value=runner),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ) as inspect_scheduler,
        patch("immich_memories.automation.system_scheduler.install_scheduler") as install,
        patch("immich_memories.automation.system_scheduler.uninstall_scheduler") as uninstall,
    ):
        preflight.return_value = MagicMock(status=CheckStatus.OK)
        json_result = _invoke(config, ["auto", "status", "--json"])
        human_result = _invoke(config, ["auto", "status"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert json_result.stdout.count("\n") == 1
    assert payload["last_attempt"]["id"] == prior_attempt.id
    assert payload["last_attempt"]["outcome"] == "skipped"
    assert payload["last_completed_auto_run"]["run_id"] == "last-good-auto"
    assert payload["recent_categories"] == ["trip"]
    assert payload["scheduler"]["state"] == "inactive"
    assert payload["rejection_reasons"] == []
    assert payload["suggestion"]["outcome"] == "discovery_failed"
    assert len(payload["suggestion"]["error"]) <= 2000
    assert secret not in json_result.output
    assert human_result.exit_code == 0
    assert "Scheduler: launchd, installed, inactive" in human_result.output
    assert "Last completed auto run: last-good-auto (trip)" in human_result.output
    assert "Suggestion snapshot unavailable" in human_result.output
    assert secret not in human_result.output
    assert row_counts() == before
    assert inspect_scheduler.call_count == 2
    generate.assert_not_called()
    install.assert_not_called()
    uninstall.assert_not_called()


def test_direct_suggest_still_raises_post_preflight_discovery_errors(tmp_path: Path) -> None:
    """Only status is best-effort; direct discovery retains its failing contract."""
    from immich_memories.preflight import CheckStatus

    config = _config(tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_time_buckets.side_effect = RuntimeError("metadata failed")

    with (
        patch(
            "immich_memories.preflight.check_immich",
            return_value=MagicMock(status=CheckStatus.OK),
        ),
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        pytest.raises(ImmichDiscoveryError, match="metadata failed"),
    ):
        AutoRunner(config).suggest(limit=1)


def test_status_does_not_hide_generated_key_database_failures(tmp_path: Path) -> None:
    """Durable-state faults remain fatal instead of impersonating an offline Immich."""
    from immich_memories.preflight import CheckStatus

    runner = AutoRunner(_config(tmp_path))
    with (
        patch(
            "immich_memories.preflight.check_immich",
            return_value=MagicMock(status=CheckStatus.OK),
        ),
        patch.object(
            runner.db,
            "get_generated_memory_keys",
            side_effect=RuntimeError("generated-key database read failed"),
        ),
        patch("immich_memories.api.immich.SyncImmichClient") as client,
        pytest.raises(RuntimeError, match="generated-key database read failed"),
    ):
        runner.status(refresh_suggestion=True)

    client.assert_not_called()


def test_status_does_not_hide_detector_programming_failures(tmp_path: Path) -> None:
    """Local candidate logic remains strict after a healthy library snapshot."""
    from immich_memories.preflight import CheckStatus

    runner = AutoRunner(_config(tmp_path))
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_time_buckets.return_value = []
    client.get_all_people.return_value = []

    with (
        patch(
            "immich_memories.preflight.check_immich",
            return_value=MagicMock(status=CheckStatus.OK),
        ),
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch(
            "immich_memories.automation.candidate_discovery._run_all_detectors",
            side_effect=RuntimeError("detector invariant failed"),
        ),
        pytest.raises(RuntimeError, match="detector invariant failed"),
    ):
        runner.status(refresh_suggestion=True)


def test_cooldown_counts_from_auto_run_start_not_completion(tmp_path: Path) -> None:
    """A daily timer fires at the same wall-clock time; run duration must not push it out (#330)."""
    config = _config(tmp_path)
    runner = AutoRunner(config)
    now = datetime.now(tz=UTC)
    runner.db.save_run(
        RunMetadata(
            run_id="yesterdays-auto-run",
            created_at=now - timedelta(hours=24),
            completed_at=now - timedelta(hours=23),
            status="completed",
            source="auto",
        )
    )

    status = runner.status(cooldown_hours=24)

    assert status.cooldown.active is False
    assert status.last_completed_auto_run is not None
    assert status.last_completed_auto_run.run_id == "yesterdays-auto-run"


def test_status_preserves_explicit_zero_cooldown(tmp_path: Path) -> None:
    """An explicit zero is a value, not a request for the configured default."""
    status = AutoRunner(_config(tmp_path)).status(cooldown_hours=0)

    assert status.cooldown.hours == 0
    assert status.cooldown.active is False


def test_status_exposes_notification_cooldown_without_changing_readiness(tmp_path: Path) -> None:
    from immich_memories.automation.notification_state import (
        NotificationFailureCategory,
        NotificationStateStore,
    )

    config = _config(tmp_path)
    config.notifications.enabled = True
    config.notifications.urls = ["ntfy://topic"]
    NotificationStateStore(config.cache.database_path).record_failure(
        NotificationFailureCategory.QUOTA
    )

    payload = AutoRunner(config).status().to_dict()

    assert payload["notification_health"]["cooldown_active"] is True
    assert payload["notification_health"]["failure_category"] == "quota"
    assert "ntfy" not in json.dumps(payload["notification_health"])


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


def test_status_real_suggest_flow_is_read_only(tmp_path: Path) -> None:
    """Status scans once without attempts, pipeline runs, generation, or scheduler mutation."""
    from immich_memories.preflight import CheckStatus

    config = _config(tmp_path)
    execute = MagicMock()
    runner = AutoRunner(config, execute=execute)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_time_buckets.return_value = []
    client.get_all_people.return_value = []

    def row_counts() -> tuple[int, int]:
        with sqlite3.connect(config.cache.database_path) as conn:
            attempts = conn.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0]
            runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
        return attempts, runs

    before = row_counts()
    scheduler = SchedulerStatus("launchd", True, False)
    with (
        patch("immich_memories.preflight.check_immich") as preflight,
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch("immich_memories.automation.runner.AutoRunner", return_value=runner),
        patch(
            "immich_memories.automation.system_scheduler.get_scheduler_status",
            return_value=scheduler,
        ) as inspect_scheduler,
        patch("immich_memories.automation.system_scheduler.install_scheduler") as install,
        patch("immich_memories.automation.system_scheduler.uninstall_scheduler") as uninstall,
    ):
        preflight.return_value = MagicMock(status=CheckStatus.OK)
        result = _invoke(config, ["auto", "status", "--json"])

    assert result.exit_code == 0
    assert row_counts() == before == (0, 0)
    assert json.loads(result.stdout)["suggestion"]["outcome"] == "ready"
    preflight.assert_called_once_with(config)
    client.get_time_buckets.assert_called_once_with()
    execute.assert_not_called()
    inspect_scheduler.assert_called_once_with()
    install.assert_not_called()
    uninstall.assert_not_called()
