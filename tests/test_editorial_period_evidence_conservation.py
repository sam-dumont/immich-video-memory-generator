"""Period citations survive production without becoming selection instructions."""

import json
from dataclasses import asdict, replace

import pytest

from immich_memories.analysis.editorial_contracts import InsightEvidence
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import (
    asked_again,
    semantic_plan,
    source,
)


def _plan(captured, judge):
    return plan_structure(
        captured,
        StructurePlannerPorts(
            judge=judge,
            thumbnail_hash=lambda _: None,
        ),
    )


@pytest.mark.parametrize("private_only", [False, True])
def test_citations_survive_without_changing_requests_or_eligible_carriers(tmp_path, private_only):
    captured = source(
        tmp_path, seconds=60, pictures=1 if private_only else 20, private_opening=private_only
    )
    captured = replace(captured, audience="sendable")
    cold_judge = ControlledStoryJudge()
    baseline = _plan(captured, cold_judge)
    evidence = (
        InsightEvidence(
            "Prior context, not an eligible picture",
            ("episode-z", "episode-a"),
            ("outside-z", "outside-a"),
        ),
        InsightEvidence("Separate observation", ("episode-other",), ("outside-other",)),
    )
    with_evidence = replace(captured, period_evidence=evidence)
    warm_judge = ControlledStoryJudge(cold_judge.bank, require_hits=True)
    revised = _plan(with_evidence, warm_judge)

    assert with_evidence.assets == captured.assets
    assert with_evidence.moment_asset_ids == captured.moment_asset_ids
    assert with_evidence.lineage == captured.lineage
    assert baseline.plan["period_evidence"] == []
    assert revised.plan["period_evidence"] == [asdict(row) for row in evidence]
    assert semantic_plan({**revised.plan, "period_evidence": []}) == semantic_plan(baseline.plan)
    assert [(c["stage"], c["prompt"]) for c in warm_judge.calls] == asked_again(cold_judge.calls)
    assert warm_judge.calls and all(c["cache_hit"] for c in warm_judge.calls)
    assert bool(revised.plan["carriers"]) is not private_only
    revised.write(tmp_path / "written")
    saved = json.loads((tmp_path / "written" / "plan.private.json").read_text())
    assert saved["period_evidence"] == [
        {
            "observation": row.observation,
            "episode_ids": list(row.episode_ids),
            "asset_ids": list(row.asset_ids),
        }
        for row in evidence
    ]
