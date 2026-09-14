"""A saved cut, not the pool's current ticks, determines a picture's outcome."""

import json

from immich_memories.operations.candidate_fates import CandidateFates


def test_final_plan_wins_over_an_earlier_rejection(tmp_path):
    (tmp_path / "plan.private.json").write_text(
        json.dumps({"carriers": [{"asset_id": "photo", "seconds": 4, "why": "The reunion"}]})
    )
    (tmp_path / "selection-trace.private.json").write_text(
        json.dumps(
            {
                "editorial_passes": [
                    {
                        "name": "duplicate",
                        "rejected": [{"asset_id": "photo", "reason": "Similar frame"}],
                    }
                ]
            }
        )
    )

    fates = CandidateFates.read(tmp_path)

    assert fates.describe("photo") == "In the cut at 0:00: The reunion"


def test_dropped_picture_names_the_pass_and_its_reason(tmp_path):
    (tmp_path / "selection-trace.private.json").write_text(
        json.dumps(
            {
                "clips": {"photo": "still"},
                "editorial_passes": [
                    {
                        "name": "picture_review",
                        "input_ids": ["photo"],
                        "rejected": [
                            {"asset_id": "photo", "reason": "Another frame shows the same moment"}
                        ],
                    }
                ],
            }
        )
    )

    assert CandidateFates.read(tmp_path).describe("photo") == (
        "Left out at the picture review: Another frame shows the same moment"
    )


def test_incomplete_and_missing_evidence_is_not_invented(tmp_path):
    assert CandidateFates.read(None).describe("new") == "No cut recorded yet"
    assert CandidateFates.read(tmp_path).describe("old") == "Outcome not recorded for this cut"
    (tmp_path / "selection-trace.private.json").write_text(
        json.dumps(
            {
                "clips": {"pending": "still", "kept": "video"},
                "editorial_passes": [
                    {
                        "name": "picture_review",
                        "input_ids": ["pending", "kept"],
                        "kept_ids": ["kept"],
                        "unresolved": [{"asset_id": "pending", "reason": "Preview unavailable"}],
                    }
                ],
            }
        )
    )
    fates = CandidateFates.read(tmp_path)
    assert fates.describe("pending") == "Undecided at the picture review: Preview unavailable"
    assert fates.describe("kept") == "Outcome not recorded for this cut"
    assert fates.describe("new") == "Not in this cut's pool"
