"""A bounded planning failure is not evidence of an unusable library.

The matrix validator replay stays on the probe branch.

The matrix-defect replays stay on the probe branch.

The matrix validator replays stay on the probe branch.
"""

import json

import pytest

from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_runtime import EditorialRunContext
from immich_memories.analysis.editorial_runtime_backend import (
    ProductionPostCardBackend,
    _plan_from_structure_result,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult
from immich_memories.analysis.selection_trace import Trace
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import run, source
from tests.test_smart_pipeline_editorial_planner import _ForbiddenLegacyStage, _pipeline


def test_available_story_material_and_shorter_selection_are_not_discarded(tmp_path):
    judge = ControlledStoryJudge()
    plan = run(source(tmp_path, seconds=60, pictures=8), judge)
    assert len(plan["carriers"]) == 8
    assert plan["content_seconds"] == sum(row["seconds"] for row in plan["carriers"])
    assert plan["status"] == "selection-awaiting-owner-review-not-rendered"
    assert plan["intent_report"]["status"] == "ok"


def test_unlimited_small_result_keeps_existing_refusal(tmp_path):
    plan = run(source(tmp_path, seconds=60, pictures=1), ControlledStoryJudge())
    assert plan["status"] == plan["intent_report"]["status"] == "insufficient_material"
    assert len(plan["carriers"]) == 1
    assert plan["intent_report"]["coverage"] == {"scope": 1}


def backend_for(source_input, result):
    case = source_input.case
    return ProductionPostCardBackend(
        config=source_input.config,
        context=EditorialRunContext(
            case.key,
            case.label,
            case.product,
            case.ranges,
            case.target_seconds,
            source_input.artifact_dir,
        ),
        people=adapt_editorial_people({}),
        thumbnail_cache=object(),
        store_path=source_input.bank_dir / "unused.sqlite",
        ports=EditorialRuntimePorts(
            structure_planner=lambda *_: result,
            structure_ports_factory=lambda *_: object(),
        ),
    )


def partial_result(source_input, *, status="planning_incomplete"):
    assets = list(source_input.assets.values())[:2]
    return StructurePlanningResult(
        {
            "status": status,
            "intent_report": {"status": status, "reason": "assembly=invalid_verdict"},
            "carriers": [
                {"asset_id": a.id, "taken": a.file_created_at.isoformat(), "kind": "still"}
                for a in assets
            ],
        },
        "contract",
        "diagnostic selection",
        {},
    )


def test_runtime_persists_valid_incomplete_result_then_raises_without_empty_or_renderable_plan(
    tmp_path,
):
    captured = source(tmp_path, seconds=60, pictures=3)
    result = partial_result(captured)
    backend = backend_for(captured, result)
    with pytest.raises(
        RuntimeError, match="Editorial planning incomplete.*assembly=invalid_verdict"
    ):
        backend.edit(captured, trace=Trace())
    assert json.loads((captured.artifact_dir / "plan.private.json").read_text()) == result.plan
    assert backend.last_structure_result is result
    with pytest.raises(RuntimeError, match="Editorial planning incomplete"):
        _plan_from_structure_result(result, allowed_ids=set(captured.assets))


@pytest.mark.parametrize("invalid", ["foreign", "duplicate", "order"])
def test_incomplete_result_does_not_publish_invalid_carrier_identity_or_order(tmp_path, invalid):
    captured = source(tmp_path, seconds=60, pictures=3)
    result = partial_result(captured)
    carriers = result.plan["carriers"]
    if invalid == "foreign":
        carriers[0]["asset_id"] = "not-in-source"
    elif invalid == "duplicate":
        carriers[1]["asset_id"] = carriers[0]["asset_id"]
    else:
        carriers.reverse()
    backend = backend_for(captured, result)
    with pytest.raises(ValueError):
        backend.edit(captured, trace=Trace())
    assert not (captured.artifact_dir / "plan.private.json").exists()
    assert not (captured.artifact_dir / "selection-sheet.private.md").exists()
    assert backend.last_structure_result is None


def test_default_admission_refusal_still_persists_an_empty_cut(tmp_path):
    captured = source(tmp_path, seconds=60, pictures=3)
    result = partial_result(captured, status="insufficient_material")
    result.plan["intent_report"]["reason"] = "No qualifying exceptional event."
    backend = backend_for(captured, result)
    assert backend.edit(captured, trace=Trace()) == EditorialPlan()
    assert backend.last_structure_result is result
    assert json.loads((captured.artifact_dir / "plan.private.json").read_text()) == result.plan


def test_actual_source_pipeline_rethrows_incomplete_failure_without_legacy_or_projection(
    tmp_path,
    monkeypatch,
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
):
    captured = source(tmp_path, seconds=60, pictures=3)
    result = partial_result(captured)
    backend = backend_for(captured, result)

    class Planner:
        def plan_source(self, _sources, *, trace, **_kwargs):
            return backend.edit(captured, trace=trace)

    pipeline = _pipeline(
        mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, planner=Planner()
    )
    pipeline.refiner = _ForbiddenLegacyStage()
    pipeline.quality.refiner = _ForbiddenLegacyStage()
    monkeypatch.setattr(
        pipeline, "_project_editorial_plan", lambda *_a, **_k: pytest.fail("no projection")
    )
    progress = []
    with pytest.raises(RuntimeError, match="Editorial planning incomplete"):
        pipeline.run_editorial_source(
            list(captured.assets.values()), progress_callback=progress.append
        )
    assert progress[-1]["status"] == "failed"
    assert backend.last_structure_result is result
    assert (captured.artifact_dir / "plan.private.json").exists()
