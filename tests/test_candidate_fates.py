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
    # Before any cut there is no outcome to expect, so the pool says nothing at all.
    assert CandidateFates.read(None).describe("new") == ""
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


# Shaped like a real saved run: a source check, a first cull, and the planner's
# own final-cut stage that drops everything the plan did not use.
_REAL_SHAPED_TRACE = {
    "clips": {"shipped": "still", "culled": "still", "survivor": "still"},
    "stages": [
        {
            "name": "editorial final cut",
            "kept": 1,
            "dropped": 2,
            "kept_ids": ["shipped"],
            "lost_ids": ["culled", "survivor"],
            "gained_ids": [],
            "notes": {},
        }
    ],
    "editorial_passes": [
        {
            "name": "source-eligibility",
            "input_ids": ["shipped", "culled", "survivor", "hidden"],
            "kept_ids": ["shipped", "culled", "survivor"],
            "rejected": [{"asset_id": "hidden", "reason": "Hidden in Immich"}],
        },
        {
            "name": "pass-1-cull",
            "input_ids": ["shipped", "culled", "survivor"],
            "kept_ids": ["shipped", "survivor"],
            "rejected": [{"asset_id": "culled", "reason": "Another frame shows the same moment"}],
        },
    ],
}


def test_a_picture_every_pass_kept_but_the_plan_did_not_use_names_the_final_cut(tmp_path):
    (tmp_path / "plan.private.json").write_text(
        json.dumps({"carriers": [{"asset_id": "shipped", "seconds": 4, "why": "The reunion"}]})
    )
    (tmp_path / "selection-trace.private.json").write_text(json.dumps(_REAL_SHAPED_TRACE))

    fates = CandidateFates.read(tmp_path)

    assert fates.describe("shipped") == "In the cut at 0:00: The reunion"
    assert fates.describe("hidden") == "Left out at the source check: Hidden in Immich"
    assert fates.describe("culled") == (
        "Left out at the first cull: Another frame shows the same moment"
    )
    assert fates.describe("survivor") == (
        "Left out at the final cut: kept by every pass, not used in the plan"
    )
