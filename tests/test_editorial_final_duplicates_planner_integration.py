"""Completed-film discovery catches an un-nominated repeat without reopening selection."""

import pytest

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.selection_same_picture import SamePicturePairDecision
from immich_memories.operations.cut_progress import announcing_stages
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_visual_body_audience import picture_record


@pytest.mark.parametrize("far_hash", [False, True])
def test_final_actual_planner_removes_un_nominated_repetition_after_completion(tmp_path, far_hash):
    captured = source(tmp_path, seconds=24, pictures=4)
    descriptions = [
        "A clothed adult lifts wooden furniture.",
        "A clothed adult lifts wooden furniture.",
        "Blue sailboat crosses turquoise water.",
        "Orange cat sleeps beneath a tree.",
    ]
    hashes = dict(
        zip(
            sorted(captured.assets),
            (
                "0000000000000000",
                "ffffffffffffffff" if far_hash else "0000000000000000",
                "aaaaaaaaaaaaaaaa",
                "5555555555555555",
            ),
            strict=True,
        )
    )
    observed, compared, hash_requests = [], [], []

    def observe(asset_id):
        observed.append(asset_id)
        return {**picture_record(), "description": descriptions[int(asset_id.rsplit("-", 1)[1])]}

    def get_hashes(asset_ids, records):
        hash_requests.append(tuple(asset_ids))
        assert set(asset_ids) <= set(observed) & set(records)
        return {asset_id: hashes[asset_id] for asset_id in asset_ids}

    def confirm(pairs, records):
        # WHY: this provider boundary reports the controlled duplicate while
        # preserving different content nominated by the same capture episode.
        compared.extend(pairs)
        assert all(records[asset_id]["status"] == "available" for asset_id in pairs[0])
        same = pairs[0] == ("picture-000", "picture-001")
        return (SamePicturePairDecision(*pairs[0], same),), {"scope": "controlled pixel relation"}

    judge = ControlledStoryJudge()
    announced: list[str] = []
    with announcing_stages(lambda update: announced.append(update.label)):
        plan = plan_structure(
            captured,
            StructurePlannerPorts(
                judge=judge,
                thumbnail_hash=lambda _: None,
                rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
                reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
                observe_picture=observe,
                confirm_sampled_pairs=confirm,
                confirm_episode_pairs=confirm,
                sampled_preview_hashes=get_hashes,
            ),
        ).plan
    assert ("picture-000", "picture-001") in compared
    assert len(compared) <= 2 * len(captured.assets)
    assert hash_requests == [tuple(sorted(captured.assets))]
    assert plan["selection_stages"]["before_picture_review"] == 4
    assert plan["selection_stages"]["after_final_duplicate_review"] == 3
    # The same counts are announced while the edit runs, so a watcher sees the
    # long "Editing the memory" stretch move instead of a record nobody reads.
    assert "Editing the memory: 4 pictures going into the family-viewing check" in announced
    assert "Editing the memory: 4 pictures going into the picture review" in announced
    assert "Editing the memory: 3 pictures after the duplicate review" in announced
    assert [c["asset_id"] for c in plan["carriers"]] == [
        "picture-000",
        "picture-002",
        "picture-003",
    ]
    assert plan["content_seconds"] == sum(c["seconds"] for c in plan["carriers"])
    assert plan["final_duplicate_review"]["status"] == "complete"
    assert plan["cut_carriers"][0]["asset_id"] == "picture-001"
    assert plan["cut_carriers"][0]["review_stage"] == "final-duplicates"
    assert not plan["assembly_repair"]["added"]
    assert not any(call["stage"].startswith("reference-entailment") for call in judge.calls)
