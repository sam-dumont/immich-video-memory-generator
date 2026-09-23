"""Completed-film discovery catches an un-nominated repeat without reopening selection."""

import pytest

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.selection_same_picture import SamePicturePairDecision
from immich_memories.operations.cut_progress import announcing_stages
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_visual_body_audience import picture_record

# Cached previews no two of which are within the free pass's 6 bits, so the hash review that
# now runs first on every tier removes nothing and the sampled review sees the whole cut.
DISTINCT_PREVIEWS = (
    "0000000000000000",
    "00000000000000ff",
    "000000000000ff00",
    "0000000000ff0000",
)


def _distinct_preview(asset_id: str) -> str:
    return DISTINCT_PREVIEWS[int(asset_id.rsplit("-", 1)[1])]


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

    def confirm(pairs, records, corroborating_distances=None):
        # WHY: this provider boundary reports the controlled duplicate and keeps the
        # different content that the shared capture episode also nominates.
        compared.extend(pairs)
        assert all(records[asset_id]["status"] == "available" for asset_id in pairs[0])
        same = pairs[0] == ("picture-000", "picture-001")
        # Only a hash nomination hands its own distance; the far pair and every
        # episode neighbour are named without any pixel corroboration.
        assert corroborating_distances == ((0,) if same and not far_hash else (None,))
        return (SamePicturePairDecision(*pairs[0], same),), {"scope": "controlled pixel relation"}

    judge = ControlledStoryJudge()
    announced: list[str] = []
    with announcing_stages(lambda update: announced.append(update.label)):
        plan = plan_structure(
            captured,
            StructurePlannerPorts(
                judge=judge,
                thumbnail_hash=_distinct_preview,
                rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
                reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
                observe_picture=observe,
                confirm_sampled_pairs=confirm,
                sampled_preview_hashes=get_hashes,
            ),
        ).plan
    # One capture episode holds all four, so every nearby pair is now nominated too.
    assert compared == [
        ("picture-000", "picture-001"),
        ("picture-000", "picture-002"),
        ("picture-000", "picture-003"),
        ("picture-002", "picture-003"),
    ]
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
    # The film's opening and closing frames are read rather than glanced at, on this tier too.
    assert [c["seconds"] for c in plan["carriers"]] == [4.5, 4.0, 4.5]
    assert plan["final_duplicate_review"]["status"] == "complete"
    assert plan["cut_carriers"][0]["asset_id"] == "picture-001"
    assert plan["cut_carriers"][0]["review_stage"] == "final-duplicates"
    assert not plan["assembly_repair"]["added"]
    assert not any(call["stage"].startswith("reference-entailment") for call in judge.calls)


def test_a_nearby_episode_neighbour_is_cut_though_pixels_and_captions_differ(tmp_path):
    captured = source(tmp_path, seconds=12, pictures=2)
    captions = {
        "picture-000": "A clothed adult sits by a window on a dark bus.",
        "picture-001": "Two clothed adults face the aisle of a dark bus.",
    }
    far_hashes = {"picture-000": "0000000000000000", "picture-001": "ffffffffffffffff"}
    asked, strict = [], []

    def confirm_episode(pairs, records, corroborating_distances=None):
        # WHY: the image gateway that would look at both pictures.
        asked.append(pairs)
        assert corroborating_distances == (None,)
        assert all(records[asset_id]["status"] == "available" for asset_id in pairs[0])
        return (SamePicturePairDecision(*pairs[0], True),), {"scope": "controlled pixel relation"}

    def confirm_strict(pairs, _records, corroborating_distances=None):
        # WHY: the same gateway asked the strict question, which nothing here nominates.
        strict.append(pairs)
        return (SamePicturePairDecision(*pairs[0], False),), {"scope": "controlled pixel relation"}

    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=_distinct_preview,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
            observe_picture=lambda asset_id: {
                **picture_record(),
                "description": captions[asset_id],
            },
            confirm_sampled_pairs=confirm_strict,
            confirm_episode_pairs=confirm_episode,
            sampled_preview_hashes=lambda ids, _records: {key: far_hashes[key] for key in ids},
        ),
    ).plan

    assert asked == [(("picture-000", "picture-001"),)] and strict == []
    assert [c["asset_id"] for c in plan["carriers"]] == ["picture-000"]
    assert [c["taken"] for c in plan["carriers"]] == sorted(c["taken"] for c in plan["carriers"])
    assert plan["cut_carriers"][0]["asset_id"] == "picture-001"
    assert plan["cut_carriers"][0]["review_stage"] == "final-duplicates"
    assert plan["final_duplicate_review"]["status"] == "complete"


def test_a_film_with_a_model_loses_its_hash_twin_free_and_still_gets_its_sampled_review(tmp_path):
    captured = source(tmp_path, seconds=24, pictures=4)
    previews = {
        "picture-000": "0000000000000000",
        "picture-001": "0000000000000000",
        "picture-002": "000000000000ff00",
        "picture-003": "0000000000ff0000",
    }
    compared = []

    def confirm(pairs, _records, corroborating_distances=None):
        # WHY: the image gateway a sampled review confirms its nominated pairs through.
        compared.extend(pairs)
        return (SamePicturePairDecision(*pairs[0], False),), {"scope": "controlled pixel relation"}

    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ControlledStoryJudge(),
            thumbnail_hash=previews.get,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
            observe_picture=lambda _asset_id: picture_record(),
            confirm_sampled_pairs=confirm,
            sampled_preview_hashes=lambda ids, _records: {key: previews[key] for key in ids},
        ),
    ).plan

    assert [c["asset_id"] for c in plan["carriers"]] == [
        "picture-000",
        "picture-002",
        "picture-003",
    ]
    # The twin went on the cached hashes alone: nothing asked the model about it.
    assert not any("picture-001" in pair for pair in compared)
    # And the sampled review still ran, over what the free pass left.
    assert compared
    review = plan["final_duplicate_review"]
    assert [row["asset_id"] for row in review["removals"]] == ["picture-001"]
    assert [row["asset_id"] for row in review["hash_review"]["removals"]] == ["picture-001"]
    assert review["policy"].startswith("final-displayed-sampled-duplicates")
    assert plan["cut_carriers"][0]["asset_id"] == "picture-001"


def test_the_finished_film_drops_a_scene_it_already_shows_when_it_has_room(tmp_path):
    """Two differently framed shots of one scene hash as strangers; their scene prints agree."""
    import numpy as np

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
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
            scene_print=prints.get,
        ),
    ).plan

    review = plan["final_duplicate_review"]
    assert [row["asset_id"] for row in review["removals"]] == ["picture-001"]
    assert review["scene"]["pairs_compared"] > 0
    assert "picture-001" not in [c["asset_id"] for c in plan["carriers"]]
