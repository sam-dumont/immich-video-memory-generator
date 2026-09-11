"""Behavior tests for Step 2 Auto/manual duration controls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType
from immich_memories.ui.pages.step2_review import (
    _duration_mode_label,
    _render_step2_header,
    _set_manual_target_minutes,
    _start_over_selection,
)
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


def test_auto_duration_label_shows_the_resolved_runtime() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    assert _duration_mode_label(state) == "Auto · 2m 30s"


def test_editing_duration_switches_to_manual_without_rounding() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    _set_manual_target_minutes(state, 3.25)

    assert state.duration_mode == "manual"
    assert state.target_duration == 3.25
    assert state.target_duration_seconds == 195.0


def test_start_over_clears_editorial_snapshot_but_keeps_loaded_library() -> None:
    library_clip = make_clip("library")
    state = AppState(
        clips=[library_clip],
        pipeline_selected_clips=[make_clip("planned")],
        selected_clip_ids={"planned"},
        clip_segments={"planned": (0.0, 4.0)},
        review_selected_mode=True,
        pipeline_result={"selected_clips": []},
    )
    state.editorial_selections = (EditorialSelection(asset_id="planned", render_mode="motion"),)

    _start_over_selection(state)

    assert state.clips == [library_clip]
    assert state.pipeline_selected_clips == []
    assert state.editorial_selections == ()
    assert state.pipeline_result is None
    assert state.selected_clip_ids == set()
    assert state.clip_segments == {}
    assert state.review_selected_mode is False


def test_review_refine_receives_retained_planner_carrier() -> None:
    library_clip = make_clip("library")
    planned_photo = make_clip("planned-photo")
    planned_photo.asset.type = AssetType.IMAGE
    state = AppState(
        memory_type="album",
        album_id="album-id",
        immich_url="https://immich.example.com",
        clips=[library_clip],
        pipeline_selected_clips=[planned_photo],
        selected_clip_ids={planned_photo.asset.id},
        review_selected_mode=True,
        analysis_cache=MagicMock(),
        thumbnail_cache=MagicMock(),
    )

    # WHY: the renderer builds NiceGUI widgets; the test only checks what it is handed.
    with patch(
        "immich_memories.ui.pages.step2_review._render_review_selected_clips"
    ) as render_review:
        handled = _render_step2_header(state)

    assert handled is True
    assert render_review.call_args.args[0] == [planned_photo]
    assert render_review.call_args.args[0][0] is planned_photo
