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


def test_a_video_typed_asset_with_a_rendering_is_an_ordinary_video_unit(tmp_path):
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_material import UnitBuilder, read_wall
    from immich_memories.analysis.motion_rendering import MotionRendering
    from immich_memories.api.models import AssetType

    captured = live_source(tmp_path, pictures=2, add_context=False)
    video = make_asset("ordinary-video", duration="0:00:15")
    video.type = AssetType.VIDEO
    [moment] = captured.moment_asset_ids
    captured = replace(
        captured,
        assets={**captured.assets, video.id: video},
        moment_asset_ids={moment: (*captured.moment_asset_ids[moment], video.id)},
    )
    wall = read_wall(captured)
    [family] = wall.event_assets
    builder = UnitBuilder(
        captured,
        StructurePlannerPorts(
            judge=None,
            thumbnail_hash=lambda _: None,
        ),
        wall,
        renderings={
            video.id: MotionRendering(
                video_ids=(video.id,),
                trim_points=((0.0, 3.0),),
                shutter_timestamps=(0.0,),
                duration_seconds=3.0,
                still_ids=(video.id,),
                minimum_seconds=3.5,
            )
        },
        never_auto=set(),
        document_sources={},
    )

    units = builder.units_of(family)

    row = next(u for u in units if u["asset_id"] == video.id)
    assert row["kind"] == "video"
    assert row["members"] == [video.id] and row["video_ids"] == [video.id]
    assert "motion_candidate" not in row and "live_material" not in row


def test_a_carrier_the_projection_would_refuse_retires_before_the_film_finalizes(tmp_path):
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.test_editorial_story_first_planner import make_source

    captured = make_source(tmp_path)

    corrupted: list[str] = []

    def corrupting(carriers):
        # The disagreement the failed 2022 run recorded: a carrier whose kind its
        # source media cannot honour, which the strict projection refuses. One carrier.
        if carriers:
            corrupted.append(carriers[0]["asset_id"])
            return [carriers[0] | {"kind": "video"}, *carriers[1:]], {"new_motion_downloads": 0}
        return carriers, {"new_motion_downloads": 0}

    result = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=lambda _: None,
            resolve_motion=corrupting,
        ),
    ).plan

    assert corrupted, "the resolver saw the carrier before it retired"
    surviving = {row["asset_id"] for row in result["carriers"]}
    assert corrupted[-1] not in surviving, "an unprojectable carrier must not survive"
    assert surviving, "the rest of the film keeps its place"
    [row] = [row for row in result["cut_carriers"] if row["asset_id"] == corrupted[-1]]
    assert row["reason"] and row["review_stage"]

    # The recorded film stays whole: its carriers project, membership and timing consistent.
    _, candidates = demand(list(captured.assets.values()))
    projected = project_source_rendering(
        result["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=captured.companion_assets,
    )
    assert {row.asset_id for row in projected.plan.selections} == surviving


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
