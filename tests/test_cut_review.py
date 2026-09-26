"""A picture's review explains proposals without confusing them with the final cut."""

import json

import pytest

from immich_memories.operations.cut_review import read_cut_decisions


def test_protected_picture_keeps_the_model_objection_and_the_rule_that_overruled_it(tmp_path):
    decisions = tmp_path / "derived-decisions"
    decisions.mkdir()
    (decisions / "thin-polish.private.json").write_text(
        json.dumps(
            {
                "ran": True,
                "verdicts": {
                    "original": {
                        "why": "Another portrait from the same moment",
                        "protected": True,
                        "rule": "The owner starred this picture",
                    }
                },
                "slots": [],
            }
        )
    )

    review = read_cut_decisions(tmp_path)

    assert review["original"]["model_reason"] == "Another portrait from the same moment"
    assert review["original"]["kept_reason"] == "The owner starred this picture"
    assert review["original"]["proposed_asset_id"] == ""


def test_offered_alternatives_keep_the_actual_outcome_instead_of_claiming_a_swap(tmp_path):
    decisions = tmp_path / "derived-decisions"
    decisions.mkdir()
    (decisions / "thin-polish.private.json").write_text(
        json.dumps(
            {
                "ran": True,
                "verdicts": {"original": {"why": "Repeated view"}},
                "slots": [
                    {
                        "replacing": "original",
                        "chosen": "candidate",
                        "offered": "2",
                        "outcome": "refused by look-alike",
                    }
                ],
            }
        )
    )

    decision = read_cut_decisions(tmp_path)["original"]

    assert decision["offered_count"] == 2
    assert decision["replacement_outcome"] == "refused by look-alike"
    assert decision["proposed_asset_id"] == "candidate"


@pytest.mark.parametrize("payload", [None, "{unfinished", "[]"])
def test_a_cut_is_reviewable_when_the_optional_model_record_is_missing_or_unreadable(
    tmp_path, payload
):
    if payload is not None:
        folder = tmp_path / "derived-decisions"
        folder.mkdir()
        (folder / "thin-polish.private.json").write_text(payload)

    assert read_cut_decisions(tmp_path) == {}
