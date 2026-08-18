"""Step 4 must recover a generation that outlived the browser page (#322)."""

from __future__ import annotations

from pathlib import Path

from immich_memories.config_loader import Config
from immich_memories.processing.output_contract import OutputProbe
from immich_memories.tracking import RunDatabase, RunTracker
from immich_memories.ui.pages.step4_recovery import recover_active_run
from immich_memories.ui.state import AppState


def _state(tmp_path: Path) -> AppState:
    config = Config(cache={"database": str(tmp_path / "runs.db")})
    return AppState(config=config)


def _tracker(state: AppState, run_id: str) -> RunTracker:
    return RunTracker(run_id, db_path=state.config.cache.database_path)


def _complete(tracker: RunTracker, output_path: Path) -> None:
    output_path.write_bytes(b"video")
    probe = OutputProbe(
        codec="h264",
        duration_seconds=1.0,
        size_bytes=5,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1280,
        height=720,
        decoded_frames=30,
        container="mp4",
    )
    tracker.complete_artifact(
        output_path, probe, warnings=["one warning"], clips_analyzed=1, clips_selected=1
    )


def test_no_active_run_means_nothing_to_recover(tmp_path: Path) -> None:
    assert recover_active_run(_state(tmp_path)) is None


def test_completed_run_restores_output_into_the_session(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.active_run_id = "run-done"
    tracker = _tracker(state, "run-done")
    tracker.start_run(source="manual")
    _complete(tracker, tmp_path / "memory.mp4")

    recovered = recover_active_run(state)

    assert recovered is not None
    assert recovered.status == "completed"
    assert state.output_path == tmp_path / "memory.mp4"
    assert state.generation_warning == "one warning"


def test_running_run_is_reported_as_in_progress(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.active_run_id = "run-live"
    _tracker(state, "run-live").start_run(source="manual")

    recovered = recover_active_run(state)

    assert recovered is not None
    assert recovered.status == "running"
    assert recovered.run is not None and recovered.run.run_id == "run-live"
    assert state.output_path is None


def test_run_that_has_not_written_its_row_yet_still_counts_as_running(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.active_run_id = "run-starting"
    RunDatabase(state.config.cache.database_path)  # schema only, no row

    recovered = recover_active_run(state)

    assert recovered is not None
    assert recovered.status == "running"
    assert recovered.run is None


def test_failed_run_is_reported_and_forgotten(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.active_run_id = "run-failed"
    tracker = _tracker(state, "run-failed")
    tracker.start_run(source="manual")
    tracker.fail_run("encoder exploded")

    recovered = recover_active_run(state)

    assert recovered is not None
    assert recovered.status == "failed"
    assert state.active_run_id is None


def test_running_row_older_than_the_stale_window_is_reported_stale_and_forgotten(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from immich_memories.tracking.models import RunMetadata

    state = _state(tmp_path)
    state.active_run_id = "run-orphan"
    RunDatabase(state.config.cache.database_path).save_run(
        RunMetadata(
            run_id="run-orphan",
            created_at=datetime.now(tz=UTC) - timedelta(hours=5),
            status="running",
            source="manual",
        )
    )

    recovered = recover_active_run(state)

    assert recovered is not None
    assert recovered.status == "stale"
    assert state.active_run_id is None
