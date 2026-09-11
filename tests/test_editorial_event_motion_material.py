"""Live motion cannot borrow footage outside the event's selectable material."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_source_route import project_source_rendering
from immich_memories.analysis.motion_rendering import motion_renderings
from immich_memories.generate_clips import _prefetch_assets, _validated_render_directives
from tests.conftest import make_asset
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import (
    run,
    semantic_plan,
    source,
)
from tests.test_editorial_source_route import demand, photo


def live_source(tmp_path, *, pictures, add_context):
    captured = source(tmp_path, seconds=15, pictures=pictures)
    assets = dict(captured.assets)
    start = next(iter(assets.values())).file_created_at
    for index, asset in enumerate(assets.values()):
        asset.file_created_at = start + timedelta(seconds=2 * index)
        asset.live_photo_video_id = f"video-{index}"
    if add_context:
        extra = photo(
            "unselectable-context", at=start + timedelta(seconds=0.684), live="extra-video"
        )
        assets[extra.id] = extra
    return replace(
        captured,
        assets=assets,
        companion_assets={
            asset.live_photo_video_id: make_asset(asset.live_photo_video_id, duration=3.0)
            for asset in assets.values()
        },
        motion_residuals={aid: {"residual": 9.0} for aid in assets},
    )


@pytest.mark.parametrize("pictures", [1, 2])
def test_planner_uses_exact_selectable_event_material_before_render_projection(tmp_path, pictures):
    captured = live_source(tmp_path, pictures=pictures, add_context=True)
    original_members = captured.moment_asset_ids
    judge = ControlledStoryJudge()

    result = run(captured, judge)

    assert len(result["carriers"]) == 1
    carrier = result["carriers"][0]
    expected_ids = tuple(aid for ids in original_members.values() for aid in ids)
    expected = motion_renderings(
        [captured.assets[aid] for aid in expected_ids],
        captured.config,
        companion_assets=captured.companion_assets,
    )
    manifest = expected[carrier["asset_id"]]
    assert set(carrier["members"]) == set(expected_ids)
    assert carrier["video_ids"] == list(manifest.video_ids)
    assert carrier["trim_points"] == [list(pair) for pair in manifest.trim_points]
    assert carrier["kind"] == ("live-motion" if pictures == 2 else "live-still")
    assert "extra-video" not in carrier["video_ids"]
    assert captured.moment_asset_ids == original_members

    _, candidates = demand(list(captured.assets.values()))
    projected = project_source_rendering(
        result["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=captured.companion_assets,
    )
    selected = [
        row.clip for row in projected.candidates if row.clip.asset.id == carrier["asset_id"]
    ]
    params = SimpleNamespace(clips=selected, editorial_selections=projected.plan.selections)
    directives = _validated_render_directives(params)
    fetched = [asset.id for asset in _prefetch_assets(selected, directives)]
    assert fetched == (list(manifest.video_ids) if pictures == 2 else [])
    assert "extra-video" not in fetched

    replay = ControlledStoryJudge(judge.bank, require_hits=True)
    assert semantic_plan(run(captured, replay)) == semantic_plan(result)
    assert all(call["cache_hit"] for call in replay.calls)


def test_existing_whole_event_burst_has_identical_requests_and_selection_with_extra_context(
    tmp_path,
):
    plain = live_source(tmp_path / "plain", pictures=2, add_context=False)
    with_context = live_source(tmp_path / "context", pictures=2, add_context=True)
    judge = ControlledStoryJudge()

    expected = run(plain, judge)
    replay = ControlledStoryJudge(judge.bank, require_hits=True)
    actual = run(with_context, replay)

    # Source context remains honestly counted, even though it cannot enter the film.
    expected["planning_scope"]["source_assets"] += 1
    assert semantic_plan(actual) == semantic_plan(expected)
    assert [call["prompt"] for call in judge.calls] == [call["prompt"] for call in replay.calls]


def test_projection_still_rejects_a_manifest_that_reintroduces_unreviewed_motion(tmp_path):
    captured = live_source(tmp_path, pictures=1, add_context=True)
    result = run(captured, ControlledStoryJudge())
    carrier = dict(result["carriers"][0])
    old_global = motion_renderings(list(captured.assets.values()), captured.config)[
        carrier["asset_id"]
    ]
    carrier.update(
        kind="live-motion",
        video_ids=list(old_global.video_ids),
        trim_points=[list(pair) for pair in old_global.trim_points],
    )
    _, candidates = demand(list(captured.assets.values()))

    with pytest.raises(ValueError, match="Live motion companion manifest changed"):
        project_source_rendering(
            [carrier],
            candidates,
            config=captured.config,
            include_live_photos=True,
        )
