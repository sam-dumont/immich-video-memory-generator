"""The finished film loses its repeats to the cached hashes and scene prints, on every tier.

Pictures are read once, at ingest. The review of a finished cut reads what ingest banked about
them (the preview hash and the scene print) and never sends a pair's pixels to a model, however
capable the configured reader is.
"""

import numpy as np

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.operations.cut_progress import announcing_stages
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source

# Cached previews no two of which are within the review's 6 bits.
DISTINCT_PREVIEWS = (
    "0000000000000000",
    "00000000000000ff",
    "000000000000ff00",
    "0000000000ff0000",
)


def _distinct_preview(asset_id: str) -> str:
    return DISTINCT_PREVIEWS[int(asset_id.rsplit("-", 1)[1])]


def test_a_film_loses_its_hash_twin_without_a_pair_read(tmp_path):
    captured = source(tmp_path, seconds=24, pictures=4)
    previews = {
        "picture-000": "0000000000000000",
        "picture-001": "0000000000000000",
        "picture-002": "000000000000ff00",
        "picture-003": "0000000000ff0000",
    }
    announced: list[str] = []
    with announcing_stages(lambda update: announced.append(update.label)):
        plan = plan_structure(
            captured,
            StructurePlannerPorts(
                judge=ControlledStoryJudge(),
                thumbnail_hash=previews.get,
            ),
        ).plan

    assert [c["asset_id"] for c in plan["carriers"]] == [
        "picture-000",
        "picture-002",
        "picture-003",
    ]
    review = plan["final_duplicate_review"]
    assert [row["asset_id"] for row in review["removals"]] == ["picture-001"]
    assert review["policy"].startswith("final-cached-hash-duplicates")
    assert review["status"] == "complete"
    assert "sampled_pair_metrics" not in plan
    assert plan["cut_carriers"][0]["asset_id"] == "picture-001"
    assert plan["cut_carriers"][0]["review_stage"] == "final-duplicates"
    # The counts are announced while the edit runs, so a watcher sees the edit move.
    assert "Editing the memory: 4 pictures going into the family-viewing check" in announced
    assert "Editing the memory: 4 pictures going into the picture review" in announced
    assert "Editing the memory: 3 pictures after the duplicate review" in announced


def test_two_pictures_that_hash_as_strangers_both_stay_when_nothing_else_says_one_scene(tmp_path):
    """No tier asks a model whether two pictures repeat each other."""
    captured = source(tmp_path, seconds=12, pictures=2)
    far = {"picture-000": "0000000000000000", "picture-001": "ffffffffffffffff"}

    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=far.get,
        ),
    ).plan

    assert [c["asset_id"] for c in plan["carriers"]] == ["picture-000", "picture-001"]
    assert plan["final_duplicate_review"]["removals"] == []


def test_the_finished_film_drops_a_scene_it_already_shows_when_it_has_room(tmp_path):
    """Two differently framed shots of one scene hash as strangers; their scene prints agree."""
    captured = source(tmp_path, seconds=12, pictures=4)
    prints = {
        "picture-000": np.array([1.0, 0.0, 0.0]),
        "picture-001": np.array([0.95, 0.3, 0.0]),
        "picture-002": np.array([0.0, 1.0, 0.0]),
        "picture-003": np.array([0.0, 0.0, 1.0]),
    }

    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=_distinct_preview,
            scene_print=prints.get,
        ),
    ).plan

    review = plan["final_duplicate_review"]
    assert [row["asset_id"] for row in review["removals"]] == ["picture-001"]
    assert review["scene"]["pairs_compared"] > 0
    assert "picture-001" not in [c["asset_id"] for c in plan["carriers"]]
