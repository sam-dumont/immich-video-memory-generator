"""Public operational lifecycle contracts."""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.cache import database as cache_database
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.config_loader import Config
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.tracking.run_tracker import RunTracker


def test_phase_event_exposes_the_outer_lifecycle_contract() -> None:
    event = PhaseEvent(
        phase=OperationalPhase.DISCOVERY,
        current=0,
        total=0,
        message="Discovering eligible memories",
        elapsed_seconds=0.0,
    )

    assert [phase.value for phase in OperationalPhase] == [
        "discovery",
        "download",
        "analysis",
        "selection",
        "render",
        "music",
        "delivery",
        "complete",
    ]
    assert event.phase is OperationalPhase.DISCOVERY
    assert event.message == "Discovering eligible memories"


def test_zero_item_cached_phase_keeps_its_label() -> None:
    event = PhaseEvent(
        phase=OperationalPhase.DOWNLOAD,
        current=0,
        total=0,
        message="Downloads already cached",
        elapsed_seconds=0.0,
    )

    assert [phase.order for phase in OperationalPhase] == list(range(8))
    assert event.to_dict()["label"] == "Download"


def test_v14_adds_last_phase_without_rewriting_populated_v13_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "v13.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 13)
    VideoAnalysisCache(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, created_at, status) VALUES (?, ?, ?)",
            ("existing-run", "2026-08-12T08:00:00+00:00", "running"),
        )
        conn.execute(
            "INSERT INTO automation_attempts (id, started_at, outcome, reason) VALUES (?, ?, ?, ?)",
            ("existing-attempt", "2026-08-12T08:00:00+00:00", "running", "daily wake"),
        )
        conn.commit()

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 14)
    VideoAnalysisCache(db_path)
    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        run = conn.execute(
            "SELECT run_id, last_phase FROM pipeline_runs WHERE run_id = ?", ("existing-run",)
        ).fetchone()
        attempt = conn.execute(
            "SELECT id, last_phase FROM automation_attempts WHERE id = ?", ("existing-attempt",)
        ).fetchone()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert run == ("existing-run", None)
    assert attempt == ("existing-attempt", None)
    assert version == 14


def test_failure_retains_the_last_monotonic_phase_on_run_and_attempt(tmp_path: Path) -> None:
    db_path = tmp_path / "phases.db"
    state = AutomationStateStore(db_path)
    attempt = state.start_attempt("daily wake")
    tracker = RunTracker("phase-run", db_path=db_path, capture_system=False)
    tracker.start_run(automation_attempt_id=attempt.id, source="auto")
    render = PhaseEvent(OperationalPhase.RENDER, 0, 1, "Rendering memory", 1.0)
    stale = PhaseEvent(OperationalPhase.ANALYSIS, 0, 1, "Analyzing clips", 2.0)

    assert tracker.record_phase_event(render) is True
    assert tracker.record_phase_event(stale) is False
    tracker.fail_run("encoder failed")

    run = tracker.db.get_run("phase-run")
    persisted_attempt = state.get_last_attempt()
    assert run is not None
    assert run.status == "failed"
    assert run.last_phase is OperationalPhase.RENDER
    assert persisted_attempt is not None
    assert persisted_attempt.last_phase is OperationalPhase.RENDER


def test_rejected_backward_phase_is_not_sent_to_observers() -> None:
    from dataclasses import dataclass

    from immich_memories.generate import GenerationParams, _emit_phase

    @dataclass
    class Tracker:
        def record_phase_event(self, _event: PhaseEvent) -> bool:
            return False

    observed: list[PhaseEvent] = []
    params = GenerationParams.__new__(GenerationParams)
    params.phase_callback = observed.append
    _emit_phase(params, Tracker(), OperationalPhase.DOWNLOAD, 0, 1, "Preparing sources")

    assert observed == []


def test_analysis_failure_retains_analysis_on_its_exact_automation_attempt(tmp_path: Path) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.timeperiod import DateRange

    config = Config(
        cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")}
    )
    state = AutomationStateStore(config.cache.database_path)
    attempt = state.start_attempt("daily wake")
    clip = MagicMock()
    clip.asset.id = "asset-1"
    progress = MagicMock()
    with (
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
        pytest.raises(RuntimeError, match="analysis exploded"),
    ):
        pipeline_type.return_value.run_analysis.side_effect = RuntimeError("analysis exploded")
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=progress,
            duration=60,
            transition="cut",
            music=None,
            output_path=tmp_path / "memory.mp4",
            memory_type="trip",
            person_names=[],
            date_range=DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2)),
            upload_to_immich=False,
            album=None,
            source="auto",
            automation_attempt_id=attempt.id,
        )

    persisted = state.get_last_attempt()
    assert persisted is not None
    assert persisted.last_phase is OperationalPhase.ANALYSIS
    assert RunTracker("unused", db_path=config.cache.database_path).db.list_runs() == []
    assert any(
        call.kwargs.get("description") == "Analyzing clips"
        for call in progress.update.call_args_list
    )


def test_ui_and_cli_render_the_exact_phase_event_message() -> None:
    from immich_memories.ui.pages._step4_generate import _set_phase_status

    status = MagicMock()
    event = PhaseEvent(OperationalPhase.RENDER, 0, 1, "Exact shared message", 1.0)

    _set_phase_status(status, event)

    status.set_text.assert_called_once_with(event.message)


def test_visible_progress_labels_keep_selection_when_generation_reports_local_download() -> None:
    """CLI and UI show the same monotonic outer label for local source preparation."""
    from immich_memories.operations.phases import format_phase_progress

    assert format_phase_progress("selection", "Downloading clips") == "Selection: Downloading clips"
