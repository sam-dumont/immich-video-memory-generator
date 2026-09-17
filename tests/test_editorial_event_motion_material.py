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


def live_source(tmp_path, *, pictures, add_context, residual=9.0):
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
        motion_residuals={aid: {"residual": residual} for aid in assets},
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
    # A lone Live Photo is under the stitch minimum by construction, so its own motion
    # decides it (#1066); here that motion is far above the discriminant.
    assert carrier["kind"] == "live-motion"
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
    assert fetched == list(manifest.video_ids)  # a Live Photo that plays fetches its own footage
    assert "extra-video" not in fetched

    replay = ControlledStoryJudge(judge.bank, require_hits=True)
    assert semantic_plan(run(captured, replay)) == semantic_plan(result)
    assert all(call["cache_hit"] for call in replay.calls)


def test_a_lone_live_photo_that_barely_moves_is_still_a_photograph(tmp_path):
    captured = live_source(tmp_path, pictures=1, add_context=True, residual=0.4)

    [carrier] = run(captured, ControlledStoryJudge())["carriers"]

    assert carrier["kind"] == "live-still"


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


def test_shared_live_companion_at_one_shutter_keeps_exact_material_through_projection(tmp_path):
    captured = live_source(tmp_path, pictures=3, add_context=False)
    first, duplicate, last = captured.assets.values()
    duplicate.file_created_at = first.file_created_at + timedelta(seconds=1.342)
    last.file_created_at = duplicate.file_created_at
    last.live_photo_video_id = duplicate.live_photo_video_id
    captured = replace(
        captured,
        companion_assets={
            first.live_photo_video_id: make_asset(first.live_photo_video_id, duration=2.733),
            duplicate.live_photo_video_id: make_asset(
                duplicate.live_photo_video_id, duration=2.267
            ),
        },
    )

    result = run(captured, ControlledStoryJudge())

    assert len(result["carriers"]) == 1
    carrier = result["carriers"][0]
    assert set(carrier["members"]) == {first.id, duplicate.id, last.id}
    assert len(carrier["video_ids"]) == 2
    _, candidates = demand(list(captured.assets.values()))
    projected = project_source_rendering(
        result["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=captured.companion_assets,
    )
    selected = next(
        row.clip for row in projected.candidates if row.clip.asset.id == carrier["asset_id"]
    )
    assert selected.live_burst_material == carrier["live_material"]
    assert selected.live_burst_trim_points == [(0.0, 2.7085), (1.1335, 2.267)]


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


def test_duplicate_positive_companions_keep_exact_timing_through_projection(tmp_path):
    captured = live_source(tmp_path, pictures=6, add_context=False)
    members = list(captured.assets.values())
    start = members[0].file_created_at
    offsets = (0.0, 1.451, 1.451, 2.406, 4.423, 4.423)
    videos = (0, 1, 1, 2, 3, 3)
    for asset, offset, video in zip(members, offsets, videos, strict=True):
        asset.file_created_at = start + timedelta(seconds=offset)
        asset.live_photo_video_id = f"video-{video}"
    captured = replace(
        captured,
        companion_assets={
            f"video-{index}": make_asset(f"video-{index}", duration=duration)
            for index, duration in enumerate((2.857, 2.603, 2.565, 2.577))
        },
    )

    result = run(captured, ControlledStoryJudge())

    carrier = next(row for row in result["carriers"] if row["kind"] == "live-motion")
    _, candidates = demand(members)
    projected = project_source_rendering(
        result["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=captured.companion_assets,
    )
    selected = next(
        row.clip for row in projected.candidates if row.clip.asset.id == carrier["asset_id"]
    )
    assert selected.live_burst_material == carrier["live_material"]
    assert selected.live_burst_video_ids == [f"video-{i}" for i in range(4)]
    assert selected.live_burst_trim_points[1][1] == pytest.approx(2.2565)
    assert selected.live_burst_trim_points[-1][1] == pytest.approx(2.577)
