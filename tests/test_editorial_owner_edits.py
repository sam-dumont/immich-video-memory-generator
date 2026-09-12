"""Explicit review changes retain selected material and pass ordinary render guards."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_clips import _validated_render_directives
from immich_memories.processing.editorial_live_render import validate_editorial_live_clip
from immich_memories.processing.editorial_owner_edits import project_editorial_owner_edits
from immich_memories.processing.editorial_timing import (
    bind_editorial_timeline,
    prepare_certified_timeline,
    timing_policy_for_params,
)
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_clip


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("owner review projection must not call models, media tools or services")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("platform.platform", lambda: "macOS-15.0-arm64")


def original_params(tmp_path, *, product="custom"):
    clips = [
        make_clip(f"chosen-{i}", duration=12, file_created_at=datetime(2020 + i, 1, 1, tzinfo=UTC))
        for i in range(4)
    ]
    clips[0].asset.type = AssetType.IMAGE
    live = clips[2]
    live.asset.type = AssetType.IMAGE
    live.asset.live_photo_video_id = "live-companion"
    material = LiveRenderMaterial((LiveSourceEntry(live.asset.id, "live-companion", 0, 0, 12),))
    live.live_burst_still_ids = list(material.still_ids)
    live.live_burst_video_ids = list(material.video_ids)
    live.live_burst_trim_points = list(material.trim_points)
    live.live_burst_shutter_timestamps = list(material.shutter_timestamps)
    live.live_burst_material = material.as_dict()
    live.editorial_live_manifest = {
        "version": "editorial-live-render-v1",
        "material": material.as_dict(),
        "selected_interval": [0, 4],
    }
    config = Config()
    params = GenerationParams(
        clips=clips,
        output_path=tmp_path / "memory.mp4",
        config=config,
        target_duration_seconds=120 if product == "on_this_day" else 60,
        memory_type=product,
        transition=config.defaults.transition,
        transition_duration=config.defaults.transition_duration,
        clip_segments={clip.asset.id: (0, 4) for clip in clips},
        editorial_selections=tuple(
            EditorialSelection(clip.asset.id, 0, 4, mode, frame)
            for clip, mode, frame in zip(
                clips, ("still", "motion", "motion", "still"), (None, None, None, 1.5), strict=True
            )
        ),
    )
    policy = timing_policy_for_params(params)
    timeline = policy.resolve(
        [{"asset_id": c.asset.id, "seconds": 4} for c in clips],
        {c.asset.id: c.asset for c in clips},
    )
    params.editorial_render_timing = bind_editorial_timeline(
        policy,
        timeline,
        [c.asset.id for c in clips],
    )
    params.timeline_plan = timeline
    return params


def project(params, *, selected_ids=None, segments=None, policy=None):
    return project_editorial_owner_edits(
        original_clips=params.clips,
        original_selections=params.editorial_selections,
        original_binding=params.editorial_render_timing,
        selected_ids=[c.asset.id for c in params.clips] if selected_ids is None else selected_ids,
        requested_segments=params.clip_segments if segments is None else segments,
        policy=timing_policy_for_params(params) if policy is None else policy,
    )


def rendered_params(original, projection):
    return replace(
        original,
        clips=list(projection.clips),
        editorial_selections=projection.selections,
        clip_segments=projection.segments,
        timeline_plan=projection.timeline,
        editorial_render_timing=projection.binding,
    )


def test_unchanged_review_reuses_exact_binding_without_edit_record(tmp_path):
    params = original_params(tmp_path)
    result = project(params)
    assert result.binding is params.editorial_render_timing
    assert result.record is None
    assert result.selections == params.editorial_selections
    assert all(left is right for left, right in zip(result.clips, params.clips, strict=True))
    prepare_certified_timeline(rendered_params(params, result))


def test_removal_rebinds_survivors_in_story_order_and_recomputes_title_overhead(tmp_path):
    params = original_params(tmp_path, product="on_this_day")
    before = deepcopy(params)
    result = project(params, selected_ids=["chosen-3", "chosen-0"])
    assert [c.asset.id for c in result.clips] == ["chosen-0", "chosen-3"]
    assert result.binding["source_ids"] == ["chosen-0", "chosen-3"]
    assert result.timeline.max_dividers == 1
    assert result.timeline.title_budget < params.timeline_plan.title_budget
    assert result.record["removed_asset_ids"] == ["chosen-1", "chosen-2"]
    assert result.record["interval_edits"] == []
    assert params == before
    updated = rendered_params(params, result)
    prepare_certified_timeline(updated)
    assert list(_validated_render_directives(updated)) == ["chosen-0", "chosen-3"]


@pytest.mark.parametrize("asset_id", ["chosen-1", "chosen-2"])
def test_motion_trim_updates_directive_and_recertifies_same_live_material(tmp_path, asset_id):
    params = original_params(tmp_path)
    before = deepcopy(params)
    result = project(params, segments={**params.clip_segments, asset_id: (1.0, 3.0)})
    updated = rendered_params(params, result)
    prepare_certified_timeline(updated)
    directive = _validated_render_directives(updated)[asset_id]
    assert (directive.start_time, directive.end_time) == (1, 3)
    assert updated.clip_segments[asset_id] == (1, 3)
    if asset_id == "chosen-2":
        live = next(c for c in updated.clips if c.asset.id == asset_id)
        assert validate_editorial_live_clip(live).as_dict() == params.clips[2].live_burst_material
        assert live.editorial_live_manifest["selected_interval"] == [1, 3]
        assert live.editorial_live_manifest is not params.clips[2].editorial_live_manifest
    assert params == before


@pytest.mark.parametrize("asset_id", ["chosen-0", "chosen-3"])
def test_still_range_changes_hold_without_changing_selected_picture(tmp_path, asset_id):
    params = original_params(tmp_path)
    result = project(params, segments={**params.clip_segments, asset_id: (2, 7)})
    updated = rendered_params(params, result)
    directive = _validated_render_directives(updated)[asset_id]
    assert updated.clip_segments[asset_id] == (0, 5)
    assert (directive.start_time, directive.end_time) == (0, 5)
    assert directive.render_frame_seconds == (1.5 if asset_id == "chosen-3" else None)
    assert result.record["interval_edits"][0]["render_mode"] == "still"


@pytest.mark.parametrize("change", ["transition", "titles"])
def test_explicit_render_settings_rebind_same_selected_material(tmp_path, change):
    params = original_params(tmp_path)
    requested = deepcopy(params)
    if change == "transition":
        requested.transition = "cut"
    else:
        requested.config.title_screens.enabled = False
    result = project(params, policy=timing_policy_for_params(requested))
    assert result.record["timing_policy_changed"] is True
    assert result.record["interval_edits"] == []
    assert result.record["removed_asset_ids"] == []
    assert result.clips == tuple(params.clips)
    assert result.selections == params.editorial_selections
    prepare_certified_timeline(rendered_params(requested, result))


@pytest.mark.parametrize(
    "interval",
    [
        (0, 0),
        (4, 2),
        (-1, 3),
        (0, 13),
        (0, float("nan")),
        (0, float("inf")),
        (True, 2),
        None,
        (0,),
        (0, 1, 2),
        (None, 2),
    ],
)
def test_invalid_motion_trim_fails_before_media(tmp_path, interval):
    params = original_params(tmp_path)
    with pytest.raises(ValueError, match="Review trim"):
        project(params, segments={**params.clip_segments, "chosen-2": interval})


@pytest.mark.parametrize("ids", [[], ["added"], ["chosen-0", "chosen-0"]])
def test_empty_added_or_duplicated_material_is_rejected(tmp_path, ids):
    with pytest.raises(ValueError, match="Keep at least|outside the chosen memory"):
        project(original_params(tmp_path), selected_ids=ids)


def test_owner_edit_cannot_bypass_original_binding_or_live_validation(tmp_path):
    params = original_params(tmp_path)
    params.editorial_render_timing["source_ids"].pop()
    with pytest.raises(ValueError, match="binding changed"):
        project(params, selected_ids=["chosen-0"])
    params = original_params(tmp_path)
    params.clips[2].editorial_live_manifest["selected_interval"] = [0, 5]
    with pytest.raises(ValueError, match="Original editorial review interval changed"):
        project(params, segments={"chosen-2": (1, 2)})


def test_excess_hold_or_title_budget_is_rejected_without_shortening_other_clips(tmp_path):
    params = original_params(tmp_path)
    before = deepcopy(params)
    with pytest.raises(ValueError, match="current titles leave"):
        project(params, segments={**params.clip_segments, "chosen-0": (0, 60)})
    tighter = replace(timing_policy_for_params(params), target_seconds=20)
    with pytest.raises(ValueError, match="current titles leave"):
        project(params, policy=tighter)
    assert params == before


def ui_state(params):
    from immich_memories.ui.state import AppState

    return AppState(
        config=params.config,
        immich_url="https://immich.example.com",
        immich_api_key="test-key",
        memory_type=params.memory_type,
        target_duration=1,
        pipeline_selected_clips=params.clips,
        editorial_selections=params.editorial_selections,
        selected_clip_ids={c.asset.id for c in params.clips},
        clip_segments=params.clip_segments,
        editorial_render_timing=params.editorial_render_timing,
        timeline_plan=params.timeline_plan,
    )


def test_ui_factory_keeps_original_plan_and_writes_private_output_specific_edit_record(tmp_path):
    import json

    from immich_memories.ui.pages._step4_generate import _build_generation_params

    params = original_params(tmp_path)
    state = ui_state(params)
    binding = deepcopy(state.editorial_render_timing)
    state.selected_clip_ids.remove("chosen-0")
    state.clip_segments = {**state.clip_segments, "chosen-2": (1, 3)}
    # WHY: avoids opening a real Immich connection; this test checks the edit record.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        generated = _build_generation_params(state, params.clips[1:], params.output_path)
    prepare_certified_timeline(generated)
    _validated_render_directives(generated)
    assert state.editorial_render_timing == binding
    assert state.editorial_selections == params.editorial_selections
    assert state.pipeline_selected_clips[2].editorial_live_manifest["selected_interval"] == [0, 4]
    path = tmp_path / generated.editorial_owner_edits["artifact_name"]
    assert path.name.startswith("memory.owner-edits-")
    assert json.loads(path.read_text()) == generated.editorial_owner_edits
    assert path.stat().st_mode & 0o777 == 0o600
    assert generated.editorial_owner_edits["removed_asset_ids"] == ["chosen-0"]


def test_ui_factory_no_op_preserves_binding_and_does_not_write_an_edit(tmp_path):
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    params = original_params(tmp_path)
    state = ui_state(params)
    # WHY: avoids opening a real Immich connection; the test never touches params.client.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        generated = _build_generation_params(state, params.clips, params.output_path)
    assert generated.editorial_render_timing is state.editorial_render_timing
    assert generated.editorial_owner_edits is None
    assert not list(tmp_path.glob("*.owner-edits-*.private.json"))


def test_ui_factory_rebinds_explicit_transition_settings(tmp_path):
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    params = original_params(tmp_path)
    state = ui_state(params)
    state.generation_options = {"transition": "Cut (no transition)"}
    # WHY: avoids opening a real Immich connection while asserting on the rebinding.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        generated = _build_generation_params(state, params.clips, params.output_path)
    assert generated.editorial_owner_edits["timing_policy_changed"]
    prepare_certified_timeline(generated)


def test_ui_factory_rejects_invalid_edit_before_creating_client(tmp_path):
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    params = original_params(tmp_path)
    state = ui_state(params)
    state.clip_segments = {**state.clip_segments, "chosen-2": None}
    # WHY: guards that no real Immich client is built when validation fails first.
    with (
        # WHY: captures the constructor call so assert_not_called can confirm it never ran.
        patch("immich_memories.api.immich.SyncImmichClient") as client,
        pytest.raises(ValueError, match="Review trim"),
    ):
        _build_generation_params(state, params.clips, params.output_path)
    client.assert_not_called()
    assert not list(tmp_path.glob("*.owner-edits-*.private.json"))
