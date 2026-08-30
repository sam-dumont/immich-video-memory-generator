"""The final asset cut can reallocate moment slots without escaping its wall."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_description_moment_cut as prototype
import probe_selection_final_cut as final_cut_contract
import probe_smart_edit_matrix as matrix
from probe_selection_final_cut import (
    FINAL_ASSET_CUT_SCHEMA,
    FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
    FineCutCandidate,
    apply_final_asset_sequence_review,
    apply_final_moment_cap,
    final_asset_audit_prompt,
    final_asset_cut_prompt,
    final_asset_delta_validation_prompt,
    final_asset_reconsideration_prompt,
    final_asset_sequence_review_prompt,
    merge_final_asset_audits,
    read_final_asset_audit,
    read_final_asset_cut,
    read_final_asset_delta_validation,
    read_final_asset_reconsideration,
    read_visual_final_asset_audit,
    runtime_final_asset_audit_findings,
    visual_final_asset_audit_groups,
    visual_final_asset_audit_prompt,
)

from immich_memories.analysis.selection_final_duplicates import (
    DOCUMENT_ARTIFACT_WORDS,
    FinalDuplicateNomination,
    FinalDuplicateReview,
)
from immich_memories.analysis.selection_selects import AbsorbedFrame, SamePicturePairDecision

START = datetime(2025, 1, 1, tzinfo=UTC)


def _candidate(index: int, moment: str) -> FineCutCandidate:
    return FineCutCandidate(
        alias=f"A{index:03d}",
        asset_id=f"private-{index}",
        moment_id=moment,
        taken_at=START + timedelta(hours=index),
        media_kind="photo",
        favourite=index == 1,
        description=f"Visible scene {index}",
    )


def _answer(*keep: str, rejected: str = "A003") -> str:
    return json.dumps(
        {
            "schema_version": FINAL_ASSET_CUT_SCHEMA,
            "keep": [
                {"asset_id": alias, "reason": "Adds a distinct visible beat."} for alias in keep
            ],
            "comparisons": [
                {
                    "kept_asset_id": keep[0],
                    "rejected_asset_id": rejected,
                    "reason": "The kept scene carries more lived action.",
                }
            ],
            "overall_reason": "The sequence carries the thesis without repetition.",
        }
    )


def test_final_moment_cap_treats_favourites_as_priority_not_an_exemption() -> None:
    candidates = tuple(
        replace(_candidate(index, "M001"), favourite=True) for index in range(1, 5)
    ) + (replace(_candidate(5, "M002"), favourite=False),)
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The model selected this beat."}
            for candidate in candidates
        ],
        "required_asset_ids": [],
        "comparisons": [],
    }

    capped = apply_final_moment_cap(candidates, cut, max_per_moment=2)

    assert [row["asset_id"] for row in capped["keep"]] == ["A001", "A002", "A005"]
    assert capped["moment_cap"] == {
        "max_per_moment": 2,
        "overfull_moments": 1,
        "removed_asset_ids": ["A003", "A004"],
        "required_overflow_moments": [],
    }


def test_final_moment_cap_uses_the_existing_motion_then_favourite_priority() -> None:
    candidates = (
        replace(_candidate(1, "M001"), favourite=False),
        replace(_candidate(2, "M001"), favourite=True),
        replace(
            _candidate(3, "M001"),
            favourite=False,
            media_kind="video",
            motion_contribution="meaningful",
        ),
    )
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The model selected this beat."}
            for candidate in candidates
        ],
        "required_asset_ids": [],
        "comparisons": [],
    }

    capped = apply_final_moment_cap(candidates, cut, max_per_moment=2)

    assert [row["asset_id"] for row in capped["keep"]] == ["A002", "A003"]


def test_final_moment_cap_never_drops_required_assets_or_refills_freed_room() -> None:
    candidates = tuple(_candidate(index, "M001") for index in range(1, 5))
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The model selected this beat."}
            for candidate in candidates
        ],
        "required_asset_ids": ["A003", "A004"],
        "comparisons": [
            {
                "kept_asset_id": "A001",
                "rejected_asset_id": "A099",
                "reason": "This stale comparison must leave with its capped keep.",
            }
        ],
    }

    capped = apply_final_moment_cap(candidates, cut, max_per_moment=2)

    assert [row["asset_id"] for row in capped["keep"]] == ["A003", "A004"]
    assert capped["comparisons"] == []
    assert len(capped["keep"]) == 2


def test_prompt_says_moment_slots_can_be_reallocated() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    prompt = final_asset_cut_prompt(
        candidates,
        memory_label="A month",
        memory_type="monthly_highlights",
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded month"},
        capacity=2,
        required_aliases=("A001",),
    )

    assert "several genuinely different" in prompt
    assert "assets from one rich moment" in prompt
    assert "retain none from a weaker shortlisted moment" in prompt
    assert "return at most 1 additional assets" in prompt
    assert "video, then meaningful live-motion, then photo" in prompt
    assert "tie-breaker" in prompt


def test_asset_reviews_do_not_infer_relationship_importance_from_anonymous_touch() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"))

    cut_prompt = final_asset_cut_prompt(
        candidates,
        memory_label="A year",
        memory_type="year_in_review",
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
        capacity=2,
    )
    review_prompt = final_asset_sequence_review_prompt(
        candidates,
        proposed_aliases=("A001", "A002"),
        required_aliases=(),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
    )

    for prompt in (cut_prompt, review_prompt):
        normalized = " ".join(prompt.split())
        assert "no supplied people context" in normalized
        assert "event atmosphere, not evidence of relationship importance" in normalized
        assert "kissing, hugging, or physical closeness" in normalized
        assert "may still earn runtime when it uniquely carries" in normalized


def test_global_sequence_review_can_underfill_repetition_across_chapters() -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A cat resting on the same sofa."),
        replace(_candidate(2, "M002"), description="The same cat resting on the same sofa."),
        replace(_candidate(3, "M003"), description="The same cat resting on the same sofa."),
        replace(_candidate(4, "M004"), description="Friends celebrating around a birthday cake."),
    )
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v1",
            "keep": ["A001", "A004"],
            "cut": [
                {"asset_id": "A002", "reason": "Repeats the unchanged sofa scene."},
                {"asset_id": "A003", "reason": "A third version adds no new beat."},
            ],
            "overall_reason": "The shorter sequence keeps the distinct lived events.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002", "A003", "A004"),
        required_aliases=(),
    )

    assert review["status"] == "approved"
    assert review["keep"] == ["A001", "A004"]
    assert [row["asset_id"] for row in review["cut"]] == ["A002", "A003"]


def test_global_sequence_review_accepts_a_compact_cut_only_verdict() -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 5))
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v2",
            "cut": [
                {"asset_id": "A002", "reason": "Repeats the first event."},
                {"asset_id": "A003", "reason": "Adds no distinct contribution."},
            ],
            "overall_reason": "The remaining pair carries the memory without echoes.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002", "A003", "A004"),
    )

    assert review["status"] == "approved"
    assert review["keep"] == ["A001", "A004"]
    assert [row["asset_id"] for row in review["cut"]] == ["A002", "A003"]


def test_global_sequence_review_restores_favourite_without_discarding_valid_cuts() -> None:
    candidates = (
        _candidate(1, "M001"),
        replace(_candidate(2, "M001"), favourite=False),
        replace(_candidate(3, "M002"), favourite=False),
        replace(_candidate(4, "M003"), favourite=False),
    )
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v2",
            "cut": [
                {"asset_id": "A001", "reason": "Repeats the other view of this moment."},
                {"asset_id": "A003", "reason": "Repeats an earlier event contribution."},
            ],
            "overall_reason": "The shorter sequence removes repeated contributions.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002", "A003", "A004"),
    )

    assert review["status"] == "approved"
    assert review["keep"] == ["A001", "A002", "A004"]
    assert review["cut"] == [
        {"asset_id": "A003", "reason": "Repeats an earlier event contribution."}
    ]
    assert review["restored_favourite_assets"] == 1


def test_global_sequence_review_allows_meaningful_motion_to_replace_favourite() -> None:
    candidates = (
        _candidate(1, "M001"),
        replace(
            _candidate(2, "M001"),
            favourite=False,
            media_kind="live-motion",
            motion_contribution="meaningful",
        ),
        replace(_candidate(3, "M002"), favourite=False),
    )
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v2",
            "cut": [
                {
                    "asset_id": "A001",
                    "reason": "The meaningful motion sibling carries the same moment better.",
                }
            ],
            "overall_reason": "The stronger moving carrier preserves the favourite moment.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002", "A003"),
    )

    assert review["status"] == "approved"
    assert review["keep"] == ["A002", "A003"]
    assert [row["asset_id"] for row in review["cut"]] == ["A001"]
    assert review["restored_favourite_assets"] == 0


def test_global_sequence_review_keeps_an_alias_listed_on_both_sides() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M003"),
    )
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v1",
            "keep": ["A001", "A002"],
            "cut": [
                {"asset_id": "A002", "reason": "Conflicts with the explicit keep."},
                {"asset_id": "A003", "reason": "Repeats the stronger scene."},
            ],
            "overall_reason": "The shorter sequence keeps the distinct lived events.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002", "A003"),
    )

    assert review["status"] == "approved"
    assert review["keep"] == ["A001", "A002"]
    assert [row["asset_id"] for row in review["cut"]] == ["A003"]
    assert review["discarded_overlapping_cuts"] == 1


def test_probe_records_cross_date_duplicate_reduction_before_the_global_cut() -> None:
    wall = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "First beat."},
            {"asset_id": "A002", "reason": "Re-imported beat."},
            {"asset_id": "A003", "reason": "Distinct beat."},
        ],
        "required_asset_ids": [],
    }
    duplicate_review = FinalDuplicateReview(
        survivors=(
            SimpleNamespace(asset_id="private-1"),
            SimpleNamespace(asset_id="private-3"),
        ),
        absorbed=(
            AbsorbedFrame(
                asset_id="private-2",
                kept_asset_id="private-1",
                reason="confirmed copy",
            ),
        ),
        nominations=(
            FinalDuplicateNomination(
                earlier_asset_id="private-1",
                later_asset_id="private-2",
                signals=("perceptual-description",),
                perceptual_distance=0,
            ),
        ),
        decisions=(
            SamePicturePairDecision(
                earlier_asset_id="private-1",
                later_asset_id="private-2",
                same=True,
            ),
        ),
    )

    reviewed, selected_aliases = matrix._apply_final_duplicate_review(
        cut,
        wall=wall,
        review=duplicate_review,
    )

    assert [row["asset_id"] for row in reviewed["pre_duplicate_review_keep"]] == [
        "A001",
        "A002",
        "A003",
    ]
    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A003"]
    assert selected_aliases == {"A001", "A003"}
    assert reviewed["duplicate_review"]["absorbed"][0]["kept_asset_id"] == "private-1"


def test_post_deliberation_duplicate_review_preserves_the_initial_audit() -> None:
    wall = (_candidate(1, "M001"), _candidate(2, "M002"))
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "Initial beat."},
            {"asset_id": "A002", "reason": "Newly admitted copy."},
        ],
        "pre_duplicate_review_keep": [{"asset_id": "A001", "reason": "Initial beat."}],
        "duplicate_review": {"absorbed": []},
    }
    review = FinalDuplicateReview(
        survivors=(SimpleNamespace(asset_id="private-1"),),
        absorbed=(
            AbsorbedFrame(
                asset_id="private-2",
                kept_asset_id="private-1",
                reason="confirmed copy",
            ),
        ),
        nominations=(
            FinalDuplicateNomination(
                earlier_asset_id="private-1",
                later_asset_id="private-2",
                signals=("perceptual-description",),
                perceptual_distance=0,
            ),
        ),
        decisions=(
            SamePicturePairDecision(
                earlier_asset_id="private-1",
                later_asset_id="private-2",
                same=True,
            ),
        ),
    )

    reviewed, selected_aliases = matrix._apply_final_duplicate_review(
        cut,
        wall=wall,
        review=review,
        pre_keep_key="pre_post_deliberation_duplicate_review_keep",
        review_key="post_deliberation_duplicate_review",
    )

    assert reviewed["pre_duplicate_review_keep"] == cut["pre_duplicate_review_keep"]
    assert reviewed["duplicate_review"] == cut["duplicate_review"]
    assert [row["asset_id"] for row in reviewed["pre_post_deliberation_duplicate_review_keep"]] == [
        "A001",
        "A002",
    ]
    assert reviewed["post_deliberation_duplicate_review"]["absorbed"][0]["asset_id"] == "private-2"
    assert selected_aliases == {"A001"}


@pytest.mark.parametrize(
    ("deliberation", "expected"),
    [
        (
            {
                "iterations": [
                    {
                        "outcome": "accepted",
                        "input_aliases": ["A001"],
                        "output_aliases": ["A001", "A002"],
                    }
                ]
            },
            True,
        ),
        (
            {
                "iterations": [
                    {
                        "outcome": "accepted",
                        "input_aliases": ["A001", "A002"],
                        "output_aliases": ["A001"],
                    }
                ]
            },
            False,
        ),
        (
            {
                "iterations": [
                    {
                        "outcome": "rejected",
                        "input_aliases": ["A001"],
                        "output_aliases": ["A001", "A002"],
                    }
                ]
            },
            False,
        ),
    ],
)
def test_post_deliberation_duplicate_review_only_follows_accepted_additions(
    deliberation: dict[str, object], expected: bool
) -> None:
    assert matrix._deliberation_added_assets(deliberation) is expected


def test_global_sequence_review_prompt_protects_change_while_limiting_repetition() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M003"),
    )

    prompt = final_asset_sequence_review_prompt(
        candidates,
        proposed_aliases=("A001", "A003"),
        required_aliases=("A001",),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A year of change."},
    )

    lower_prompt = " ".join(prompt.lower().split())
    assert "reduce-only" in prompt
    assert "across the whole draft" in prompt
    assert "ordinary texture is bounded" in lower_prompt
    assert "quiet consequential record" in lower_prompt
    assert "different timestamps do not prove" in lower_prompt
    assert "smallest distinct set" in lower_prompt
    assert "continuous occasion or event family" in lower_prompt
    assert "a new date, outfit, route, or episode label" in lower_prompt
    assert "distinct setup, action, payoff, relationship turn, or visible" in lower_prompt
    assert "start with one strongest carrier per event family" in lower_prompt
    assert "a second should add a genuinely different contribution" in lower_prompt
    assert "four or more from one family is exceptional" in lower_prompt
    assert "duration capacity are ceilings, never targets" in lower_prompt
    assert (
        "a visible face or selfie is not automatically a relationship contribution" in lower_prompt
    )
    assert "a repeated selfie pose can be weaker than a strong view of the place" in lower_prompt
    assert "an unpeopled view may carry that beat" in lower_prompt
    assert "repeated postcard views still diminish" in lower_prompt
    assert "A001" in prompt
    assert "A003" in prompt
    assert "\nA002 |" not in prompt


def test_global_sequence_review_prompt_requests_only_the_cut_delta() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))

    prompt = final_asset_sequence_review_prompt(
        candidates,
        proposed_aliases=("A001", "A002"),
        required_aliases=("A001",),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A concise memory."},
    )

    assert '"schema_version":"description-final-sequence-review-v2"' in prompt
    assert '"cut":[' in prompt
    assert '"keep":[' not in prompt
    assert "Partition every proposed asset exactly once" not in prompt
    assert "Anything you do not name in cut remains selected" in prompt
    assert "Never put a required asset in cut" in prompt


def test_final_asset_audit_accepts_stability_only_without_findings() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    raw = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "Every retained visual makes a distinct contribution.",
        }
    )

    audit = read_final_asset_audit(
        raw,
        candidates,
        current_aliases=("A001", "A002"),
    )

    assert audit == {
        "verdict": "stable",
        "findings": [],
        "overall_reason": "Every retained visual makes a distinct contribution.",
    }


def test_final_asset_audit_requires_a_grounded_visible_defect_for_revision() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))
    raw = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "revise",
            "findings": [
                {
                    "kind": "subject_or_pose_overweight",
                    "asset_ids": ["A001", "A002"],
                    "visible_defect": "Both frames repeat the same face-forward pose.",
                    "missing_contribution": (
                        "The current event is not grounded by action or a sense of place."
                    ),
                }
            ],
            "overall_reason": "One repeated contribution warrants checking the reservoirs.",
        }
    )

    audit = read_final_asset_audit(
        raw,
        candidates,
        current_aliases=("A001", "A002", "A003"),
    )

    assert audit["verdict"] == "revise"
    assert audit["findings"] == [
        {
            "finding_id": "F001",
            "kind": "subject_or_pose_overweight",
            "asset_ids": ["A001", "A002"],
            "visible_defect": "Both frames repeat the same face-forward pose.",
            "missing_contribution": (
                "The current event is not grounded by action or a sense of place."
            ),
        }
    ]


def test_final_asset_audit_rejects_a_finding_about_a_noncurrent_candidate() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))
    raw = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "revise",
            "findings": [
                {
                    "kind": "missing_place_or_progression",
                    "asset_ids": ["A003"],
                    "visible_defect": "The current cut lacks a grounded view of the setting.",
                    "missing_contribution": "Place or route progression, if the pool contains it.",
                }
            ],
            "overall_reason": "The reservoir might contain a stronger carrier.",
        }
    )

    with pytest.raises(ValueError, match="not grounded"):
        read_final_asset_audit(
            raw,
            candidates,
            current_aliases=("A001", "A002"),
        )


def test_final_asset_audit_prompt_makes_stability_the_unbiased_default() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))

    prompt = final_asset_audit_prompt(
        candidates,
        current_aliases=("A001", "A003"),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
    )

    lower_prompt = " ".join(prompt.lower().split())
    assert "stable is the default verdict" in lower_prompt
    assert "do not presume that a better reservoir candidate exists" in lower_prompt
    assert "a specific visible defect" in lower_prompt
    assert "exact current aliases" in lower_prompt
    assert "event_family_repetition" in prompt
    assert "event_or_window_overweight" not in prompt
    assert "should something change" not in lower_prompt
    assert "A001" in prompt
    assert "A003" in prompt
    assert "\nA002 |" not in prompt


def test_runtime_audit_flags_only_a_dense_event_window_above_four_assets() -> None:
    candidates = tuple(
        replace(_candidate(index, f"M{index:03d}"), taken_at=START + timedelta(days=index))
        for index in range(1, 7)
    )

    below_threshold = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=("A001", "A002", "A003", "A004"),
    )
    dense_window = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=("A001", "A002", "A003", "A004", "A005", "A006"),
    )

    assert below_threshold == []
    assert len(dense_window) == 1
    assert dense_window[0]["focus_id"] == "R001"
    assert dense_window[0]["asset_ids"] == [
        "A001",
        "A002",
        "A003",
        "A004",
        "A005",
        "A006",
    ]
    assert "6 assets occur inside one seven-day window" in dense_window[0]["observation"]
    assert "Do they repeat the same contribution" in dense_window[0]["review_question"]
    assert "defect" not in dense_window[0]["observation"].lower()


def test_final_asset_audit_prompt_requires_each_neutral_focus_to_be_assessed() -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 7))
    review_focus = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=tuple(candidate.alias for candidate in candidates),
    )

    prompt = final_asset_audit_prompt(
        candidates,
        current_aliases=tuple(candidate.alias for candidate in candidates),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
        review_focus=review_focus,
    )

    lower_prompt = " ".join(prompt.lower().split())
    assert "assess every cited focus" in lower_prompt
    assert "do not assume that every focus needs a finding" in lower_prompt
    assert "attention cues, not defects" in lower_prompt


def test_runtime_audit_surfaces_same_moment_concentration_below_window_threshold() -> None:
    candidates = tuple(_candidate(index, "M001") for index in range(1, 5))

    focus = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=("A001", "A002", "A003", "A004"),
    )

    assert len(focus) == 1
    assert focus[0]["focus_kind"] == "same_moment"
    assert focus[0]["asset_ids"] == ["A001", "A002", "A003", "A004"]
    assert "4 selected assets come from one production moment" in focus[0]["observation"]
    assert "defect" not in focus[0]["observation"].lower()


def test_runtime_audit_surfaces_same_episode_across_distinct_moments() -> None:
    candidates = tuple(
        replace(_candidate(index, f"M{index:03d}"), episode_id="E001") for index in range(1, 4)
    )

    focus = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=("A001", "A002", "A003"),
    )

    assert len(focus) == 1
    assert focus[0]["focus_kind"] == "same_episode"
    assert focus[0]["asset_ids"] == ["A001", "A002", "A003"]
    assert "3 selected assets come from one production episode" in focus[0]["observation"]
    assert "defect" not in focus[0]["observation"].lower()


def test_runtime_audit_does_not_repeat_the_same_moment_as_an_episode() -> None:
    candidates = tuple(
        replace(_candidate(index, "M001"), episode_id="E001") for index in range(1, 5)
    )

    focus = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=("A001", "A002", "A003", "A004"),
    )

    assert len(focus) == 1
    assert focus[0]["focus_kind"] == "same_moment"


def test_runtime_audit_surfaces_multiple_disjoint_dense_windows() -> None:
    days = (1, 2, 3, 4, 5, 20, 21, 22, 23, 24)
    candidates = tuple(
        replace(
            _candidate(index, f"M{index:03d}"),
            taken_at=START + timedelta(days=day),
        )
        for index, day in enumerate(days, start=1)
    )

    focus = runtime_final_asset_audit_findings(
        candidates,
        current_aliases=tuple(candidate.alias for candidate in candidates),
    )
    windows = [row for row in focus if row["focus_kind"] == "dense_window"]

    assert [row["asset_ids"] for row in windows] == [
        ["A001", "A002", "A003", "A004", "A005"],
        ["A006", "A007", "A008", "A009", "A010"],
    ]


def test_final_asset_reconsideration_can_swap_a_repeated_selfie_for_grounded_place() -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A face-forward selfie at the trailhead."),
        replace(_candidate(2, "M001"), description="A similar face-forward trail selfie."),
        replace(
            _candidate(3, "M001"),
            description="A wide ridge view shows the route, weather, and surrounding scale.",
        ),
        replace(_candidate(4, "M002"), description="A family meal around a table."),
    )
    audit = {
        "verdict": "revise",
        "findings": [
            {
                "finding_id": "F001",
                "kind": "subject_or_pose_overweight",
                "asset_ids": ["A001", "A002"],
                "visible_defect": "Both frames repeat the same face-forward pose.",
                "missing_contribution": "The hike lacks route, weather, and scale.",
            }
        ],
        "overall_reason": "One repeated contribution warrants checking the reservoirs.",
    }
    raw = json.dumps(
        {
            "schema_version": "description-final-asset-reconsideration-v1",
            "changes": [
                {
                    "finding_id": "F001",
                    "add_asset_ids": ["A003"],
                    "remove_asset_ids": ["A002"],
                    "reason": "The ridge view supplies place and route absent from the second selfie.",
                }
            ],
            "overall_reason": "The swap preserves people while restoring the hike's setting.",
        }
    )

    proposal = read_final_asset_reconsideration(
        raw,
        candidates,
        current_aliases=("A001", "A002", "A004"),
        required_aliases=("A004",),
        capacity=3,
        audit=audit,
    )

    assert proposal["keep"] == ["A001", "A003", "A004"]
    assert proposal["added"] == ["A003"]
    assert proposal["removed"] == ["A002"]


def test_reconsideration_prompt_reopens_candidates_from_every_selected_moment() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M003"),
    )
    audit = {
        "verdict": "revise",
        "findings": [
            {
                "finding_id": "F001",
                "kind": "repetition",
                "asset_ids": ["A001", "A002"],
                "visible_defect": "The pair repeats one contribution.",
                "missing_contribution": "A distinct lived beat, if present.",
            }
        ],
        "overall_reason": "One grounded defect crossed the threshold.",
    }

    prompt = final_asset_reconsideration_prompt(
        candidates,
        current_aliases=("A001", "A002"),
        required_aliases=("A001",),
        capacity=3,
        audit=audit,
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
    )

    assert "all post-Selects candidates from every selected moment" in prompt
    assert "A001|M001|" in prompt
    assert "A002|M002|" in prompt
    assert "A003|M003|" in prompt
    assert prompt.index("ALL ELIGIBLE RESERVOIR CANDIDATES") < prompt.index("CURRENT DRAFT ALIASES")
    assert "Return an empty changes list when no grounded candidate is better" in prompt
    assert "Capacity is a ceiling, never a target" in prompt


def test_delta_validation_accepts_only_when_every_changed_finding_is_supported() -> None:
    audit = {
        "verdict": "revise",
        "findings": [
            {
                "finding_id": "F001",
                "kind": "repetition",
                "asset_ids": ["A001", "A002"],
                "visible_defect": "Two frames repeat one contribution.",
                "missing_contribution": "A distinct event carrier, if present.",
            },
            {
                "finding_id": "F002",
                "kind": "missing_place_or_progression",
                "asset_ids": ["A004"],
                "visible_defect": "The trip has people but no visible setting.",
                "missing_contribution": "Place and route, if present.",
            },
        ],
        "overall_reason": "Two grounded defects crossed the threshold.",
    }
    proposal = {
        "keep": ["A001", "A003", "A005"],
        "added": ["A003", "A005"],
        "removed": ["A002", "A004"],
        "changes": [
            {
                "finding_id": "F001",
                "add_asset_ids": ["A003"],
                "remove_asset_ids": ["A002"],
                "reason": "A003 adds a distinct action.",
            },
            {
                "finding_id": "F002",
                "add_asset_ids": ["A005"],
                "remove_asset_ids": ["A004"],
                "reason": "A005 visibly carries the route.",
            },
        ],
        "overall_reason": "Both swaps address their cited findings.",
    }
    raw = json.dumps(
        {
            "schema_version": "description-final-asset-delta-validation-v1",
            "verdict": "accept",
            "supported_finding_ids": ["F001", "F002"],
            "reason": "Every changed alias visibly resolves its cited defect.",
        }
    )

    validation = read_final_asset_delta_validation(raw, audit=audit, proposal=proposal)

    assert validation["verdict"] == "accept"
    assert validation["supported_finding_ids"] == ["F001", "F002"]


def test_delta_validation_prompt_rejects_novelty_without_contribution() -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="Friends walking together on a trail."),
        replace(_candidate(2, "M001"), description="A scenic but generic mountain view."),
        replace(_candidate(3, "M002"), description="A family meal around a table."),
    )
    audit = {
        "verdict": "revise",
        "findings": [
            {
                "finding_id": "F001",
                "kind": "repetition",
                "asset_ids": ["A001"],
                "visible_defect": "The trail contribution repeats elsewhere.",
                "missing_contribution": "A distinct beat, if present.",
            }
        ],
        "overall_reason": "One possible repetition crossed the threshold.",
    }
    proposal = {
        "keep": ["A002", "A003"],
        "added": ["A002"],
        "removed": ["A001"],
        "changes": [
            {
                "finding_id": "F001",
                "add_asset_ids": ["A002"],
                "remove_asset_ids": ["A001"],
                "reason": "The mountain view is more attractive.",
            }
        ],
        "overall_reason": "A scenic swap.",
    }

    prompt = final_asset_delta_validation_prompt(
        candidates,
        before_aliases=("A001", "A003"),
        proposal=proposal,
        audit=audit,
    )

    lower_prompt = " ".join(prompt.lower().split())
    assert "accept only if every changed alias" in lower_prompt
    assert "more attractive, scenic, clear, or simply different is insufficient" in lower_prompt
    assert "BEFORE SEQUENCE" in prompt
    assert "AFTER SEQUENCE" in prompt
    assert "A001" in prompt
    assert "A002" in prompt
    assert "A003" in prompt


def test_iterative_final_review_stops_after_one_stable_audit(monkeypatch, tmp_path: Path) -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "Distinct first beat."},
            {"asset_id": "A002", "reason": "Distinct second beat."},
        ],
        "required_asset_ids": [],
    }
    stable = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "Every current visual contributes a distinct beat.",
        }
    )
    prompts: list[str] = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, stable, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=3,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=3,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002"]
    assert deliberation["stop_reason"] == "stable"
    assert len(deliberation["iterations"]) == 1
    assert deliberation["iterations"][0]["outcome"] == "unchanged"
    assert len(prompts) == 1
    assert "ALL ELIGIBLE RESERVOIR CANDIDATES" not in prompts[0]


def test_runtime_density_focus_does_not_override_a_stable_audit(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 7))
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "Tentatively distinct beat."}
            for candidate in candidates[:5]
        ],
        "required_asset_ids": [],
    }
    stable = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "All five concentrated beats are visibly distinct.",
        }
    )
    prompts: list[str] = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, stable, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=6,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == [
        "A001",
        "A002",
        "A003",
        "A004",
        "A005",
    ]
    assert deliberation["stop_reason"] == "stable"
    assert deliberation["iterations"][0]["audit"]["verdict"] == "stable"
    assert len(prompts) == 1
    assert "MECHANICAL REVIEW FOCUS" in prompts[0]
    assert "not itself a defect" in prompts[0]
    assert all(candidate.alias in prompts[0] for candidate in candidates[:5])
    assert "ALL ELIGIBLE RESERVOIR CANDIDATES" not in prompts[0]


def test_small_visual_audit_uses_one_whole_wall_instead_of_overlapping_focuses() -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 9))
    current = tuple(candidate.alias for candidate in candidates[:6])
    focus = [
        {
            "focus_id": "R001",
            "focus_kind": "dense_window",
            "asset_ids": list(current[:5]),
            "observation": "Five assets occur in one week.",
            "review_question": "Do they repeat one contribution?",
        }
    ]

    groups = visual_final_asset_audit_groups(
        candidates,
        current_aliases=current,
        review_focus=focus,
    )

    assert groups == (
        {
            "group_id": "V001",
            "focus_kind": "whole_sequence",
            "asset_ids": list(current),
            "observation": "The complete current cut contains 6 assets.",
            "review_question": "Does the whole visible sequence contain a concrete repetition or coverage defect?",
        },
    )


def test_large_visual_audit_uses_the_densest_grounded_focuses_in_sequence_order() -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 31))
    current = tuple(candidate.alias for candidate in candidates)
    focus = [
        {
            "focus_id": "R001",
            "focus_kind": "same_episode",
            "asset_ids": list(current[12:18]),
            "observation": "Six assets share one episode.",
            "review_question": "Do they show useful progression?",
        },
        {
            "focus_id": "R002",
            "focus_kind": "dense_window",
            "asset_ids": list(current[2:10]),
            "observation": "Eight assets occur in one week.",
            "review_question": "Do they repeat one contribution?",
        },
    ]

    groups = visual_final_asset_audit_groups(
        candidates,
        current_aliases=current,
        review_focus=focus,
        max_groups=1,
    )

    assert len(groups) == 1
    assert groups[0]["group_id"] == "V001"
    assert groups[0]["focus_kind"] == "dense_window"
    assert groups[0]["asset_ids"] == list(current[2:10])


def test_visual_audit_prompt_is_neutral_and_preserves_long_trip_variation() -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A couple resting beside a trail."),
        replace(_candidate(2, "M001"), description="A valley and route below the ridge."),
    )
    group = {
        "group_id": "V001",
        "focus_kind": "same_episode",
        "asset_ids": ["A001", "A002"],
        "observation": "Two assets share one trip episode.",
        "review_question": "Do they carry distinct people and place beats?",
    }

    prompt = visual_final_asset_audit_prompt(
        candidates,
        current_aliases=("A001", "A002"),
        group=group,
        tile_mapping=((1, "A001"), (2, "A002")),
        editorial_brief="Make a truthful trip memory.",
        thesis={"thesis": "A shared mountain journey."},
    )

    lower = " ".join(prompt.lower().split())
    assert "stable is the default verdict" in lower
    assert "multiple visuals from a long trip" in lower
    assert "useful when they show genuinely different phases" in lower
    assert "tile 1 = a001" in lower
    assert "tile 2 = a002" in lower
    assert FINAL_VISUAL_ASSET_AUDIT_SCHEMA in prompt


def test_visual_audit_reader_and_merger_promote_one_grounded_visual_finding() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"))
    visual_raw = json.dumps(
        {
            "schema_version": FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
            "verdict": "revise",
            "findings": [
                {
                    "kind": "missing_place_or_progression",
                    "asset_ids": ["A001", "A002"],
                    "visible_defect": "Both visible frames are close portraits at one stop.",
                    "missing_contribution": "The route or setting, if the reservoirs contain it.",
                }
            ],
            "overall_reason": "The visible trip fragment lacks a place carrier.",
        }
    )
    visual = read_visual_final_asset_audit(
        visual_raw,
        candidates,
        current_aliases=("A001", "A002"),
    )
    text_stable = {
        "verdict": "stable",
        "findings": [],
        "overall_reason": "The descriptions appear distinct.",
    }

    merged = merge_final_asset_audits((text_stable, visual))

    assert merged["verdict"] == "revise"
    assert merged["findings"][0]["finding_id"] == "F001"
    assert merged["findings"][0]["kind"] == "missing_place_or_progression"
    assert merged["source_audits"] == 2


def test_visual_audit_runner_attaches_the_current_wall_and_returns_grounded_findings(
    tmp_path: Path,
) -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 7))
    current = tuple(candidate.alias for candidate in candidates)
    raw = json.dumps(
        {
            "schema_version": FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
            "verdict": "revise",
            "findings": [
                {
                    "kind": "subject_or_pose_overweight",
                    "asset_ids": ["A001", "A002"],
                    "visible_defect": "Two visible frames repeat one close pose.",
                    "missing_contribution": "A distinct action or setting, if available.",
                }
            ],
            "overall_reason": "One repeated visible contribution crosses the threshold.",
        }
    )
    requests = []

    class Requester:
        def ask(self, request):
            requests.append(request)
            return SimpleNamespace(raw_text=raw)

    audit = matrix._run_visual_final_asset_audit(
        candidates,
        current_aliases=current,
        case=matrix.Case(
            key="case",
            label="A memory",
            product="year_in_review",
            ranges=(),
            target_seconds=600.0,
            brief="Make a truthful memory.",
        ),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {
                "chapter_id": "C001",
                "label": "the matching chapter",
                "moment_ids": [candidate.moment_id for candidate in candidates],
                "thesis": "A mountain trip develops across several distinct phases.",
            },
            {
                "chapter_id": "C002",
                "label": "an unrelated chapter",
                "moment_ids": ["M999"],
                "thesis": "This evidence must not leak into the group audit.",
            },
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-audit",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
    )

    assert audit is not None
    assert audit["verdict"] == "revise"
    assert audit["findings"][0]["kind"] == "subject_or_pose_overweight"
    assert audit["groups"] == [
        {
            "group_id": "V001",
            "focus_kind": "whole_sequence",
            "asset_count": 6,
            "verdict": "revise",
            "findings": 1,
        }
    ]
    assert len(requests) == 1
    request = requests[0]
    assert request.pass_name == matrix.VISUAL_FINAL_AUDIT_PASS_NAME
    assert request.schema_version == FINAL_VISUAL_ASSET_AUDIT_SCHEMA
    assert request.ordered_input_ids == tuple(candidate.asset_id for candidate in candidates)
    assert len(request.pages[0].tile_refs) == 6
    assert "stable is the default verdict" in " ".join(request.prompt.lower().split())
    assert "LOCAL STORY EVIDENCE" in request.prompt
    assert "A mountain trip develops across several distinct phases." in request.prompt
    assert "This evidence must not leak into the group audit." not in request.prompt
    assert "A grounded year." not in request.prompt
    assert "Inspect this chapter sequence as a whole." in request.prompt
    assert "MECHANICAL ATTENTION CUE" not in request.prompt


def test_visual_audit_can_trigger_reconsideration_when_text_audit_is_stable(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A face-forward selfie at a trailhead."),
        replace(_candidate(2, "M001"), description="A second close trail selfie."),
        replace(_candidate(3, "M001"), description="A wide view shows the route and valley."),
    )
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "Shows the hikers."},
            {"asset_id": "A002", "reason": "Shows a second trail stop."},
        ],
        "required_asset_ids": [],
    }
    text_stable = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "The descriptions look distinct.",
        }
    )
    proposal = json.dumps(
        {
            "schema_version": "description-final-asset-reconsideration-v1",
            "changes": [
                {
                    "finding_id": "F001",
                    "add_asset_ids": ["A003"],
                    "remove_asset_ids": [],
                    "reason": "A003 adds the missing route and setting.",
                }
            ],
            "overall_reason": "The addition supplies the cited visual gap.",
        }
    )
    accept = json.dumps(
        {
            "schema_version": "description-final-asset-delta-validation-v1",
            "verdict": "accept",
            "supported_finding_ids": ["F001"],
            "reason": "The added frame visibly supplies the missing contribution.",
        }
    )
    answers = iter((text_stable, proposal, accept, text_stable))

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    visual_calls: list[tuple[tuple[str, ...], int]] = []

    def visual_audit(current: tuple[str, ...], iteration: int):
        visual_calls.append((current, iteration))
        if iteration == 1:
            return {
                "verdict": "revise",
                "findings": [
                    {
                        "finding_id": "F001",
                        "kind": "missing_place_or_progression",
                        "asset_ids": ["A001", "A002"],
                        "visible_defect": "Both visible frames are close portraits at trail stops.",
                        "missing_contribution": "The route or valley, if available.",
                    }
                ],
                "overall_reason": "The visible wall lacks a place carrier.",
            }
        return {
            "verdict": "stable",
            "findings": [],
            "overall_reason": "The revised visible wall carries people and place.",
        }

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=3,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=3,
            visual_audit=visual_audit,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002", "A003"]
    assert [row["outcome"] for row in deliberation["iterations"]] == [
        "accepted",
        "unchanged",
    ]
    assert visual_calls == [
        (("A001", "A002"), 1),
        (("A001", "A002", "A003"), 2),
    ]


def test_visual_pool_groups_reopen_every_candidate_in_each_selected_chapter() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
        _candidate(3, "M002"),
        _candidate(4, "M002"),
    )
    readings = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001"],
            "thesis": "The first chapter.",
        },
        {
            "chapter_id": "C002",
            "label": "second",
            "moment_ids": ["M002"],
            "thesis": "The second chapter.",
        },
    )

    groups = final_cut_contract.visual_final_pool_groups(
        candidates,
        current_aliases=("A001", "A003"),
        chapter_readings=readings,
    )

    assert groups == (
        {
            "group_id": "P001",
            "chapter_id": "C001",
            "label": "first",
            "current_asset_ids": ["A001"],
            "asset_ids": ["A001", "A002"],
            "story_evidence": {
                "chapter_id": "C001",
                "label": "first",
                "thesis": "The first chapter.",
            },
        },
        {
            "group_id": "P002",
            "chapter_id": "C002",
            "label": "second",
            "current_asset_ids": ["A003"],
            "asset_ids": ["A003", "A004"],
            "story_evidence": {
                "chapter_id": "C002",
                "label": "second",
                "thesis": "The second chapter.",
            },
        },
    )


def test_visual_pool_findings_prioritize_isolated_occasions_before_episode_siblings() -> None:
    candidates = (
        replace(
            _candidate(1, "M001"),
            episode_id="E001",
            favourite=False,
            taken_at=START + timedelta(days=31, hours=1),
        ),
        replace(
            _candidate(2, "M002"),
            episode_id="E001",
            favourite=False,
            taken_at=START + timedelta(days=31, hours=2),
        ),
        replace(
            _candidate(3, "M003"),
            episode_id="E002",
            favourite=False,
            taken_at=START + timedelta(days=31, hours=3),
        ),
        replace(
            _candidate(4, "M004"),
            episode_id="E003",
            favourite=True,
            taken_at=START + timedelta(days=31, hours=4),
        ),
    )

    findings = final_cut_contract.runtime_final_pool_findings(
        candidates,
        current_aliases=("A001",),
        max_findings=2,
    )

    assert [row["moment_ids"] for row in findings] == [["M004"], ["M003"]]
    assert all(row["focus_kind"] == "unrepresented_isolated_moment" for row in findings)
    assert all(row["selection_limit"] == 2 for row in findings)


def test_visual_pool_findings_reopen_every_candidate_touched_by_the_cap() -> None:
    candidates = tuple(
        replace(_candidate(index, "M001"), favourite=False) for index in range(1, 5)
    )

    findings = final_cut_contract.runtime_final_pool_findings(
        candidates,
        current_aliases=("A001", "A002"),
        cap_removed_aliases=("A003", "A004"),
    )

    assert findings[0]["focus_kind"] == "moment_cap_projection"
    assert findings[0]["asset_ids"] == ["A001", "A002", "A003", "A004"]
    assert findings[0]["current_asset_ids"] == ["A001", "A002"]
    assert findings[0]["selection_limit"] == 2


def test_focused_visual_pool_can_reopen_a_zero_survivor_moment_without_the_whole_chapter() -> None:
    candidates = (
        replace(_candidate(1, "M001"), episode_id="E001"),
        replace(_candidate(2, "M002"), episode_id="E002", favourite=False),
        replace(_candidate(3, "M002"), episode_id="E002", favourite=False),
        replace(_candidate(4, "M003"), episode_id="E003", favourite=False),
    )
    readings = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001", "M002"],
            "thesis": "The first chapter.",
        },
        {
            "chapter_id": "C002",
            "label": "second",
            "moment_ids": ["M003"],
            "thesis": "The second chapter.",
        },
    )
    focus = (
        {
            "focus_id": "R001",
            "focus_kind": "unrepresented_isolated_moment",
            "moment_ids": ["M002"],
            "asset_ids": ["A002", "A003"],
            "current_asset_ids": [],
            "selection_limit": 2,
            "observation": "One retained moment has no current asset.",
            "review_question": "Does it add a distinct necessary contribution?",
        },
    )

    groups = final_cut_contract.visual_final_pool_groups(
        candidates,
        current_aliases=("A001", "A004"),
        chapter_readings=readings,
        review_focus=focus,
    )

    assert len(groups) == 1
    assert groups[0]["asset_ids"] == ["A002", "A003"]
    assert groups[0]["current_asset_ids"] == []
    assert groups[0]["validation_current_asset_ids"] == ["A001"]
    assert groups[0]["selection_limit"] == 2


def test_zero_survivor_visual_pool_proposal_can_add_without_forcing_a_change() -> None:
    candidates = (
        replace(_candidate(1, "M001"), favourite=False),
        replace(_candidate(2, "M002"), favourite=False),
        replace(_candidate(3, "M002"), favourite=False),
    )
    group = {
        "group_id": "P001",
        "chapter_id": "C001",
        "label": "first",
        "current_asset_ids": [],
        "validation_current_asset_ids": ["A001"],
        "asset_ids": ["A002", "A003"],
        "story_evidence": {"thesis": "A grounded chapter."},
        "focus_kind": "unrepresented_isolated_moment",
        "target_moment_ids": ["M002"],
        "selection_limit": 2,
        "observation": "One retained moment has no current asset.",
        "review_question": "Does it add a distinct necessary contribution?",
    }
    raw = json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "revise",
            "changes": [
                {
                    "add_asset_ids": ["A002"],
                    "remove_asset_ids": [],
                    "visible_gain": "A distinct occasion is now visible.",
                    "displaced_contribution": "Nothing is displaced.",
                }
            ],
            "overall_reason": "The addition earns runtime without padding.",
        }
    )

    proposal = final_cut_contract.read_visual_final_pool_reconsideration(
        raw,
        candidates,
        current_aliases=("A001",),
        group=group,
        required_aliases=(),
        capacity=3,
    )

    assert proposal["keep"] == ["A001", "A002"]
    assert proposal["added"] == ["A002"]


def test_visual_pool_proposal_is_grounded_and_keeps_less_is_more() -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A close posed trail selfie."),
        replace(_candidate(2, "M001"), description="The cabin and valley at the trip base."),
    )
    group = {
        "group_id": "P001",
        "chapter_id": "C001",
        "label": "trip",
        "current_asset_ids": ["A001"],
        "asset_ids": ["A001", "A002"],
        "story_evidence": {"thesis": "A multi-phase trip."},
    }
    prompt = final_cut_contract.visual_final_pool_reconsideration_prompt(
        candidates,
        current_aliases=("A001",),
        group=group,
        tile_mapping=((1, "A001"), (2, "A002")),
        editorial_brief="Make a truthful trip memory.",
        thesis={"thesis": "A year of change."},
        capacity=5,
        required_aliases=(),
    )
    raw = json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "revise",
            "changes": [
                {
                    "add_asset_ids": ["A002"],
                    "remove_asset_ids": [],
                    "visible_gain": "The cabin supplies the missing trip base.",
                    "displaced_contribution": "No removal is needed.",
                }
            ],
            "overall_reason": "One grounded place beat is absent.",
        }
    )
    proposal = final_cut_contract.read_visual_final_pool_reconsideration(
        raw,
        candidates,
        current_aliases=("A001",),
        group=group,
        required_aliases=(),
        capacity=5,
    )

    normalized = " ".join(prompt.lower().split())
    assert "stable is the default verdict" in normalized
    assert "less is more" in normalized
    assert "complete visible pool" in normalized
    assert proposal["keep"] == ["A001", "A002"]
    assert proposal["added"] == ["A002"]
    assert proposal["removed"] == []
    assert proposal["changes"][0]["reason"] == "The cabin supplies the missing trip base."


def test_visual_pool_proposal_discards_one_noop_but_keeps_a_grounded_sibling() -> None:
    candidates = (
        _candidate(1, "M001"),
        replace(_candidate(2, "M001"), description="A missing environmental carrier."),
    )
    group = {
        "group_id": "P001",
        "chapter_id": "C001",
        "label": "trip",
        "current_asset_ids": ["A001"],
        "asset_ids": ["A001", "A002"],
        "story_evidence": {"thesis": "A multi-phase trip."},
    }
    raw = json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "revise",
            "changes": [
                {
                    "add_asset_ids": ["A001"],
                    "remove_asset_ids": [],
                    "visible_gain": "Incorrectly repeats an existing keep.",
                    "displaced_contribution": "None.",
                },
                {
                    "add_asset_ids": ["A002"],
                    "remove_asset_ids": [],
                    "visible_gain": "A002 supplies the missing place beat.",
                    "displaced_contribution": "None.",
                },
            ],
            "overall_reason": "Only the grounded sibling improves the chapter.",
        }
    )

    proposal = final_cut_contract.read_visual_final_pool_reconsideration(
        raw,
        candidates,
        current_aliases=("A001",),
        group=group,
        required_aliases=(),
        capacity=3,
    )

    assert proposal["verdict"] == "revise"
    assert proposal["keep"] == ["A001", "A002"]
    assert proposal["added"] == ["A002"]
    assert proposal["discarded_changes"] == 1
    assert [row["add_asset_ids"] for row in proposal["changes"]] == [["A002"]]


def test_global_visual_pool_validation_accepts_only_grounded_net_gains() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
        _candidate(3, "M002"),
    )
    proposals = (
        {
            "change_id": "C001",
            "chapter": "trip",
            "add_asset_ids": ["A002"],
            "remove_asset_ids": [],
            "reason": "Adds the missing base.",
        },
        {
            "change_id": "C002",
            "chapter": "social",
            "add_asset_ids": ["A003"],
            "remove_asset_ids": ["A001"],
            "reason": "Offers another posed group.",
        },
    )
    prompt = final_cut_contract.visual_final_pool_global_validation_prompt(
        candidates,
        current_aliases=("A001",),
        proposals=proposals,
        tile_mapping=((1, "A001"), (2, "A002"), (3, "A003")),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A year with one special event."},
    )
    raw = json.dumps(
        {
            "schema_version": "visual-final-pool-global-validation-v1",
            "decisions": [
                {
                    "change_id": "C001",
                    "verdict": "accept",
                    "reason": "The base adds a missing place beat.",
                },
                {
                    "change_id": "C002",
                    "verdict": "reject",
                    "reason": "The posed group would weaken current action.",
                },
            ],
            "overall_reason": "Only one delta improves the complete film.",
        }
    )

    validation = final_cut_contract.read_visual_final_pool_global_validation(
        raw,
        candidates,
        current_aliases=("A001",),
        proposals=proposals,
        required_aliases=(),
        capacity=3,
    )

    assert validation["accepted_change_ids"] == ["C001"]
    assert validation["keep"] == ["A001", "A002"]
    assert validation["added"] == ["A002"]
    assert validation["removed"] == []
    normalized = " ".join(prompt.lower().split())
    assert "do not reject an establishing frame merely because" in normalized
    assert "current cut otherwise fails to make" in normalized
    assert "special event" in normalized
    assert "portrait taken at an event does not by itself establish the event" in normalized


def test_visual_pool_filters_only_lower_priority_same_moment_near_duplicate_additions(
    monkeypatch,
) -> None:
    candidates = (
        replace(
            _candidate(1, "M001"),
            media_kind="live-motion",
            motion_contribution="meaningful",
        ),
        replace(_candidate(2, "M001"), favourite=True),
        _candidate(3, "M002"),
    )
    proposals = (
        {
            "change_id": "C001",
            "add_asset_ids": ["A002"],
            "remove_asset_ids": [],
            "reason": "Adds a static reference.",
        },
        {
            "change_id": "C002",
            "add_asset_ids": ["A003"],
            "remove_asset_ids": [],
            "reason": "Adds a different event.",
        },
    )
    monkeypatch.setattr(matrix, "compute_thumbnail_hash", lambda _jpeg: "0" * 16)

    eligible, decisions = matrix._filter_lower_priority_near_duplicate_proposals(
        candidates,
        current_aliases=("A001",),
        proposals=proposals,
        preview_jpeg=lambda _asset_id: b"preview",
    )

    assert [row["change_id"] for row in eligible] == ["C002"]
    assert decisions == [
        {
            "change_id": "C001",
            "verdict": "reject",
            "reason": (
                "A lower-priority same-moment addition is a perceptual near-duplicate of "
                "selected meaningful motion."
            ),
        }
    ]


def test_global_visual_pool_prompt_does_not_repeat_full_evidence_for_untouched_assets() -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 5))
    proposals = (
        {
            "change_id": "C001",
            "chapter": "trip",
            "add_asset_ids": ["A002"],
            "remove_asset_ids": ["A001"],
            "reason": "The setting may replace a repeated portrait.",
        },
    )

    prompt = final_cut_contract.visual_final_pool_global_validation_prompt(
        candidates,
        current_aliases=("A001", "A004"),
        proposals=proposals,
        tile_mapping=((1, "A001"), (2, "A004"), (3, "A002")),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A year with one special event."},
    )

    assert "Visible scene 1" in prompt
    assert "Visible scene 2" in prompt
    assert "Visible scene 4" not in prompt
    assert "tile 1 = A001 | CURRENT" in prompt
    assert "tile 2 = A004 | CURRENT" in prompt
    assert "tile 3 = A002 | PROPOSED ADDITION C001" in prompt
    assert "A proposed-addition tile is the candidate being judged" in prompt
    normalized = " ".join(prompt.lower().split())
    assert "coverage is contribution-specific" in normalized
    assert "people or relationship coverage cannot substitute" in normalized
    assert "another current tile visibly carries the same" in normalized
    assert "A004|M004|" in prompt
    assert "full descriptions are limited to assets directly involved" in normalized


def test_global_visual_pool_validation_repairs_stray_structural_quotes() -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
        _candidate(3, "M002"),
    )
    proposals = (
        {
            "change_id": "C001",
            "add_asset_ids": ["A002"],
            "remove_asset_ids": [],
            "reason": "Adds missing place.",
        },
        {
            "change_id": "C002",
            "add_asset_ids": ["A003"],
            "remove_asset_ids": [],
            "reason": "Adds a repeated pose.",
        },
    )
    raw = """{
"schema_version":"visual-final-pool-global-validation-v1",
"decisions":[
{"change_id":"C001","verdict":"accept","reason":"The place is absent."
"},
{"change_id":"C002","verdict":"reject","reason":"The pose is redundant."
"}
],
"overall_reason":"Only the place improves the complete film."
}"""

    validation = final_cut_contract.read_visual_final_pool_global_validation(
        raw,
        candidates,
        current_aliases=("A001",),
        proposals=proposals,
        required_aliases=(),
        capacity=3,
    )

    assert validation["accepted_change_ids"] == ["C001"]
    assert validation["keep"] == ["A001", "A002"]
    assert validation["repaired_envelope"] is True


def test_visual_pool_runner_searches_locally_then_validates_globally(tmp_path: Path) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
        _candidate(3, "M002"),
    )
    local_raw = json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "revise",
            "changes": [
                {
                    "add_asset_ids": ["A002"],
                    "remove_asset_ids": [],
                    "visible_gain": "A002 supplies the missing setting.",
                    "displaced_contribution": "No removal is needed.",
                }
            ],
            "overall_reason": "One visible setting beat is absent.",
        }
    )
    global_raw = json.dumps(
        {
            "schema_version": "visual-final-pool-global-validation-v1",
            "decisions": [
                {
                    "change_id": "C001",
                    "verdict": "accept",
                    "reason": "The setting is absent from the complete cut.",
                }
            ],
            "overall_reason": "The one addition improves the whole film.",
        }
    )
    requests = []

    class Requester:
        def ask(self, request):
            requests.append(request)
            raw = global_raw if "global-validation" in request.pass_name else local_raw
            return SimpleNamespace(raw_text=raw)

    result = matrix._run_visual_final_pool_reconsideration(
        candidates,
        current_aliases=("A001", "A003"),
        required_aliases=(),
        capacity=4,
        case=matrix.Case(
            key="case",
            label="A memory",
            product="year_in_review",
            ranges=(),
            target_seconds=600.0,
            brief="Make a truthful memory.",
        ),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {
                "chapter_id": "C001",
                "label": "first",
                "moment_ids": ["M001"],
                "thesis": "A chapter with a missing setting.",
            },
            {
                "chapter_id": "C002",
                "label": "second",
                "moment_ids": ["M002"],
                "thesis": "A complete chapter.",
            },
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
        cap_removed_aliases=("A002",),
    )

    assert result is not None
    assert result["verdict"] == "revise"
    assert result["keep"] == ["A001", "A002", "A003"]
    assert result["added"] == ["A002"]
    assert result["removed"] == []
    assert result["accepted_change_ids"] == ["C001"]
    assert result["proposals"] == [
        {
            "change_id": "C001",
            "chapter_id": "C001",
            "chapter_label": "first",
            "add_asset_ids": ["A002"],
            "remove_asset_ids": [],
            "reason": "A002 supplies the missing setting.",
            "visible_gain": "A002 supplies the missing setting.",
            "displaced_contribution": "No removal is needed.",
        }
    ]
    assert result["decisions"] == [
        {
            "change_id": "C001",
            "verdict": "accept",
            "reason": "The setting is absent from the complete cut.",
        }
    ]
    assert [request.pass_name for request in requests] == [
        matrix.VISUAL_FINAL_POOL_PASS_NAME,
        matrix.VISUAL_FINAL_POOL_GLOBAL_PASS_NAME,
    ]
    global_request = requests[-1]
    assert [len(page.tile_refs) for page in global_request.pages] == [1, 1]
    assert tuple(ref.entity_id for ref in global_request.pages[0].tile_refs) == ("A001",)
    assert tuple(ref.entity_id for ref in global_request.pages[1].tile_refs) == ("A002",)
    assert global_request.ordered_input_ids == (
        candidates[0].asset_id,
        candidates[1].asset_id,
    )
    assert "selected assets from every affected chapter" in global_request.prompt


def test_visual_pool_runner_does_not_repeat_a_successfully_reviewed_finding(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M002"),
    )
    stable_raw = json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "stable",
            "changes": [],
            "overall_reason": "The unrepresented moment does not improve the film.",
        }
    )
    requests = []

    class Requester:
        def ask(self, request):
            requests.append(request)
            return SimpleNamespace(raw_text=stable_raw)

    reviewed_focus_keys: set[tuple[str, tuple[str, ...]]] = set()
    arguments = {
        "current_aliases": ("A001",),
        "required_aliases": (),
        "capacity": 4,
        "case": matrix.Case(
            key="case",
            label="A memory",
            product="year_in_review",
            ranges=(),
            target_seconds=600.0,
            brief="Make a truthful memory.",
        ),
        "thesis": {"thesis": "A grounded year."},
        "chapter_readings": (
            {
                "chapter_id": "C001",
                "label": "first",
                "moment_ids": ["M001", "M002"],
                "thesis": "One complete chapter.",
            },
        ),
        "requester": Requester(),
        "output_dir": tmp_path / "visual-pool",
        "preview_jpeg": lambda _asset_id: b"not-a-real-jpeg",
        "timeout_seconds": 30,
        "reviewed_focus_keys": reviewed_focus_keys,
    }

    first = matrix._run_visual_final_pool_reconsideration(
        candidates,
        iteration=1,
        **arguments,
    )
    second = matrix._run_visual_final_pool_reconsideration(
        candidates,
        iteration=2,
        **arguments,
    )

    assert first is not None
    assert first["verdict"] == "stable"
    assert second is None
    assert len(requests) == 1
    assert reviewed_focus_keys == {("unrepresented_isolated_moment", ("M002",))}


def test_iterative_review_rechecks_a_globally_validated_visual_pool_delta(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
    )
    cut = {
        "keep": [{"asset_id": "A001", "reason": "Current selection."}],
        "required_asset_ids": [],
    }
    stable_text = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "The revised corpus is stable.",
        }
    )

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, stable_text, 0.1, False, False)

    visual_calls = []

    def visual_reconsideration(current: tuple[str, ...], iteration: int):
        visual_calls.append((current, iteration))
        if iteration == 1:
            return {
                "verdict": "revise",
                "keep": ["A001", "A002"],
                "added": ["A002"],
                "removed": [],
                "changes": [
                    {
                        "change_id": "C001",
                        "add_asset_ids": ["A002"],
                        "remove_asset_ids": [],
                        "reason": "A002 supplies the missing setting.",
                    }
                ],
                "accepted_change_ids": ["C001"],
                "proposals": [
                    {
                        "change_id": "C001",
                        "add_asset_ids": ["A002"],
                        "remove_asset_ids": [],
                        "reason": "A002 supplies the missing setting.",
                    }
                ],
                "decisions": [
                    {
                        "change_id": "C001",
                        "verdict": "accept",
                        "reason": "The setting improves the whole film.",
                    }
                ],
                "groups": [{"group_id": "P001", "verdict": "revise"}],
                "warnings": ["visual pool group P008 failed: ValueError"],
            }
        return {
            "verdict": "stable",
            "keep": list(current),
            "added": [],
            "removed": [],
            "changes": [],
            "accepted_change_ids": [],
            "groups": [{"group_id": "P001", "verdict": "stable"}],
        }

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=3,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=3,
            visual_reconsideration=visual_reconsideration,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002"]
    assert [row["outcome"] for row in deliberation["iterations"]] == [
        "accepted",
        "unchanged",
    ]
    assert deliberation["stop_reason"] == "stable"
    assert visual_calls == [
        (("A001",), 1),
        (("A001", "A002"), 2),
    ]
    assert deliberation["iterations"][0]["calls"]["visual_pool"]["warnings"] == [
        "visual pool group P008 failed: ValueError"
    ]
    assert (
        deliberation["iterations"][0]["calls"]["visual_pool"]["proposals"][0]["change_id"] == "C001"
    )
    assert deliberation["iterations"][0]["calls"]["visual_pool"]["decisions"][0] == {
        "change_id": "C001",
        "verdict": "accept",
        "reason": "The setting improves the whole film.",
    }


def test_visual_global_validation_does_not_trigger_a_weaker_text_rereview() -> None:
    visual_deliberation = {
        "iterations": [
            {
                "outcome": "accepted",
                "validation": {"source": "visual-pool-global-validation"},
            }
        ]
    }
    text_deliberation = {
        "iterations": [
            {
                "outcome": "accepted",
                "validation": {"source": "description-delta-validation"},
            }
        ]
    }

    assert not matrix._needs_post_deliberation_text_global_review(visual_deliberation)
    assert matrix._needs_post_deliberation_text_global_review(text_deliberation)


def test_iterative_final_review_accepts_one_grounded_delta_then_converges(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), description="A face-forward selfie at a trailhead."),
        replace(_candidate(2, "M001"), description="A similar face-forward trail selfie."),
        replace(_candidate(3, "M001"), description="A ridge view shows route and weather."),
        replace(_candidate(4, "M002"), description="A family meal around a table."),
    )
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "Shows the people at the trailhead."},
            {"asset_id": "A002", "reason": "A second trail portrait."},
            {"asset_id": "A004", "reason": "Distinct family event."},
        ],
        "required_asset_ids": ("A004",),
    }
    revise = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "revise",
            "findings": [
                {
                    "kind": "subject_or_pose_overweight",
                    "asset_ids": ["A001", "A002"],
                    "visible_defect": "Both frames repeat the same face-forward pose.",
                    "missing_contribution": "The hike lacks route and weather, if available.",
                }
            ],
            "overall_reason": "One repeated contribution crosses the threshold.",
        }
    )
    proposal = json.dumps(
        {
            "schema_version": "description-final-asset-reconsideration-v1",
            "changes": [
                {
                    "finding_id": "F001",
                    "add_asset_ids": ["A003"],
                    "remove_asset_ids": ["A002"],
                    "reason": "A003 adds route and weather absent from the second selfie.",
                }
            ],
            "overall_reason": "The swap keeps people and adds the setting.",
        }
    )
    accept = json.dumps(
        {
            "schema_version": "description-final-asset-delta-validation-v1",
            "verdict": "accept",
            "supported_finding_ids": ["F001"],
            "reason": "The swap visibly resolves the cited overweight without losing a beat.",
        }
    )
    stable = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "The revised corpus now has distinct contributions.",
        }
    )
    answers = iter((revise, proposal, accept, stable))
    prompts: list[str] = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=3,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=3,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A003", "A004"]
    assert deliberation["stop_reason"] == "stable"
    assert [row["outcome"] for row in deliberation["iterations"]] == [
        "accepted",
        "unchanged",
    ]
    assert len(prompts) == 4
    assert "A003|M001|" in prompts[1]
    assert "BEFORE SEQUENCE" in prompts[2]
    assert "AFTER SEQUENCE" in prompts[2]


def test_iterative_final_review_keeps_the_prior_corpus_when_delta_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"), _candidate(3, "M003"))
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "Unique lived action."},
            {"asset_id": "A003", "reason": "Distinct relationship beat."},
        ],
        "required_asset_ids": [],
    }
    answers = iter(
        (
            json.dumps(
                {
                    "schema_version": "description-final-asset-audit-v1",
                    "verdict": "revise",
                    "findings": [
                        {
                            "kind": "sequence_gap",
                            "asset_ids": ["A001"],
                            "visible_defect": "The setting is not established.",
                            "missing_contribution": "Place, if the pool contains it.",
                        }
                    ],
                    "overall_reason": "A grounded gap may warrant a pool check.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-reconsideration-v1",
                    "changes": [
                        {
                            "finding_id": "F001",
                            "add_asset_ids": ["A002"],
                            "remove_asset_ids": ["A001"],
                            "reason": "A002 is a different view.",
                        }
                    ],
                    "overall_reason": "A different view may add variety.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-delta-validation-v1",
                    "verdict": "reject",
                    "supported_finding_ids": [],
                    "reason": "Difference alone does not replace the unique lived action.",
                }
            ),
        )
    )
    call_count = 0

    async def ask(prompt, *_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A003"]
    assert deliberation["stop_reason"] == "rejected"
    assert deliberation["iterations"][0]["outcome"] == "rejected"
    assert call_count == 3


def test_final_candidate_carries_compact_episode_and_people_evidence() -> None:
    candidate = FineCutCandidate(
        alias="A001",
        asset_id="private-1",
        moment_id="M001",
        taken_at=START,
        media_kind="photo",
        favourite=False,
        description="Two people cook together.",
        episode_id="E001",
        people_context=("P01:tier=inner;relationship=confirmed-current",),
        motion_contribution="meaningful",
    )

    wall_line = candidate.wall_line()

    assert "episode E001" in wall_line
    assert "people P01:tier=inner;relationship=confirmed-current" in wall_line
    assert "motion meaningful" in wall_line


def test_global_sequence_review_fails_open_without_approving_an_invalid_cut() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v1",
            "keep": ["A002"],
            "cut": [{"asset_id": "A001", "reason": "Drop the required visual."}],
            "overall_reason": "An invalid cut.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002"),
        required_aliases=("A001",),
    )

    assert review["status"] == "unapproved"
    assert review["keep"] == ["A001", "A002"]
    assert review["cut"] == []
    assert "required" in review["warning"]


def test_global_sequence_review_cannot_turn_a_nonempty_proposal_into_an_empty_film() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v1",
            "keep": [],
            "cut": [
                {"asset_id": "A001", "reason": "Too quiet."},
                {"asset_id": "A002", "reason": "Also quiet."},
            ],
            "overall_reason": "Nothing remains.",
        }
    )

    review = apply_final_asset_sequence_review(
        raw,
        candidates,
        proposed_aliases=("A001", "A002"),
    )

    assert review["status"] == "unapproved"
    assert review["keep"] == ["A001", "A002"]
    assert "empty" in review["warning"]


def test_global_sequence_review_reduces_the_assembled_chapter_cut(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 5))
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "Won its chapter."} for candidate in candidates
        ],
        "required_asset_ids": [],
    }
    raw = json.dumps(
        {
            "schema_version": "description-final-sequence-review-v1",
            "keep": ["A001", "A004"],
            "cut": [
                {"asset_id": "A002", "reason": "Repeats the first beat."},
                {"asset_id": "A003", "reason": "Adds no new state."},
            ],
            "overall_reason": "Two beats carry the whole sequence.",
        }
    )

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, raw, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, review, call = asyncio.run(
        matrix._global_final_sequence_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A year of change."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A004"]
    assert review["status"] == "approved"
    assert call.raw == raw


def test_global_sequence_review_transport_failure_keeps_everything_unapproved(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "Won its chapter."} for candidate in candidates
        ],
        "required_asset_ids": [],
    }

    async def ask(*_args, **_kwargs):
        raise TimeoutError("private provider detail must not enter the artifact")

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, review, call = asyncio.run(
        matrix._global_final_sequence_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A year of change."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002"]
    assert review["status"] == "unapproved"
    assert review["cut"] == []
    assert review["warning"] == "global sequence review failed: TimeoutError"
    assert call is None


@pytest.mark.parametrize(
    ("candidate_count", "required"),
    [
        (1, ()),
        (2, ("A001", "A002")),
    ],
)
def test_global_sequence_review_skips_a_call_when_no_optional_decision_exists(
    monkeypatch,
    tmp_path: Path,
    candidate_count: int,
    required: tuple[str, ...],
) -> None:
    candidates = tuple(
        _candidate(index, f"M{index:03d}") for index in range(1, candidate_count + 1)
    )
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "Already settled."} for candidate in candidates
        ],
        "required_asset_ids": list(required),
    }

    async def ask(*_args, **_kwargs):
        raise AssertionError("no-decision reviews must not reach the model")

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, review, call = asyncio.run(
        matrix._global_final_sequence_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A year of change."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
        )
    )

    assert reviewed["keep"] == cut["keep"]
    assert review["status"] == "not-needed"
    assert call is None


def test_hierarchical_capacity_scales_the_prior_editorial_allocation() -> None:
    candidates = tuple(_candidate(index, "M001" if index <= 3 else "M002") for index in range(1, 9))
    text_result = {
        "chapter_readings": [
            {"chapter_id": "C001", "label": "Earlier", "moment_ids": ["M001"]},
            {"chapter_id": "C002", "label": "Later", "moment_ids": ["M002"]},
        ],
        "allocation": {
            "allocations": [
                {"chapter_id": "C001", "slots": 1},
                {"chapter_id": "C002", "slots": 3},
            ]
        },
    }

    plans = matrix._hierarchical_final_cut_plan(
        text_result,
        candidates,
        required_aliases=("A001",),
        capacity=6,
    )

    assert [plan["capacity"] for plan in plans] == [2, 4]
    assert sum(plan["capacity"] for plan in plans) == 6
    assert plans[0]["required_aliases"] == ("A001",)


def test_hierarchical_plan_preserves_local_story_and_moment_admission_evidence() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    text_result = {
        "chapter_readings": [
            {
                "chapter_id": "C001",
                "label": "A festival chapter",
                "moment_ids": ["M001", "M002"],
                "thesis": "A multi-day event progresses from campsite life to live music.",
                "sustained_threads": [
                    {
                        "summary": "The lived event changes across the weekend.",
                        "evidence_moment_ids": ["M001", "M002"],
                    }
                ],
            }
        ],
        "allocation": {
            "allocations": [{"chapter_id": "C001", "slots": 2}],
        },
        "selection": {
            "keep": [
                {"moment_id": "M001", "reason": "The campsite establishes lived texture."},
                {"moment_id": "M002", "reason": "The concert supplies the event payoff."},
            ]
        },
    }

    (plan,) = matrix._hierarchical_final_cut_plan(
        text_result,
        candidates,
        required_aliases=(),
        capacity=2,
    )

    assert plan["story_evidence"] == {
        "chapter_reading": text_result["chapter_readings"][0],
        "admitted_moments": text_result["selection"]["keep"],
    }


def test_hierarchical_asset_cut_reads_the_preserved_local_story_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M002"))
    prompts = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, _answer("A001", rejected="A002"), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    asyncio.run(
        matrix._hierarchical_final_asset_cut(
            (
                {
                    "chapter_id": "C001",
                    "label": "One chapter",
                    "source_slots": 1,
                    "candidates": candidates,
                    "required_aliases": (),
                    "capacity": 1,
                    "story_evidence": {
                        "chapter_reading": {
                            "thesis": "The lived event progresses from setup to payoff."
                        },
                        "admitted_moments": [
                            {
                                "moment_id": "M002",
                                "reason": "This moment carries the visible payoff.",
                            }
                        ],
                    },
                },
            ),
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A condensed global thesis."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert len(prompts) == 1
    assert "LOCAL CHAPTER EVIDENCE" in prompts[0]
    assert "The lived event progresses from setup to payoff." in prompts[0]
    assert "This moment carries the visible payoff." in prompts[0]
    assert "absence from the condensed global thesis is not evidence" in prompts[0]


def test_hierarchical_final_cut_repairs_an_ungrounded_keep(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), alias="A420"),
        replace(_candidate(2, "M001"), alias="A431"),
        replace(_candidate(3, "M002"), alias="A503"),
    )
    answers = iter((_answer("A999", rejected="A002"), _answer("A003", rejected="A002")))

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    cut, calls = asyncio.run(
        matrix._hierarchical_final_asset_cut(
            (
                {
                    "chapter_id": "C001",
                    "label": "One chapter",
                    "source_slots": 1,
                    "candidates": candidates,
                    "required_aliases": (),
                    "capacity": 1,
                },
            ),
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
        )
    )

    assert len(calls) == 2
    assert [row["asset_id"] for row in cut["keep"]] == ["A503"]
    assert cut["chapters"][0]["calls"] == 2
    progress = capsys.readouterr().out
    assert "final-asset-cut: 1/1 chapters complete" in progress
    assert "0 cache hits, 2 actual calls" in progress
    assert "ETA 0s" in progress


def test_hierarchical_final_cut_projects_missing_favourite_without_a_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), alias="A420"),
        replace(_candidate(2, "M001"), alias="A431"),
        replace(_candidate(3, "M002"), alias="A503"),
    )
    prompts = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, _answer("A002"), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    cut, calls = asyncio.run(
        matrix._hierarchical_final_asset_cut(
            (
                {
                    "chapter_id": "C001",
                    "label": "One chapter",
                    "source_slots": 1,
                    "candidates": candidates,
                    "required_aliases": (),
                    "capacity": 1,
                },
            ),
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
        )
    )

    assert len(calls) == 1
    assert [row["asset_id"] for row in cut["keep"]] == ["A420"]
    assert cut["projected_favourite_assets"] == 1
    assert len(prompts) == 1


def test_favourite_projection_replaces_only_inside_the_selected_moment() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A002", rejected="A003"),
        candidates,
        capacity=1,
        project_favourites=True,
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001"]
    assert cut["projected_favourite_assets"] == 1


def test_favourite_projection_yields_to_meaningful_motion_inside_the_moment() -> None:
    candidates = (
        _candidate(1, "M001"),
        replace(
            _candidate(2, "M001"),
            favourite=False,
            media_kind="live-motion",
            motion_contribution="meaningful",
        ),
        replace(_candidate(3, "M002"), favourite=False),
    )

    cut = read_final_asset_cut(
        _answer("A002", rejected="A003"),
        candidates,
        capacity=1,
        project_favourites=True,
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A002"]
    assert cut["projected_favourite_assets"] == 0


def test_required_and_optional_assets_merge_in_chronological_wall_order() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A002"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001", "A002"]


def test_optional_assets_cannot_overfill_the_remaining_capacity() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    with pytest.raises(ValueError, match="exceeds remaining"):
        read_final_asset_cut(
            _answer("A002", "A003"),
            candidates,
            capacity=2,
            required_aliases=("A001",),
        )


def test_required_asset_echo_is_a_recorded_noop() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A001", rejected="A002"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001"]
    assert cut["discarded_required_echoes"] == 1


def test_bad_debug_comparison_does_not_discard_a_valid_cut() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A002", rejected="A001"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001", "A002"]
    assert cut["comparisons"] == []
    assert cut["discarded_comparisons"] == 1


def test_a_shortlisted_favourite_moment_can_be_dropped_but_not_misrepresented() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    dropped = read_final_asset_cut(
        _answer("A003", rejected="A002"),
        candidates,
        capacity=1,
    )
    assert [row["asset_id"] for row in dropped["keep"]] == ["A003"]

    with pytest.raises(ValueError, match="no favourite"):
        read_final_asset_cut(
            _answer("A002", rejected="A003"),
            candidates,
            capacity=1,
        )


def test_moment_audit_allows_no_comparison_only_when_nothing_is_rejected() -> None:
    all_kept = json.dumps(
        {
            "schema_version": prototype.SELECTION_SCHEMA,
            "keep": [
                {"moment_id": "M001", "reason": "Distinct lived scene."},
                {"moment_id": "M002", "reason": "Different lived scene."},
            ],
            "audit_summary": "Both moments earn runtime.",
            "comparisons": [],
            "overall_reason": "The small complete wall is coherent.",
        }
    )

    assert len(prototype._read_selection(all_kept, frozenset({"M001", "M002"}), 13)["keep"]) == 2

    rejected_without_comparison = json.loads(all_kept)
    rejected_without_comparison["keep"].pop()
    with pytest.raises(ValueError, match="audit"):
        prototype._read_selection(
            json.dumps(rejected_without_comparison),
            frozenset({"M001", "M002"}),
            13,
        )


def _day_occasion(days: int, *, per_day: int = 2) -> tuple[FineCutCandidate, ...]:
    return tuple(
        replace(
            _candidate(day * per_day + slot, f"M{day:03d}"),
            taken_at=START + timedelta(days=day, hours=slot),
        )
        for day in range(1, days + 1)
        for slot in range(per_day)
    )


def test_pool_findings_report_a_multi_day_occasion_with_half_its_days_dark() -> None:
    # The camp class: one continuous occasion whose reservoir spans many days
    # while the cut lands on a couple of them.
    candidates = _day_occasion(10)
    half = final_cut_contract.runtime_final_pool_findings(
        candidates,
        current_aliases=("A002", "A004", "A006", "A008", "A010"),
    )
    covered = final_cut_contract.runtime_final_pool_findings(
        candidates,
        current_aliases=("A002", "A004", "A006", "A008", "A010", "A012"),
    )

    coverage = [row for row in half if row["focus_kind"] == "occasion_day_coverage"]
    assert len(coverage) == 1
    assert coverage[0]["owner_evidence"]["photographed_days"] == 10
    assert coverage[0]["owner_evidence"]["represented_days"] == 5
    assert coverage[0]["owner_evidence"]["unrepresented_days"] == [
        "2025-01-07",
        "2025-01-08",
        "2025-01-09",
        "2025-01-10",
        "2025-01-11",
    ]
    assert coverage[0]["moment_ids"] == ["M006", "M007", "M008", "M009", "M010"]
    assert coverage[0]["asset_ids"] == [f"A{index:03d}" for index in range(12, 22)]
    assert [row["focus_kind"] for row in covered].count("occasion_day_coverage") == 0

    # The day is the unit this finding reasons in, so every review row must carry it.
    wall = final_cut_contract.compact_reservoir_wall(candidates).splitlines()
    assert all(
        candidate.taken_at.date().isoformat() in line
        for line, candidate in zip(wall, candidates, strict=True)
    )


def _person_row(token: str) -> str:
    return f"{token}:tier=inner;relationship=confirmed;source=owner"


def _person_wall() -> tuple[FineCutCandidate, ...]:
    # P01 is on the wall twice, P02 is photographed eight times and never kept,
    # P03 sits below the recurrence floor so its absence is not a finding.
    plan = (("M001", "P01", 2), ("M002", "P02", 5), ("M003", "P02", 3), ("M004", "P03", 3))
    rows: list[FineCutCandidate] = []
    for moment_id, token, count in plan:
        for _ in range(count):
            rows.append(
                replace(
                    _candidate(len(rows) + 1, moment_id),
                    favourite=False,
                    people_context=(_person_row(token),),
                )
            )
    return tuple(rows)


def _dark_wall() -> tuple[FineCutCandidate, ...]:
    return (
        replace(_candidate(1, "M001"), luminance=30, favourite=False),
        replace(_candidate(2, "M001"), luminance=90, favourite=False),
        replace(_candidate(3, "M002"), luminance=40, favourite=False),
        replace(_candidate(4, "M002"), luminance=45, favourite=False),
        replace(_candidate(5, "M003"), luminance=100, favourite=False),
        replace(_candidate(6, "M003"), luminance=220, favourite=False),
    )


def test_pool_findings_name_a_recurring_person_the_cut_never_shows() -> None:
    wall = _person_wall()

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A002"),
    )
    unbalanced = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001",),
    )

    coverage = [row for row in findings if row["focus_kind"] == "person_coverage"]
    assert len(coverage) == 1
    assert coverage[0]["owner_evidence"]["person"] == "P02"
    assert coverage[0]["owner_evidence"]["reservoir_assets"] == 8
    assert coverage[0]["owner_evidence"]["busiest_person_wall_assets"] == 2
    assert coverage[0]["moment_ids"] == ["M002"]
    assert coverage[0]["asset_ids"] == ["A003", "A004", "A005", "A006", "A007"]
    assert coverage[0]["current_asset_ids"] == []
    # P03's three reservoir assets are one occasion, not a person the cut is missing.
    assert "P03" not in json.dumps(findings)
    # With one wall tile nobody has been visibly chosen, so nobody is compared to them.
    assert [row["focus_kind"] for row in unbalanced].count("person_coverage") == 0


def test_pool_findings_offer_a_brighter_sibling_for_a_kept_near_black_frame() -> None:
    wall = _dark_wall()

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A003", "A005"),
    )

    assert [row["focus_kind"] for row in findings] == ["dark_frame"]
    assert findings[0]["moment_ids"] == ["M001"]
    assert findings[0]["asset_ids"] == ["A001", "A002"]
    assert findings[0]["current_asset_ids"] == ["A001"]
    assert findings[0]["owner_evidence"]["dark_asset_ids"] == ["A001"]
    assert findings[0]["owner_evidence"]["dark_luminance"] == [30]
    assert findings[0]["owner_evidence"]["brightest_sibling_luminance"] == 90


def test_person_and_dark_frame_findings_pass_the_visual_pool_grounding() -> None:
    people = _person_wall()
    people_reading = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001", "M002", "M003", "M004"],
            "thesis": "The whole chapter.",
        },
    )
    dark = _dark_wall()
    dark_reading = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001", "M002", "M003"],
            "thesis": "The whole chapter.",
        },
    )

    person_groups = final_cut_contract.visual_final_pool_groups(
        people,
        current_aliases=("A001", "A002"),
        chapter_readings=people_reading,
        review_focus=final_cut_contract.runtime_final_pool_findings(
            people,
            current_aliases=("A001", "A002"),
            chapter_readings=people_reading,
        ),
    )
    dark_groups = final_cut_contract.visual_final_pool_groups(
        dark,
        current_aliases=("A001", "A003", "A005"),
        chapter_readings=dark_reading,
        review_focus=final_cut_contract.runtime_final_pool_findings(
            dark,
            current_aliases=("A001", "A003", "A005"),
            chapter_readings=dark_reading,
        ),
    )

    person = [group for group in person_groups if group["focus_kind"] == "person_coverage"]
    assert len(person) == 1
    assert person[0]["target_moment_ids"] == ["M002"]
    assert person[0]["asset_ids"] == ["A003", "A004", "A005", "A006", "A007"]
    assert person[0]["validation_current_asset_ids"] == ["A001", "A002"]
    assert [group["focus_kind"] for group in dark_groups] == ["dark_frame"]
    assert [group["asset_ids"] for group in dark_groups] == [["A001", "A002"]]
    assert [group["current_asset_ids"] for group in dark_groups] == [["A001"]]


def _document_wall() -> tuple[FineCutCandidate, ...]:
    assert "newspaper" in DOCUMENT_ARTIFACT_WORDS
    return (
        replace(
            _candidate(1, "M001"),
            favourite=False,
            description="A newspaper front page held up to the camera",
        ),
        replace(_candidate(2, "M002"), favourite=False),
        replace(_candidate(3, "M002"), favourite=False),
    )


def test_pool_findings_challenge_a_kept_document_in_a_single_asset_moment() -> None:
    wall = _document_wall()

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A002"),
    )

    documents = [row for row in findings if row["focus_kind"] == "document_artifact"]
    assert len(documents) == 1
    assert documents[0]["moment_ids"] == ["M001"]
    assert documents[0]["asset_ids"] == ["A001"]
    assert documents[0]["current_asset_ids"] == ["A001"]


def test_pool_findings_never_challenge_a_document_with_a_lived_sibling_in_its_moment() -> None:
    wall = (
        replace(
            _candidate(1, "M001"),
            favourite=False,
            description="A newspaper front page held up to the camera",
        ),
        replace(_candidate(2, "M001"), favourite=False),
    )

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A002"),
    )

    assert [row for row in findings if row["focus_kind"] == "document_artifact"] == []


def test_document_artifact_finding_passes_the_visual_pool_grounding() -> None:
    wall = _document_wall()
    reading = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001", "M002"],
            "thesis": "The whole chapter.",
        },
    )

    groups = final_cut_contract.visual_final_pool_groups(
        wall,
        current_aliases=("A001", "A002"),
        chapter_readings=reading,
        review_focus=final_cut_contract.runtime_final_pool_findings(
            wall,
            current_aliases=("A001", "A002"),
            chapter_readings=reading,
        ),
    )

    documents = [group for group in groups if group["focus_kind"] == "document_artifact"]
    assert len(documents) == 1
    assert documents[0]["asset_ids"] == ["A001"]
    assert documents[0]["current_asset_ids"] == ["A001"]


def test_pool_findings_never_challenge_a_kept_single_asset_lived_scene() -> None:
    wall = (
        replace(_candidate(1, "M001"), favourite=False),
        replace(_candidate(2, "M002"), favourite=False),
    )

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001",),
    )

    assert [row for row in findings if row["focus_kind"] == "document_artifact"] == []


def _people_only_occasion() -> tuple[FineCutCandidate, ...]:
    # One occasion: people-dense moments the cut keeps, plus a people-free moment of the
    # same days the moment cut rejected. A rejected moment never opens a reservoir, so its
    # rows carry the placeholder description rather than a described frame.
    faces = tuple(
        replace(
            _candidate(index, moment),
            favourite=False,
            people_context=(_person_row("P01"),),
            description=description,
        )
        for index, moment, description in (
            (1, "M001", "Two friends laughing across a table"),
            (2, "M001", "The same pair raising their cups indoors"),
            (3, "M002", "A child hugging a parent while they wait in a queue"),
        )
    )
    place = tuple(
        replace(
            _candidate(index, "M003"),
            favourite=index == 5,
            description="[visual description unavailable]",
            proposed_from_rejected=True,
        )
        for index in (4, 5)
    )
    return (*faces, *place)


# The primary fixture is season-neutral on purpose: the same structural signal has to fire
# for an open coastline as for a snow-covered hillside, so neither can be what carries it.
_COASTLINE_CARD = {
    "moment_id": "M003",
    "summary": "An empty coastline under mid-day sun, the horizon flat behind the water.",
    "people": "insufficient evidence",
    "reason": "The other moments carry the day's relationships more directly.",
    "asset_ids": ["A004", "A005"],
}
_HILLSIDE_CARD = {
    **_COASTLINE_CARD,
    "summary": "A snow-covered hillside under grey cloud, seen from above.",
}
_UNHEDGED_CARD = {
    "moment_id": "M003",
    "summary": "A coastline with the whole group lined up along the horizon.",
    "people": "the group of four",
    "asset_ids": ["A004", "A005"],
}


@pytest.mark.parametrize(
    ("card", "words"),
    [
        (_COASTLINE_CARD, ["coastline", "horizon"]),
        (_HILLSIDE_CARD, ["hillside", "snow-covered"]),
    ],
)
def test_pool_findings_propose_a_rejected_people_free_place_for_a_faces_only_occasion(
    card: dict[str, Any],
    words: list[str],
) -> None:
    wall = _people_only_occasion()

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A002", "A003"),
        rejected_moments=(card,),
    )

    assert [row["focus_kind"] for row in findings] == ["place_without_landscape"]
    assert findings[0]["moment_ids"] == ["M003"]
    assert findings[0]["asset_ids"] == ["A004", "A005"]
    assert findings[0]["current_asset_ids"] == []
    assert findings[0]["owner_evidence"]["proposed_asset_id"] == "A005"
    assert findings[0]["owner_evidence"]["people_dense_wall_assets"] == 3
    assert findings[0]["owner_evidence"]["people_free_signal"] == "hedged-card-people"
    assert findings[0]["owner_evidence"]["corroborating_outdoor_words"] == words


def test_a_place_finding_can_only_name_rows_marked_as_unkept_proposals() -> None:
    # The marker is what tells every wall the row is a proposal from a moment the cut
    # dropped, so a finding may not reach a row that was never built as one.
    wall = _people_only_occasion()
    unmarked = (*wall[:3], *(replace(row, proposed_from_rejected=False) for row in wall[3:]))

    with pytest.raises(ValueError, match="rejected moment rows are not grounded"):
        final_cut_contract.runtime_final_pool_findings(
            unmarked,
            current_aliases=("A001", "A002", "A003"),
            rejected_moments=(_COASTLINE_CARD,),
        )


def test_an_unkept_proposal_row_says_so_on_every_wall_it_reaches() -> None:
    row = replace(_candidate(4, "M003"), proposed_from_rejected=True)

    assert "proposed-from-unkept-moment" in row.wall_line()
    assert "proposed-from-unkept-moment" in final_cut_contract.compact_reservoir_wall((row,))


def test_a_rejected_card_that_names_people_is_never_a_place_finding() -> None:
    # The words alone must decide nothing: this card carries two of them.
    wall = _people_only_occasion()

    findings = final_cut_contract.runtime_final_pool_findings(
        wall,
        current_aliases=("A001", "A002", "A003"),
        rejected_moments=(_UNHEDGED_CARD,),
    )

    assert findings == ()


def test_an_occasion_that_already_shows_its_place_gets_no_place_finding() -> None:
    wall = _people_only_occasion()
    with_place = (
        *wall[:2],
        replace(wall[2], description="The queue below a mountain ridge in full sun"),
        *wall[3:],
    )

    findings = final_cut_contract.runtime_final_pool_findings(
        with_place,
        current_aliases=("A001", "A002", "A003"),
        rejected_moments=(_COASTLINE_CARD,),
    )

    assert findings == ()


def test_the_visual_arm_renders_the_unkept_place_tiles_beside_the_kept_context(
    tmp_path: Path,
) -> None:
    wall = _people_only_occasion()
    film, offered = wall[:3], wall[3:]
    requests: list[Any] = []

    class Requester:
        def ask(self, request):
            requests.append(request)
            return SimpleNamespace(
                raw_text=json.dumps(
                    {
                        "schema_version": final_cut_contract
                        .FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA,
                        "verdict": "stable",
                        "changes": [],
                        "overall_reason": "The current tiles already carry the chapter.",
                    }
                )
            )

    result = matrix._run_visual_final_pool_reconsideration(
        film,
        current_aliases=("A001", "A002", "A003"),
        required_aliases=(),
        capacity=6,
        case=_adoption_case(),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {
                "chapter_id": "C001",
                "label": "the first days",
                "moment_ids": ["M001", "M002", "M003"],
                "thesis": "Two friends and the place they were in.",
            },
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
        adoptable=offered,
        rejected_moments=(_COASTLINE_CARD,),
    )

    assert result is not None
    assert [group["focus_kind"] for group in result["groups"]] == ["place_without_landscape"]
    assert len(requests) == 1
    assert list(requests[0].ordered_input_ids) == ["private-4", "private-5"]
    assert "proposed-from-unkept-moment" in requests[0].prompt
    # The kept context of the same chapter is what the model compares them against.
    assert "A001" in requests[0].prompt


def _adoption_case() -> Any:
    return matrix.Case(
        key="case",
        label="A memory",
        product="year_in_review",
        ranges=(),
        target_seconds=600.0,
        brief="Make a truthful memory.",
    )


def test_an_adopted_unkept_proposal_survives_the_merge_into_the_final_keep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The visual arm may adopt a row that was never in the wall the text cut worked from.
    wall = _people_only_occasion()
    film, offered = wall[:3], wall[3:]
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The cut chose this beat."}
            for candidate in film
        ],
        "required_asset_ids": [],
    }
    text_prompts: list[str] = []

    async def ask(prompt, *_args, **_kwargs):
        text_prompts.append(prompt)
        return matrix.TextCall(
            prompt,
            json.dumps(
                {
                    "schema_version": final_cut_contract.FINAL_ASSET_AUDIT_SCHEMA,
                    "verdict": "stable",
                    "findings": [],
                    "overall_reason": "The film reads without repetition.",
                }
            ),
            0.1,
            False,
            False,
        )

    def visual_reconsideration(current: tuple[str, ...], iteration: int):
        if iteration > 1:
            return None
        return {
            "verdict": "revise",
            "keep": [*current, "A005"],
            "added": ["A005"],
            "removed": [],
            "changes": [
                {
                    "add_asset_ids": ["A005"],
                    "remove_asset_ids": [],
                    "reason": "The occasion is shown only as faces; this carries its place.",
                }
            ],
            "proposals": [],
            "decisions": [],
            "accepted_change_ids": ["C001"],
            "groups": [],
            "warnings": [],
            "failed_focus": [],
        }

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            film,
            cut,
            case=_adoption_case(),
            thesis={"thesis": "A grounded year."},
            capacity=6,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=2,
            visual_reconsideration=visual_reconsideration,
            adoptable=offered,
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002", "A003", "A005"]
    assert deliberation["iterations"][0]["outcome"] == "accepted"
    reasons = {row["asset_id"]: row["reason"] for row in reviewed["keep"]}
    assert "place" in reasons["A005"]
    # Once adopted the row is part of the film, so the text arm can read it -- and it
    # still says on every wall that it came from a moment the cut dropped.
    wall_lines = [
        line for line in text_prompts[-1].splitlines() if line.startswith("A005 ")
    ]
    assert len(wall_lines) == 1
    assert "proposed-from-unkept-moment" in wall_lines[0]


def test_landscape_findings_alone_leave_the_text_arm_wall_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wall = _people_only_occasion()
    film, offered = wall[:3], wall[3:]
    cut = {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The cut chose this beat."}
            for candidate in film
        ],
        "required_asset_ids": [],
    }
    prompts: list[str] = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(
            prompt,
            json.dumps(
                {
                    "schema_version": final_cut_contract.FINAL_ASSET_AUDIT_SCHEMA,
                    "verdict": "stable",
                    "findings": [],
                    "overall_reason": "The film reads without repetition.",
                }
            ),
            0.1,
            False,
            False,
        )

    monkeypatch.setattr(matrix, "_ask_text", ask)

    def run(**kwargs: Any) -> list[str]:
        prompts.clear()
        asyncio.run(
            matrix._iterative_final_asset_review(
                film,
                cut,
                case=_adoption_case(),
                thesis={"thesis": "A grounded year."},
                capacity=6,
                llm_config=SimpleNamespace(),
                cache_path=tmp_path / "cache.db",
                timeout_seconds=30,
                max_iterations=1,
                **kwargs,
            )
        )
        return list(prompts)

    assert run() == run(adoptable=offered, visual_reconsideration=lambda *_args: None)


def test_the_place_finding_passes_the_visual_pool_grounding() -> None:
    wall = _people_only_occasion()
    reading = (
        {
            "chapter_id": "C001",
            "label": "the first days",
            "moment_ids": ["M001", "M002", "M003"],
            "thesis": "Two friends and the place they were in.",
        },
    )

    groups = final_cut_contract.visual_final_pool_groups(
        wall,
        current_aliases=("A001", "A002", "A003"),
        chapter_readings=reading,
        review_focus=final_cut_contract.runtime_final_pool_findings(
            wall,
            current_aliases=("A001", "A002", "A003"),
            chapter_readings=reading,
            rejected_moments=(_COASTLINE_CARD,),
        ),
    )

    assert [group["focus_kind"] for group in groups] == ["place_without_landscape"]
    assert groups[0]["target_moment_ids"] == ["M003"]
    assert groups[0]["asset_ids"] == ["A004", "A005"]
    assert groups[0]["current_asset_ids"] == []
    assert groups[0]["validation_current_asset_ids"] == ["A001", "A002", "A003"]


def test_wall_rows_carry_mean_luminance_beside_the_timestamp() -> None:
    dark = replace(_candidate(1, "M001"), luminance=54)
    unknown = _candidate(2, "M001")

    rows = [dark.wall_line(), unknown.wall_line()]
    compact = final_cut_contract.compact_reservoir_wall((dark, unknown)).splitlines()
    global_wall = final_cut_contract.compact_visual_global_wall(
        (dark, unknown), detailed_aliases=set()
    ).splitlines()

    assert f"{dark.taken_at.isoformat()} lum=54" in rows[0]
    assert f"{dark.taken_at.isoformat()} lum=54" in compact[0]
    assert f"{dark.taken_at.isoformat()} lum=54" in global_wall[0]
    assert "lum=" not in rows[1]
    assert "lum=" not in compact[1]
    assert "lum=" not in global_wall[1]


def test_a_luminance_datum_grows_every_wall_row_by_at_most_eight_characters() -> None:
    plain = _candidate(1, "M001")
    brightest = replace(plain, luminance=255)

    growth = (
        len(brightest.wall_line()) - len(plain.wall_line()),
        len(final_cut_contract.compact_reservoir_wall((brightest,)))
        - len(final_cut_contract.compact_reservoir_wall((plain,))),
        len(final_cut_contract.compact_visual_global_wall((brightest,), detailed_aliases=set()))
        - len(final_cut_contract.compact_visual_global_wall((plain,), detailed_aliases=set())),
    )

    assert growth == (8, 8, 8)
    assert matrix.EDITORIAL_WALL_MAX_CHARS == 90_000


def _grey_jpeg(value: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (16, 16), (value, value, value)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_wall_luminance_is_decoded_once_per_asset_and_tolerates_a_missing_preview() -> None:
    matrix._PREVIEW_LUMINANCE_CACHE.clear()
    wall = (
        _candidate(1, "M001"),
        _candidate(2, "M001"),
        _candidate(3, "M002"),
    )
    previews = {wall[0].asset_id: _grey_jpeg(54), wall[1].asset_id: b"not-a-jpeg"}
    reads: list[str] = []

    def preview_jpeg(asset_id: str) -> bytes | None:
        reads.append(asset_id)
        return previews.get(asset_id)

    measured = matrix._with_preview_luminance(wall, preview_jpeg=preview_jpeg)
    again = matrix._with_preview_luminance(wall, preview_jpeg=preview_jpeg)

    assert [candidate.luminance for candidate in measured] == [54, None, None]
    assert [candidate.luminance for candidate in again] == [54, None, None]
    assert reads == [candidate.asset_id for candidate in wall]


def test_only_the_last_kept_row_is_marked_as_closing_the_memory() -> None:
    wall = tuple(_candidate(index, f"M{index:03d}") for index in range(1, 5))

    marked = final_cut_contract.mark_closing_candidate(wall, kept_aliases=("A001", "A003"))

    rows = [candidate.wall_line() for candidate in marked]
    compact = final_cut_contract.compact_reservoir_wall(marked).splitlines()
    assert [index for index, row in enumerate(rows) if "closes-memory" in row] == [2]
    assert [index for index, row in enumerate(compact) if "closes-memory" in row] == [2]
    assert [candidate.alias for candidate in marked] == ["A001", "A002", "A003", "A004"]


def _closer_moment_wall() -> tuple[FineCutCandidate, ...]:
    return (
        replace(_candidate(1, "M001"), luminance=120, favourite=False),
        replace(_candidate(2, "M002"), luminance=40, favourite=False),
        replace(_candidate(3, "M002"), luminance=90, favourite=True),
        replace(_candidate(4, "M002"), luminance=200, favourite=False),
    )


def _closer_cut(*aliases: str) -> dict[str, Any]:
    return {
        "keep": [{"asset_id": alias, "reason": "The model chose this beat."} for alias in aliases],
        "required_asset_ids": [],
    }


def test_a_dark_closing_pick_is_swapped_for_the_favourite_of_its_own_reservoir() -> None:
    wall = _closer_moment_wall()

    swapped = final_cut_contract.apply_closer_luminance_swap(wall, _closer_cut("A001", "A002"))

    assert [row["asset_id"] for row in swapped["keep"]] == ["A001", "A003"]
    assert swapped["closer_swap"]["moment_id"] == "M002"
    assert swapped["closer_swap"]["before"] == {"asset_id": "A002", "luminance": 40}
    assert swapped["closer_swap"]["after"] == {"asset_id": "A003", "luminance": 90}
    assert "favourite" in swapped["closer_swap"]["reason"]


def test_a_closing_pick_at_or_above_its_reservoir_median_is_left_alone() -> None:
    wall = _closer_moment_wall()

    swapped = final_cut_contract.apply_closer_luminance_swap(wall, _closer_cut("A001", "A003"))

    assert [row["asset_id"] for row in swapped["keep"]] == ["A001", "A003"]
    assert swapped["closer_swap"] is None


def _day_cut(*candidates: FineCutCandidate) -> dict[str, Any]:
    return {
        "keep": [
            {"asset_id": candidate.alias, "reason": "The model chose this beat."}
            for candidate in candidates
        ],
        "required_asset_ids": [],
        "comparisons": [],
    }


def test_a_day_over_the_asset_ceiling_sheds_its_dimmest_non_favourite() -> None:
    # The party class: two moments of two assets on one evening, legal under every cap.
    wall = (
        replace(_candidate(1, "M001"), favourite=False, luminance=120),
        replace(_candidate(2, "M001"), favourite=False, luminance=40),
        replace(_candidate(3, "M002"), favourite=False, luminance=200),
        replace(_candidate(4, "M002"), favourite=False, luminance=90),
        replace(
            _candidate(5, "M003"),
            favourite=False,
            luminance=150,
            taken_at=START + timedelta(days=2),
        ),
    )

    held = final_cut_contract.apply_final_day_ceiling(wall, _day_cut(*wall))

    assert [row["asset_id"] for row in held["keep"]] == ["A001", "A003", "A004", "A005"]
    assert held["day_ceiling"]["max_per_day"] == 3
    assert held["day_ceiling"]["overfull_days"] == 1
    assert held["day_ceiling"]["removed_asset_ids"] == ["A002"]
    assert held["day_ceiling"]["removed"][0]["day"] == "2025-01-01"
    assert "ceiling" in held["day_ceiling"]["removed"][0]["reason"]
    assert held["day_ceiling"]["favourite_overflow_days"] == []


def test_the_day_ceiling_never_drops_a_favourite_but_counts_its_slot() -> None:
    wall = (
        replace(_candidate(1, "M001"), favourite=True, luminance=30),
        replace(_candidate(2, "M001"), favourite=True, luminance=35),
        replace(_candidate(3, "M002"), favourite=False, luminance=200),
        replace(_candidate(4, "M002"), favourite=False, luminance=90),
    )

    held = final_cut_contract.apply_final_day_ceiling(wall, _day_cut(*wall))

    assert [row["asset_id"] for row in held["keep"]] == ["A001", "A002", "A003"]
    assert held["day_ceiling"]["removed_asset_ids"] == ["A004"]


def test_the_closer_is_re_marked_when_the_day_ceiling_drops_it() -> None:
    wall = final_cut_contract.mark_closing_candidate(
        (
            replace(_candidate(1, "M001"), favourite=False, luminance=150),
            replace(_candidate(2, "M001"), favourite=False, luminance=140),
            replace(_candidate(3, "M002"), favourite=False, luminance=130),
            replace(_candidate(4, "M002"), favourite=False, luminance=20),
        ),
        kept_aliases=("A001", "A002", "A003", "A004"),
    )
    assert [row.alias for row in wall if row.closes_memory] == ["A004"]

    held = final_cut_contract.apply_final_day_ceiling(wall, _day_cut(*wall))
    kept = tuple(row["asset_id"] for row in held["keep"])
    remarked = final_cut_contract.mark_closing_candidate(wall, kept_aliases=kept)

    assert kept == ("A001", "A002", "A003")
    assert [row.alias for row in remarked if row.closes_memory] == ["A003"]


def test_the_day_ceiling_runs_before_the_closer_swap_that_reads_the_last_row() -> None:
    wall = (
        replace(_candidate(1, "M001"), favourite=False, luminance=150),
        replace(_candidate(2, "M001"), favourite=False, luminance=140),
        replace(_candidate(3, "M002"), favourite=False, luminance=40),
        replace(_candidate(4, "M002"), favourite=False, luminance=20),
        replace(_candidate(5, "M002"), favourite=False, luminance=200),
        replace(_candidate(6, "M002"), favourite=False, luminance=210),
    )
    cut = _day_cut(*wall[:4])

    capped = final_cut_contract.apply_final_moment_cap(wall, cut, max_per_moment=2)
    held = final_cut_contract.apply_final_day_ceiling(wall, capped)
    swapped = final_cut_contract.apply_closer_luminance_swap(wall, held)
    kept = tuple(row["asset_id"] for row in swapped["keep"])
    marked = final_cut_contract.mark_closing_candidate(wall, kept_aliases=kept)

    # The ceiling sheds A004, so the swap reads A003 as the closing pick, not A004.
    assert held["day_ceiling"]["removed_asset_ids"] == ["A004"]
    assert swapped["closer_swap"]["before"]["asset_id"] == "A003"
    assert kept == ("A001", "A002", "A006")
    assert [row.alias for row in marked if row.closes_memory] == ["A006"]


def _floor_wall() -> tuple[FineCutCandidate, ...]:
    # Three moments the chapter cut kept, on one day. The trim stack left only M001 standing.
    return (
        replace(_candidate(1, "M001"), favourite=False, luminance=120),
        replace(_candidate(2, "M002"), favourite=False, luminance=40),
        replace(_candidate(3, "M002"), favourite=False, luminance=180),
        replace(_candidate(4, "M003"), favourite=True, luminance=50),
        replace(_candidate(5, "M003"), favourite=False, luminance=200),
    )


def _floor_cut(*cut_rows: tuple[str, str]) -> dict[str, Any]:
    return {
        "keep": [{"asset_id": "A001", "reason": "The model chose this beat."}],
        "required_asset_ids": [],
        "initial_global_review": {
            "status": "approved",
            "keep": ["A001"],
            "cut": [{"asset_id": alias, "reason": reason} for alias, reason in cut_rows],
        },
    }


def test_every_moment_the_chapter_cut_kept_lands_an_asset_on_the_final_wall() -> None:
    floored = final_cut_contract.apply_kept_moment_floor(
        _floor_wall(),
        _floor_cut(("A003", "Redundant with A001."), ("A004", "A minor texture beat.")),
        kept_moment_ids=("M001", "M002", "M003"),
    )

    assert [row["asset_id"] for row in floored["keep"]] == ["A001", "A003", "A004"]
    assert [row["moment_id"] for row in floored["moment_floor"]["restored"]] == ["M002", "M003"]
    assert floored["moment_floor"]["waived"] == []


def test_the_floor_prefers_a_star_then_the_cut_s_own_pick_then_the_brightest_frame() -> None:
    floored = final_cut_contract.apply_kept_moment_floor(
        _floor_wall(),
        _floor_cut(("A003", "Redundant with A001.")),
        kept_moment_ids=("M001", "M002", "M003"),
    )
    basis = {
        row["moment_id"]: (row["asset_id"], row["basis"])
        for row in floored["moment_floor"]["restored"]
    }

    # M002 lost the frame the asset cut had chosen; M003 was never in the cut, so its star wins.
    assert basis["M002"] == ("A003", "original-pick")
    assert basis["M003"] == ("A004", "favourite")


def test_the_floor_falls_back_to_the_brightest_frame_of_a_moment_no_pass_ever_chose() -> None:
    wall = tuple(replace(row, favourite=False) for row in _floor_wall())

    floored = final_cut_contract.apply_kept_moment_floor(
        wall,
        _floor_cut(),
        kept_moment_ids=("M001", "M002", "M003"),
    )
    basis = {
        row["moment_id"]: (row["asset_id"], row["basis"])
        for row in floored["moment_floor"]["restored"]
    }

    assert basis["M002"] == ("A003", "brightest")
    assert basis["M003"] == ("A005", "brightest")


def test_the_floor_names_the_trim_pass_that_erased_each_moment_it_restores() -> None:
    wall = _floor_wall()
    cut = _floor_cut(("A004", "A minor texture beat."))
    cut["day_ceiling"] = {"max_per_day": 3, "removed_asset_ids": ["A003"], "removed": []}

    floored = final_cut_contract.apply_kept_moment_floor(
        wall,
        cut,
        kept_moment_ids=("M001", "M002", "M003"),
    )
    erased = {row["moment_id"]: row["erased_by"] for row in floored["moment_floor"]["restored"]}

    assert erased == {"M002": "day_ceiling", "M003": "initial_global_review"}


def test_a_moment_whose_every_asset_a_correctness_pass_removed_stays_erased() -> None:
    floored = final_cut_contract.apply_kept_moment_floor(
        _floor_wall(),
        _floor_cut(("A003", "Redundant with A001.")),
        kept_moment_ids=("M001", "M002", "M003"),
        waived_aliases=("A004", "A005"),
    )

    assert [row["asset_id"] for row in floored["keep"]] == ["A001", "A003"]
    assert [row["moment_id"] for row in floored["moment_floor"]["restored"]] == ["M002"]
    assert floored["moment_floor"]["waived"][0]["moment_id"] == "M003"
    assert floored["moment_floor"]["waived"][0]["asset_ids"] == ["A004", "A005"]


def test_the_day_ceiling_yields_to_the_floor_for_one_asset_per_erased_moment() -> None:
    wall = (*_floor_wall(), replace(_candidate(6, "M001"), favourite=False, luminance=110))
    cut = _floor_cut(("A003", "Redundant with A001."), ("A004", "A minor texture beat."))
    cut["keep"].append({"asset_id": "A006", "reason": "The model chose this beat too."})

    floored = final_cut_contract.apply_kept_moment_floor(
        wall,
        cut,
        kept_moment_ids=("M001", "M002", "M003"),
    )
    yielded = floored["moment_floor"]["ceiling_yielded"]

    assert [row["asset_id"] for row in floored["keep"]] == ["A001", "A003", "A004", "A006"]
    assert [(row["moment_id"], row["held"]) for row in yielded] == [("M002", 4), ("M003", 4)]
    assert {row["day"] for row in yielded} == {"2025-01-01"}


def test_the_floor_leaves_a_wall_that_already_represents_every_kept_moment_alone() -> None:
    wall = _floor_wall()
    cut = _floor_cut()
    cut["keep"].extend(
        [
            {"asset_id": "A002", "reason": "The model chose this beat."},
            {"asset_id": "A005", "reason": "The model chose this beat."},
        ]
    )

    floored = final_cut_contract.apply_kept_moment_floor(
        wall,
        cut,
        kept_moment_ids=("M001", "M002", "M003"),
    )

    assert [row["asset_id"] for row in floored["keep"]] == ["A001", "A002", "A005"]
    assert floored["moment_floor"]["restored"] == []
    assert floored["moment_floor"]["ceiling_yielded"] == []


def test_reconsideration_cannot_resurrect_a_review_cut_asset_but_may_use_a_sibling(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M002"),
        _candidate(4, "M003"),
    )

    def local_raw(alias: str) -> str:
        return json.dumps(
            {
                "schema_version": "visual-final-pool-reconsideration-v1",
                "verdict": "revise",
                "changes": [
                    {
                        "add_asset_ids": [alias],
                        "remove_asset_ids": [],
                        "visible_gain": f"{alias} supplies an unreached occasion.",
                        "displaced_contribution": "No removal is needed.",
                    }
                ],
                "overall_reason": "One occasion is missing from the film.",
            }
        )

    global_raw = json.dumps(
        {
            "schema_version": "visual-final-pool-global-validation-v1",
            "decisions": [
                {
                    "change_id": "C001",
                    "verdict": "accept",
                    "reason": "The sibling occasion is absent from the complete cut.",
                }
            ],
            "overall_reason": "The one surviving addition improves the whole film.",
        }
    )

    class Requester:
        def ask(self, request):
            if "global-validation" in request.pass_name:
                return SimpleNamespace(raw_text=global_raw)
            wanted = "A004" if "private-4" in request.ordered_input_ids else "A002"
            return SimpleNamespace(raw_text=local_raw(wanted))

    result = matrix._run_visual_final_pool_reconsideration(
        candidates,
        current_aliases=("A001",),
        required_aliases=(),
        capacity=4,
        case=matrix.Case(
            key="case",
            label="A memory",
            product="year_in_review",
            ranges=(),
            target_seconds=600.0,
            brief="Make a truthful memory.",
        ),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {"chapter_id": "C001", "label": "first", "moment_ids": ["M001"], "thesis": "one"},
            {"chapter_id": "C002", "label": "second", "moment_ids": ["M002"], "thesis": "two"},
            {"chapter_id": "C003", "label": "third", "moment_ids": ["M003"], "thesis": "three"},
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
        review_cut_aliases=("A004",),
    )

    assert result is not None
    assert result["keep"] == ["A001", "A002"]
    assert result["skipped_review_cut_assets"] == ["A004"]
    rejected = [row for row in result["decisions"] if row["verdict"] == "reject"]
    assert [row["change_id"] for row in rejected] == ["C002"]
    assert "global review already cut" in rejected[0]["reason"]


def test_text_reconsideration_cannot_resurrect_a_review_cut_asset_but_may_use_a_sibling(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M002"),
        _candidate(4, "M003"),
    )
    cut = {
        "keep": [
            {"asset_id": "A001", "reason": "The owner starred this beat."},
            {"asset_id": "A004", "reason": "A distinct closing event."},
        ],
        "required_asset_ids": [],
    }
    revise = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "revise",
            "findings": [
                {
                    "kind": "missing_place_or_progression",
                    "asset_ids": ["A001"],
                    "visible_defect": "The middle of the film shows no second place.",
                    "missing_contribution": "A candidate from the unreached moment.",
                },
                {
                    "kind": "missing_action_or_event",
                    "asset_ids": ["A004"],
                    "visible_defect": "No action carries the same moment.",
                    "missing_contribution": "A candidate showing the action.",
                },
            ],
            "overall_reason": "Two visible contributions are absent.",
        }
    )
    proposal = json.dumps(
        {
            "schema_version": "description-final-asset-reconsideration-v1",
            "changes": [
                {
                    "finding_id": "F001",
                    "add_asset_ids": ["A002"],
                    "remove_asset_ids": [],
                    "reason": "A002 supplies the unreached place.",
                },
                {
                    "finding_id": "F002",
                    "add_asset_ids": ["A003"],
                    "remove_asset_ids": [],
                    "reason": "A003 supplies the missing action.",
                },
            ],
            "overall_reason": "Both additions carry absent contributions.",
        }
    )
    accept = json.dumps(
        {
            "schema_version": "description-final-asset-delta-validation-v1",
            "verdict": "accept",
            "supported_finding_ids": ["F001"],
            "reason": "The surviving addition resolves the cited gap.",
        }
    )
    stable = json.dumps(
        {
            "schema_version": "description-final-asset-audit-v1",
            "verdict": "stable",
            "findings": [],
            "overall_reason": "The revised corpus has distinct contributions.",
        }
    )
    answers = iter((revise, proposal, accept, stable))

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    reviewed, deliberation = asyncio.run(
        matrix._iterative_final_asset_review(
            candidates,
            cut,
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            capacity=4,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            max_iterations=3,
            review_cut_aliases=("A003",),
        )
    )

    assert [row["asset_id"] for row in reviewed["keep"]] == ["A001", "A002", "A004"]
    guard = deliberation["iterations"][0]["calls"]["reconsideration_review_cut"]
    assert guard["skipped_review_cut_assets"] == ["A003"]
    assert [row["change_id"] for row in guard["decisions"]] == ["F002"]
    assert "global review already cut" in guard["decisions"][0]["reason"]


def _pool_case() -> Any:
    return matrix.Case(
        key="case",
        label="A memory",
        product="year_in_review",
        ranges=(),
        target_seconds=600.0,
        brief="Make a truthful memory.",
    )


def _minute_candidate(index: int, moment: str) -> FineCutCandidate:
    return replace(_candidate(index, moment), taken_at=START + timedelta(minutes=index))


def _pool_revision(*changes: dict[str, Any]) -> str:
    return json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "revise" if changes else "stable",
            "changes": list(changes),
            "overall_reason": "The bounded pool carries one unreached beat.",
        }
    )


def _pool_stable() -> str:
    return json.dumps(
        {
            "schema_version": "visual-final-pool-reconsideration-v1",
            "verdict": "stable",
            "changes": [],
            "overall_reason": "Nothing in this pool adds a distinct contribution.",
        }
    )


def _local_pool_requests(requests: list[Any]) -> list[Any]:
    return [
        request for request in requests if request.pass_name == matrix.VISUAL_FINAL_POOL_PASS_NAME
    ]


def _request_aliases(request: Any) -> list[str]:
    return [reference.entity_id for page in request.pages for reference in page.tile_refs]


def test_a_reconsideration_group_over_the_tile_budget_is_split_into_bounded_requests(
    tmp_path: Path,
) -> None:
    candidates = (_minute_candidate(1, "M001"),) + tuple(
        _minute_candidate(index, "M002") for index in range(2, 32)
    )
    global_raw = json.dumps(
        {
            "schema_version": "visual-final-pool-global-validation-v1",
            "decisions": [
                {
                    "change_id": "C001",
                    "verdict": "accept",
                    "reason": "The capped alternative is the stronger visible beat.",
                },
                *(
                    {
                        "change_id": change_id,
                        "verdict": "reject",
                        "reason": "The complete cut already carries this.",
                    }
                    for change_id in ("C002", "C003", "C004")
                ),
            ],
            "overall_reason": "One swap improves the whole film.",
        }
    )
    requests: list[Any] = []

    # WHY: the vision gateway is the only external boundary here; every tile split
    # decision under test happens before the request leaves the process.
    class Requester:
        def ask(self, request):
            requests.append(request)
            if "global-validation" in request.pass_name:
                return SimpleNamespace(raw_text=global_raw)
            alternative = next(
                alias for alias in _request_aliases(request) if alias not in {"A002", "A003"}
            )
            return SimpleNamespace(
                raw_text=_pool_revision(
                    {
                        "add_asset_ids": [alternative],
                        "remove_asset_ids": [],
                        "visible_gain": f"{alternative} shows the beat the cut misses.",
                        "displaced_contribution": "No removal is needed.",
                    },
                    {
                        "add_asset_ids": [],
                        "remove_asset_ids": ["A002"],
                        "visible_gain": "Dropping A002 loses nothing visible.",
                        "displaced_contribution": "A002 repeats A003.",
                    },
                )
            )

    result = matrix._run_visual_final_pool_reconsideration(
        candidates,
        current_aliases=("A001", "A002", "A003"),
        required_aliases=(),
        capacity=40,
        case=_pool_case(),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {
                "chapter_id": "C001",
                "label": "first",
                "moment_ids": ["M001", "M002"],
                "thesis": "One chapter the cap thinned.",
            },
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
        cap_removed_aliases=("A004",),
    )

    local = _local_pool_requests(requests)
    assert [len(_request_aliases(request)) for request in local] == [12, 12, 10]
    assert all(len(request.pages) == 1 for request in local)
    assert all("A002" in _request_aliases(request)[:2] for request in local)
    assert result is not None
    assert [row["change_id"] for row in result["proposals"]] == ["C001", "C002", "C003", "C004"]
    assert [row["add_asset_ids"] for row in result["proposals"]] == [
        ["A004"],
        [],
        ["A014"],
        ["A024"],
    ]
    assert result["groups"][0]["request_count"] == 3
    assert result["groups"][0]["truncated_tiles"] == 0
    assert result["accepted_change_ids"] == ["C001"]


def test_a_reconsideration_group_inside_the_tile_budget_issues_one_unchanged_request(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M002"),
    )
    readings = (
        {
            "chapter_id": "C001",
            "label": "first",
            "moment_ids": ["M001", "M002"],
            "thesis": "One complete chapter.",
        },
    )
    requests: list[Any] = []

    # WHY: the vision gateway is the only external boundary; the prompt it receives is
    # the artefact under test.
    class Requester:
        def ask(self, request):
            requests.append(request)
            return SimpleNamespace(raw_text=_pool_stable())

    result = matrix._run_visual_final_pool_reconsideration(
        candidates,
        current_aliases=("A001",),
        required_aliases=(),
        capacity=4,
        case=_pool_case(),
        thesis={"thesis": "A grounded year."},
        chapter_readings=readings,
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
    )

    assert result is not None
    assert len(requests) == 1
    issued = requests[0]
    assert issued.ordered_group_ids == ("P001",)
    assert _request_aliases(issued) == ["A002", "A003"]
    groups = final_cut_contract.visual_final_pool_groups(
        candidates,
        current_aliases=("A001",),
        chapter_readings=readings,
        review_focus=final_cut_contract.runtime_final_pool_findings(
            candidates,
            current_aliases=("A001",),
            chapter_readings=readings,
        ),
    )
    assert issued.prompt == final_cut_contract.visual_final_pool_reconsideration_prompt(
        candidates,
        current_aliases=("A001",),
        group=groups[0],
        tile_mapping=tuple(
            (reference.number, reference.entity_id)
            for page in issued.pages
            for reference in page.tile_refs
        ),
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded year."},
        capacity=4,
        required_aliases=(),
    )


def test_a_pool_group_whose_request_fails_is_never_asked_again(tmp_path: Path) -> None:
    candidates = (
        _candidate(1, "M001"),
        _candidate(2, "M002"),
        _candidate(3, "M002"),
    )
    requests: list[Any] = []

    # WHY: the vision gateway is the only external boundary, and a terminal gateway
    # failure is exactly the receipt this guard was measured against.
    class Requester:
        def ask(self, request):
            requests.append(request)
            raise TimeoutError("the local model never answered")

    reviewed_focus_keys: set[tuple[str, tuple[str, ...]]] = set()
    failed_focus_keys: set[tuple[str, tuple[str, ...]]] = set()
    arguments = {
        "current_aliases": ("A001",),
        "required_aliases": (),
        "capacity": 4,
        "case": _pool_case(),
        "thesis": {"thesis": "A grounded year."},
        "chapter_readings": (
            {
                "chapter_id": "C001",
                "label": "first",
                "moment_ids": ["M001", "M002"],
                "thesis": "One complete chapter.",
            },
        ),
        "requester": Requester(),
        "output_dir": tmp_path / "visual-pool",
        "preview_jpeg": lambda _asset_id: b"not-a-real-jpeg",
        "timeout_seconds": 30,
        "reviewed_focus_keys": reviewed_focus_keys,
        "failed_focus_keys": failed_focus_keys,
    }

    first = matrix._run_visual_final_pool_reconsideration(candidates, iteration=1, **arguments)
    second = matrix._run_visual_final_pool_reconsideration(candidates, iteration=2, **arguments)

    assert first is not None
    assert first["verdict"] == "stable"
    assert first["failed_focus"] == [
        {
            "group_id": "P001",
            "focus_kind": "unrepresented_isolated_moment",
            "moment_ids": ["M002"],
            "error": "TimeoutError",
        }
    ]
    assert failed_focus_keys == {("unrepresented_isolated_moment", ("M002",))}
    assert reviewed_focus_keys == set()
    assert second is None
    assert len(requests) == 1


def test_the_request_bound_truncates_a_group_and_records_the_dropped_tiles(
    tmp_path: Path,
) -> None:
    candidates = (_minute_candidate(1, "M001"),) + tuple(
        _minute_candidate(index, "M002") for index in range(2, 82)
    )
    requests: list[Any] = []

    # WHY: the vision gateway is the only external boundary; the request bound under
    # test is applied before anything is sent.
    class Requester:
        def ask(self, request):
            requests.append(request)
            return SimpleNamespace(raw_text=_pool_stable())

    result = matrix._run_visual_final_pool_reconsideration(
        candidates,
        current_aliases=("A001",),
        required_aliases=(),
        capacity=40,
        case=_pool_case(),
        thesis={"thesis": "A grounded year."},
        chapter_readings=(
            {
                "chapter_id": "C001",
                "label": "first",
                "moment_ids": ["M001", "M002"],
                "thesis": "One very dense chapter.",
            },
        ),
        requester=Requester(),
        output_dir=tmp_path / "visual-pool",
        preview_jpeg=lambda _asset_id: b"not-a-real-jpeg",
        iteration=1,
        timeout_seconds=30,
    )

    assert result is not None
    assert [len(_request_aliases(request)) for request in requests] == [12] * 6
    assert result["groups"][0]["request_count"] == 6
    assert result["groups"][0]["truncated_tiles"] == 8


def test_a_split_pool_request_anchors_the_current_tiles_of_its_group() -> None:
    candidates = tuple(_minute_candidate(index, "M001") for index in range(1, 21))
    group = {
        "group_id": "P001",
        "chapter_id": "C001",
        "label": "first",
        "current_asset_ids": ["A001", "A002"],
        "asset_ids": [candidate.alias for candidate in candidates],
        "target_moment_ids": ["M001"],
    }

    slices = final_cut_contract.visual_final_pool_request_groups(candidates, group)

    assert [len(row["asset_ids"]) for row in slices] == [12, 10]
    assert [row["group_id"] for row in slices] == ["P001s01", "P001s02"]
    assert all(row["current_asset_ids"] == ["A001", "A002"] for row in slices)
    assert all(row["asset_ids"][:2] == ["A001", "A002"] for row in slices)
    assert [alias for row in slices for alias in row["asset_ids"][2:]] == [
        candidate.alias for candidate in candidates[2:]
    ]
    assert slices[0]["chapter_id"] == "C001"
