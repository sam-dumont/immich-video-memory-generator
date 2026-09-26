"""Captioning selected shots must not erase the people facts their cut protects."""

import json
from dataclasses import replace

from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.test_editorial_duration_planner_integration import source


def test_captioned_family_shots_do_not_trigger_another_family_replacement(tmp_path):
    captured = source(tmp_path, seconds=60, pictures=24)
    for asset in captured.assets.values():
        asset.is_favorite = True
    lines = {
        asset: f"{value.file_created_at.isoformat()} | with Person A (partner; inner circle)"
        for asset, value in captured.assets.items()
    }
    captured = replace(captured, annotations=lines)
    ports = StructurePlannerPorts(
        judge=NoModelJudge(), rules=RuleStructureReader(captured), thumbnail_hash=lambda _: None
    )

    # WHY: new captions arrive from an external producer after NAS selected its shots;
    # keep the real planner and its people protections across that boundary.
    def refine(current, draft):
        selected = {c["asset_id"] for c in draft.carriers}
        assert selected and selected != set(lines)
        enriched = replace(
            current,
            annotations={
                asset: line.replace(" | with", " | A person stands outdoors. | with")
                if asset in selected
                else line
                for asset, line in lines.items()
            },
        )
        return enriched, replace(ports, rules=RuleStructureReader(enriched), draft=draft)

    result = plan_structure(captured, replace(ports, refine=refine))

    assert result.plan["carriers"]
    for name in ("family-seat", "family-seat-after-review"):
        audit = json.loads(
            (captured.artifact_dir / "derived-decisions" / f"{name}.private.json").read_text()
        )
        assert audit["seats"] == [], "a captioned shot already satisfies the partner's seat"
