"""A production title reserve must be fixed before Live intervals are inspected."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_final_attached import AttachedMaterialEvidence
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.processing.editorial_timing import (
    EditorialTimingPolicy,
    bind_editorial_timeline,
    build_editorial_timing_policy,
    prepare_certified_timeline,
    read_editorial_timeline,
    timing_policy_for_params,
)
from tests.conftest import make_asset
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_visual_body_audience import picture_record


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("timing tests must not call media tools or services")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("platform.platform", lambda: "macOS-15.0-arm64")


def rows(count=3):
    assets = {
        str(index): SimpleNamespace(file_created_at=datetime(2010 + index, 1, 1), exif_info=None)
        for index in range(count)
    }
    return [{"asset_id": key, "seconds": 4.0} for key in assets], assets


@pytest.mark.parametrize(
    "transition, overlap", [("smart", 1.4), ("crossfade", 2), ("cut", 0), ("none", 0)]
)
def test_real_default_title_budget_credits_configured_overlap(transition, overlap):
    policy = build_editorial_timing_policy(
        config=Config(),
        target_seconds=60,
        memory_type="special_day",
        transition=transition,
    )
    carriers, assets = rows()
    for asset in assets.values():
        asset.file_created_at = datetime(2010, 1, 1)
    plan = policy.resolve(carriers, assets)
    assert plan.content_budget == pytest.approx(49.5 + overlap)
    assert plan.title_budget == 10.5
    assert plan.transition_budget == pytest.approx(overlap)
    assert read_editorial_timeline(bind_editorial_timeline(policy, plan, list(assets))) == plan


def test_no_titles_and_output_path_do_not_invalidate_timing():
    config = Config(title_screens={"enabled": False})
    params = GenerationParams(
        clips=[], output_path=Path("/one"), config=config, target_duration_seconds=60
    )
    policy = timing_policy_for_params(params)
    assert policy == timing_policy_for_params(replace(params, output_path=Path("/two")))
    carriers, assets = rows()
    assert policy.resolve(carriers, assets).content_budget == 61


def test_slot_overlap_estimate_uses_the_available_video_lengths():
    policy = build_editorial_timing_policy(
        config=Config(title_screens={"enabled": False}),
        target_seconds=60,
        memory_type="custom",
        transition="crossfade",
    )
    _, assets = rows()
    assert policy.selection_budget(assets, expected_clip_duration=6.0) == 65.0


@pytest.mark.parametrize(
    "product",
    [
        None,
        "custom",
        "person_spotlight",
        "year_in_review",
        "monthly_highlights",
        "on_this_day",
        "album",
    ],
)
def test_effective_product_policy_matches_actual_generation_builder(product):
    config = Config()
    params = GenerationParams(
        clips=[],
        output_path=Path("/unused"),
        config=config,
        target_duration_seconds=120,
        memory_type=product,
        date_start=datetime(2010, 1, 1),
        date_end=datetime(2010, 12, 31),
        transition=config.defaults.transition,
        transition_duration=config.defaults.transition_duration,
    )
    matrix_policy = build_editorial_timing_policy(
        config=config,
        target_seconds=120,
        memory_type=product or "custom",
        date_start=params.date_start,
        date_end=params.date_end,
    )
    assert matrix_policy == timing_policy_for_params(params)


def test_actual_generation_rejects_changed_timing_before_run_or_media(tmp_path):
    from immich_memories.generate import _generate_memory_inner

    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "not-created" / "memory.mp4",
        config=Config(),
        target_duration_seconds=60,
    )
    policy = timing_policy_for_params(params)
    params.editorial_render_timing = bind_editorial_timeline(policy, policy.resolve([], {}), [])
    params.transition_duration += 0.1
    with pytest.raises(ValueError, match="timing settings changed"):
        _generate_memory_inner(params)
    assert not params.output_path.parent.exists()


def test_all_year_subsets_need_no_more_than_the_frozen_title_reserve():
    policy = build_editorial_timing_policy(
        config=Config(),
        target_seconds=120,
        memory_type="on_this_day",
        date_start=datetime(2010, 1, 1),
        date_end=datetime(2014, 1, 1),
    )
    carriers, assets = rows(5)
    original = policy.resolve(carriers, assets)
    assert original.max_dividers == 4
    for mask in range(1, 32):
        subset = [row for index, row in enumerate(carriers) if mask >> index & 1]
        assert policy.resolve(subset, assets).content_budget >= original.content_budget


def test_trip_new_jump_does_not_recompute_frozen_divider_cap():
    policy = build_editorial_timing_policy(config=Config(), target_seconds=120, memory_type="trip")
    carriers, assets = rows()
    for index, asset in enumerate(assets.values()):
        asset.exif_info = SimpleNamespace(latitude=0.0, longitude=index * 0.20, city="Place")
    original = policy.resolve(carriers, assets)
    subset = [carriers[0], carriers[2]]
    assert original.max_dividers == 0
    assert policy.resolve(subset, assets).max_dividers == 1
    frozen = read_editorial_timeline(bind_editorial_timeline(policy, original, ["0", "2"]))
    assert frozen == original


@pytest.mark.parametrize(
    "change",
    ["target", "titles", "transition", "transition_duration", "dates", "selection", "tamper"],
)
def test_changed_generation_policy_is_rejected_before_media(change):
    params = GenerationParams(
        clips=[SimpleNamespace(asset=SimpleNamespace(id="one"), editorial_live_manifest={})],
        output_path=Path("/memory.mp4"),
        config=Config(),
        target_duration_seconds=60,
        memory_type="custom",
        date_start=datetime(2010, 1, 1),
        date_end=datetime(2010, 1, 30),
    )
    carriers, assets = rows(1)
    carriers[0]["asset_id"] = "one"
    assets["one"] = assets.pop("0")
    policy = timing_policy_for_params(params)
    timeline = policy.resolve(carriers, assets)
    params.editorial_render_timing = bind_editorial_timeline(policy, timeline, ["one"])
    if change == "target":
        params.target_duration_seconds = 80
    elif change == "titles":
        params.config.title_screens.enabled = False
    elif change == "transition":
        params.transition = "cut"
    elif change == "transition_duration":
        params.transition_duration = 1.0
    elif change == "dates":
        params.date_end = datetime(2015, 1, 1)
    elif change == "selection":
        params.clips = []
    else:
        params.editorial_render_timing["timeline"]["max_dividers"] = 4
    with pytest.raises(ValueError, match="timing"):
        prepare_certified_timeline(params)


def test_existing_saved_timeline_cannot_mask_changed_configuration():
    params = GenerationParams(
        clips=[], output_path=Path("/memory"), config=Config(), target_duration_seconds=60
    )
    policy = timing_policy_for_params(params)
    plan = policy.resolve([], {})
    params.editorial_render_timing = bind_editorial_timeline(policy, plan, [])
    params.timeline_plan = plan
    prepare_certified_timeline(params)
    params.config.title_screens.ending_duration = 3
    with pytest.raises(ValueError, match="timing settings changed"):
        prepare_certified_timeline(params)


def _ports(inspect=None):
    return StructurePlannerPorts(
        judge=ControlledStoryJudge(),
        thumbnail_hash=lambda _: None,
        rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
        reranker_identity={"model": "controlled", "endpoint": "test://local"},
        observe_picture=lambda _: {
            **picture_record(),
            "description": "A clothed person moves furniture.",
        },
        observe_attached_material=inspect,
    )


@pytest.mark.parametrize("titles", [False, True])
def test_actual_photo_planner_allocates_and_binds_finished_film_budget(tmp_path, titles):
    captured = source(tmp_path, seconds=60, pictures=50)
    captured.config.title_screens.enabled = titles
    policy = build_editorial_timing_policy(
        config=captured.config,
        target_seconds=60,
        memory_type=captured.case.product,
        transition="cut",
    )
    plan = plan_structure(replace(captured, render_timing=policy), _ports()).plan
    timeline = read_editorial_timeline(plan["render_timing"])
    assert timeline.content_budget == (49.5 if titles else 60)
    assert plan["content_cap_seconds"] == timeline.content_budget
    assert len(plan["carriers"]) == (12 if titles else 15)
    assert sum(c["seconds"] for c in plan["carriers"]) == (48 if titles else 60)


def test_actual_live_planner_freezes_timing_before_inspection_and_survives_cuts(tmp_path):
    captured = replace(source(tmp_path, seconds=60, pictures=20), audience="sendable")
    first_time = next(iter(captured.assets.values())).file_created_at
    assets = {
        key: asset.model_copy(
            update={
                "live_photo_video_id": f"video-{index}",
                "file_created_at": first_time
                + timedelta(seconds=(index // 2) * 600 + (index % 2) * 2),
            }
        )
        for index, (key, asset) in enumerate(captured.assets.items())
    }
    policy = build_editorial_timing_policy(
        config=captured.config, target_seconds=60, memory_type=captured.case.product
    )
    captured = replace(
        captured,
        assets=assets,
        render_timing=policy,
        companion_assets={
            asset.live_photo_video_id: make_asset(asset.live_photo_video_id, duration=3.0)
            for asset in assets.values()
        },
        motion_residuals={key: {"residual": 2.0} for key in assets},
    )
    observed = []

    def inspect(carriers):
        assert (
            sum(row["seconds"] for row in carriers)
            <= policy.resolve(carriers, assets).content_budget
        )
        observed.extend(deepcopy(carriers))
        first = carriers[0]["asset_id"]
        members = {first: ("bound-hold",)}
        records = {"bound-hold": {**picture_record("yes"), "description": "An uncovered person."}}
        return AttachedMaterialEvidence(members, members, records)

    plan = plan_structure(captured, _ports(inspect)).plan
    assert observed and all(row in observed for row in plan["carriers"])
    assert len(plan["carriers"]) < len(observed)
    assert plan["content_cap_seconds"] == policy.resolve(observed, assets).content_budget
    timeline = read_editorial_timeline(plan["render_timing"])
    assert timeline.content_budget == plan["content_cap_seconds"]
    assert plan["render_timing"]["source_ids"] == [row["asset_id"] for row in plan["carriers"]]
    assert plan["target_seconds"] == 60


def test_impossible_render_budget_fails_before_attached_calls(tmp_path, monkeypatch):
    captured = source(tmp_path, seconds=60, pictures=6)
    first_time = next(iter(captured.assets.values())).file_created_at
    assets = {
        key: asset.model_copy(
            update={
                "live_photo_video_id": f"video-{index}",
                "file_created_at": first_time
                + timedelta(seconds=(index // 2) * 600 + (index % 2) * 2),
            }
        )
        for index, (key, asset) in enumerate(captured.assets.items())
    }
    policy = build_editorial_timing_policy(
        config=captured.config, target_seconds=60, memory_type=captured.case.product
    )
    base = policy.resolve([{"asset_id": key, "seconds": 4} for key in assets], assets)
    monkeypatch.setattr(
        EditorialTimingPolicy,
        "resolve",
        lambda *_a: replace(base, content_budget=0, title_budget=60),
    )
    captured = replace(
        captured,
        assets=assets,
        render_timing=policy,
        companion_assets={
            asset.live_photo_video_id: make_asset(asset.live_photo_video_id, duration=3.0)
            for asset in assets.values()
        },
        motion_residuals={key: {"residual": 2.0} for key in assets},
    )
    with pytest.raises(ValueError, match="minimum content cannot fit"):
        plan_structure(captured, _ports(lambda *_a: pytest.fail("sampled before fitting")))


def test_a_carrier_the_timing_trim_cuts_says_why_on_the_selection_sheet(tmp_path):
    """April 2021 at 60 s died with KeyError 'reason' while writing the selection sheet: the
    sheet prints every cut carrier with the reason it was cut, so each cut owes one."""
    captured = source(tmp_path, seconds=60, pictures=50)
    captured.config.title_screens.enabled = True
    policy = build_editorial_timing_policy(
        config=captured.config,
        target_seconds=60,
        memory_type=captured.case.product,
        transition="cut",
    )

    def hold_speech(carriers):
        # WHY: stands in for the speech measurement boundary (media tools over the real
        # companion files). An utterance held to 8 s is indivisible, so the selection no
        # longer fits the finished film and the timing trim has to cut carriers.
        return [
            row
            | {
                "start_time": 0.0,
                "seconds": 8.0,
                "end_time": 8.0,
                "speech_regions": [[0.0, 8.0]],
            }
            for row in carriers
        ]

    result = plan_structure(
        replace(captured, render_timing=policy), replace(_ports(), resolve_speech=hold_speech)
    )

    cut = result.plan["cut_carriers"]
    assert cut, "the timing trim cut nothing, so no cut carrier was written down"
    assert all(row.get("reason") for row in cut), [row.get("review_stage") for row in cut]
    assert all(row["reason"] in result.selection_sheet for row in cut)
