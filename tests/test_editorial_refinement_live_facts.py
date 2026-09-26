"""Late clip evidence changes how the retained photograph plays."""

from dataclasses import replace

from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.conftest import make_asset
from tests.test_editorial_duration_planner_integration import source


def test_fresh_live_frame_quality_changes_playback_but_keeps_the_photo(tmp_path):
    captured = source(tmp_path, seconds=60, pictures=8)
    companions = {}
    for asset in captured.assets.values():
        asset.is_favorite = True
        asset.live_photo_video_id = "clip-" + asset.id
        companions[asset.live_photo_video_id] = make_asset(
            asset.live_photo_video_id, duration=3.0, file_created_at=asset.file_created_at
        )
    captured = replace(
        captured,
        companion_assets=companions,
        motion_residuals={a: {"residual": 9.0} for a in captured.assets},
    )
    ports = StructurePlannerPorts(
        judge=NoModelJudge(), rules=RuleStructureReader(captured), thumbnail_hash=lambda _: None
    )
    baseline = plan_structure(captured, ports)
    assert any(c["kind"] == "live-motion" for c in baseline.plan["carriers"])

    # WHY: frame classification is the external boundary; the real planner must
    # apply its late result to carriers already chosen by the NAS draft.
    def refine(current, draft):
        enriched = replace(current, clip_frames=dict.fromkeys(companions, "subject_often_missing"))
        return enriched, replace(ports, rules=RuleStructureReader(enriched), draft=draft)

    result = plan_structure(captured, replace(ports, refine=refine))
    assert {c["asset_id"] for c in result.plan["carriers"]} == {
        c["asset_id"] for c in baseline.plan["carriers"]
    }
    assert all(c["kind"] == "live-still" for c in result.plan["carriers"])
