"""Duration fitting must keep speech boundaries and the selected interval together."""

from immich_memories.analysis.editorial_structure_record import shave_content_duration


def test_budget_trim_moves_to_a_pause_and_keeps_the_interval_consistent():
    video = {
        "kind": "video",
        "asset_id": "talk",
        "seconds": 8.0,
        "start_time": 0.0,
        "end_time": 8.0,
        "raw_seconds": 10.0,
        "speech_regions": [[5.0, 7.5]],
    }
    shave_content_duration([video], 6.0)
    assert 3.5 <= video["seconds"] <= 5.0
    assert video["end_time"] - video["start_time"] == video["seconds"]


def test_speech_spanning_a_live_stitch_join_uses_the_stitched_clock():
    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry

    material = LiveRenderMaterial(
        (
            LiveSourceEntry("a", "va", 0, 0, 3),
            LiveSourceEntry("b", "vb", 2, 1, 4),
        )
    )
    carrier = {
        "asset_id": "a",
        "kind": "live-motion",
        "seconds": 4.0,
        "raw_seconds": 6.0,
        "live_material": material.as_dict(),
    }
    speech = {"va": [(2.0, 3.0)], "vb": [(1.0, 3.0)]}
    resolved = resolve_speech_cuts([carrier], speech.__getitem__, buffer=0.08)[0]
    assert resolved["seconds"] == 5.08  # vb's 3s maps to 5s in the stitched movie.
    assert resolved["speech_regions"] == [[1.92, 5.08]]
    assert resolved["end_time"] == resolved["seconds"]
    assert carrier["seconds"] == 4.0


def test_fitting_drops_an_indivisible_utterance_instead_of_cutting_it():
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [
        {"asset_id": "speech", "seconds": 8, "speech_regions": [[0, 8]], "story_weight": "minor"},
        {"asset_id": "photo", "seconds": 4, "story_weight": "dominant"},
    ]
    kept, dropped = trim_to_timing_budget(carriers, lambda _: 10, 3.5)
    assert [c["asset_id"] for c in kept] == ["photo"]
    assert [c["asset_id"] for c in dropped] == ["speech"]


def test_short_video_contributes_its_actual_duration_to_minimum_fit():
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [{"asset_id": str(i), "seconds": 2.0} for i in range(3)]
    kept, dropped = trim_to_timing_budget(carriers, lambda _: 6, 3.5)
    assert kept == carriers
    assert dropped == []


def test_real_planner_budgets_video_lengths_and_fits_after_speech_detection(tmp_path):
    from dataclasses import replace

    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from immich_memories.api.models import AssetType
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy
    from tests.test_editorial_duration_planner_integration import source
    from tests.test_editorial_timing import _ports

    captured = source(tmp_path, seconds=60, pictures=50)
    captured = replace(
        captured,
        assets={
            key: asset.model_copy(update={"type": AssetType.VIDEO, "duration_seconds": 12.0})
            for key, asset in captured.assets.items()
        },
    )
    timing = build_editorial_timing_policy(
        config=captured.config,
        target_seconds=60,
        memory_type=captured.case.product,
        transition="cut",
    )
    calls = []

    def speech(carriers):
        calls.append([dict(c) for c in carriers])
        return resolve_speech_cuts(carriers, lambda _: [(4.0, 8.0)], buffer=0.08)

    plan = plan_structure(
        replace(captured, render_timing=timing), replace(_ports(), resolve_speech=speech)
    ).plan
    assert calls and all(c["seconds"] == 6.0 for c in calls[0])
    assert len(calls[0]) == 8  # 49.5 seconds funds eight six-second videos.
    assert sum(c["seconds"] for c in plan["carriers"]) <= 49.5
    for carrier in plan["carriers"]:
        assert carrier["end_time"] - carrier["start_time"] == carrier["seconds"]
        assert carrier["end_time"] <= 3.92 or carrier["end_time"] >= 8.08
