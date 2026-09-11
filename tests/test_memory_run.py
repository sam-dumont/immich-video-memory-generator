"""A cut survives its page: armed by key, followed through the attempt tree, recoverable."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.operations.editorial_attempt import EditorialAttempt
from immich_memories.operations.phases import OperationalPhase
from immich_memories.ui.pages.memory_run import (
    CUT_PHASES,
    arm_cut,
    attempt_root,
    elapsed_label,
    phase_of,
    read_latest_attempt,
    restore_cut_from_attempt,
)
from immich_memories.ui.state import AppState
from tests.conftest import make_asset, make_clip


def test_a_second_cut_is_refused_while_one_runs_and_its_reset_never_runs() -> None:
    state = AppState(pipeline_running=True, active_cut_key="running-key")
    resets: list[str] = []

    assert arm_cut(state, before=lambda: resets.append("reset")) is False

    assert resets == []
    assert state.active_cut_key == "running-key"


def test_arming_a_cut_forgets_the_previous_key_and_any_cancel() -> None:
    state = AppState(active_cut_key="old-key", cancel_requested=True)

    assert arm_cut(state) is True

    assert state.pipeline_running is True
    assert state.active_cut_key is None
    assert state.cancel_requested is False
    assert state.cut_armed_at is not None and state.cut_armed_at.tzinfo is UTC


def test_an_attempt_from_an_earlier_cut_of_the_same_brief_does_not_count(tmp_path: Path) -> None:
    """Sessions cutting the same brief share a key; only attempts started after arming are ours."""
    root = tmp_path / "editorial-runs" / "k"
    with EditorialAttempt(root, request={"key": "k"}):
        pass
    started = datetime.fromisoformat(read_latest_attempt(root)["started_at"])

    assert read_latest_attempt(root, since=started) is not None
    assert read_latest_attempt(root, since=started + timedelta(seconds=1)) is None


def test_the_attempt_root_needs_a_key() -> None:
    config = Config()

    assert attempt_root(AppState(config=config)) is None
    assert attempt_root(AppState(config=config, active_cut_key="k")) == (
        config.cache.cache_path / "editorial-runs" / "k"
    )


def test_a_live_attempt_reads_as_running_and_a_dropped_lease_as_interrupted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "editorial-runs" / "k"
    with EditorialAttempt(root, request={"key": "k"}) as attempt:
        attempt.stage("Editing the memory")
        live = read_latest_attempt(root)
    dropped = read_latest_attempt(root)

    assert live is not None and live["status"] == "running"
    assert live["directory"] == str(attempt.directory)
    assert dropped is not None and dropped["status"] == "incomplete"


def test_no_pointer_means_no_attempt(tmp_path: Path) -> None:
    assert read_latest_attempt(None) is None
    assert read_latest_attempt(tmp_path) is None


def test_stages_map_onto_the_cut_phases() -> None:
    assert phase_of(None).phase is OperationalPhase.ANALYSIS
    assert phase_of({"status": "running", "stage": "Preparing pixels: 3/9"}).phase is (
        OperationalPhase.ANALYSIS
    )
    editing = phase_of({"status": "running", "stage": "Editing the memory"})
    assert (editing.phase, editing.detail) == (OperationalPhase.SELECTION, "Editing the memory")
    assert phase_of({"status": "complete", "stage": "x"}).phase is OperationalPhase.COMPLETE
    assert CUT_PHASES[-1] is OperationalPhase.COMPLETE


def test_elapsed_counts_from_the_attempt_record() -> None:
    now = datetime(2024, 6, 30, 12, 1, 5, tzinfo=UTC)

    assert elapsed_label("2024-06-30T12:00:00+00:00", now) == "1m 05s"
    assert elapsed_label("2024-06-30T12:01:00+00:00", now) == "5s"
    assert elapsed_label(None, now) == ""
    assert elapsed_label("not a time", now) == ""


def test_a_finished_attempt_rebuilds_the_selection_over_a_reloaded_pool(tmp_path: Path) -> None:
    video = make_clip("video-1", duration=8.0)
    photo = make_asset("photo-1").model_copy(update={"type": AssetType.IMAGE})
    state = AppState(clips=[video], photo_assets=[photo], include_photos=True)
    (tmp_path / "plan.private.json").write_text(
        json.dumps(
            {
                "carriers": [
                    {"asset_id": "video-1", "kind": "video"},
                    {"asset_id": "photo-1", "kind": "still"},
                    {"asset_id": "gone", "kind": "still"},
                ]
            }
        )
    )
    (tmp_path / "render-projection.private.json").write_text(
        json.dumps(
            {
                "selected_ids": ["photo-1", "video-1", "gone"],
                "intervals": {"photo-1": [0.0, 4.0], "video-1": [1.0, 6.0], "gone": [0.0, 4.0]},
            }
        )
    )
    record = {"duration_realization": {"status": "near_target"}}

    restore_cut_from_attempt(state, tmp_path, record)

    assert [clip.asset.id for clip in state.pipeline_selected_clips] == ["photo-1", "video-1"]
    assert state.pipeline_selected_clips[1] is video
    assert state.clip_segments == {"photo-1": (0.0, 4.0), "video-1": (1.0, 6.0)}
    assert [s.render_mode for s in state.editorial_selections] == ["still", "motion"]
    assert state.selected_photo_ids == {"photo-1"}
    assert state.editorial_attempt_dir == tmp_path
    assert state.pipeline_result is not None
    assert state.pipeline_result["stats"]["recovered"] is True
    assert state.pipeline_result["stats"]["editorial_duration_realization"] == {
        "status": "near_target"
    }
