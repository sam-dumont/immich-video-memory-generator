"""Trace selected facts through story-first selection without rewriting source discovery."""

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source


def test_facts_reach_picture_checks_without_changing_story_discovery(tmp_path):
    # Exceed the comparison cap. A smaller pool legitimately needs every picture
    # read now that fitting the grant no longer means compulsory selection.
    captured = source(tmp_path, seconds=120, pictures=120)
    original_lines = dict(captured.annotations)
    calls = []

    def observe(asset_id):
        calls.append(asset_id)
        index = int(asset_id.rsplit("-", 1)[1])
        return {
            "status": "available",
            "identity": "controlled-picture-observer",
            "description": f"A clothed person carries furniture; observed angle {index}.",
            "facts": {"subject_action": "A clothed person carries furniture."},
        }

    judge = ControlledStoryJudge()
    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=judge,
            thumbnail_hash=lambda _: None,
            observe_picture=observe,
        ),
    ).plan
    assert calls and len(calls) == len(set(calls))
    assert len(calls) < len(captured.assets)
    assert captured.annotations == original_lines
    assert plan["carriers"]
    for prefix in ("shareability-",):
        prompts = [row["prompt"] for row in judge.calls if row["stage"].startswith(prefix)]
        assert prompts and all("observed angle" in prompt for prompt in prompts), prefix
    for row in judge.calls:
        if row["stage"].startswith(
            ("worthy-", "story-episodes", "story-understanding", "moment-inventory")
        ):
            assert "observed angle" not in row["prompt"]
    assert all("observed angle" in row["line"] for row in plan["carriers"])
    assert set(plan["picture_facts"]) == set(calls)
