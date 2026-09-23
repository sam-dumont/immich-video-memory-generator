"""The activity classifier has final authority; coverage review may only tighten a share."""

from __future__ import annotations

import hashlib
import json

import pytest

from immich_memories.analysis import editorial_picture_facts as picture_facts
from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure


def observed(state="no"):
    """A picture-facts record that carries this run's producer identity."""
    digest = hashlib.sha256(state.encode()).hexdigest()
    return {
        "status": "available",
        "identity": digest,
        "input_sha256": digest,
        "image_sha256": digest,
        "facts": {"uncovered_person": state},
        "producer": {
            "pass_version": picture_facts.STAGE_VERSION,
            "prompt_version": picture_facts.PROMPT_VERSION,
            "schema_version": picture_facts.SCHEMA_VERSION,
            "prompt_sha256": hashlib.sha256(picture_facts.PROMPT.encode()).hexdigest(),
            "schema_sha256": picture_facts._digest(picture_facts.RESPONSE_SCHEMA),
        },
    }


class Annotation:
    def __init__(self, description, heads=()):
        self.description = description
        self.heads = heads


class Judge:
    """Answers each editorial stage from a script, recording what it was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.stages: list[str] = []

    def ask(self, stage, prompt, max_tokens):
        self.stages.append(stage)
        answer = self.answers.pop(0) if self.answers else '{"finding":"none","why":"Ordinary."}'
        if isinstance(answer, Exception):
            raise answer
        return answer


def finding(name, why="Observed."):
    return json.dumps({"finding": name, "why": why})


def coverage(rows):
    return json.dumps({"observations": rows})


def failure():
    attempts = [
        {"outcome": "invalid", "raw": "", "max_tokens": 120, "error": "empty"},
        {"outcome": "invalid", "raw": "", "max_tokens": 240, "error": "empty again"},
    ]
    return TextCompletionFailure(attempts)


def evidence_of(unit, annotations, flags=None, records=None):
    return share.evidence_for_unit(
        unit,
        annotations,
        flags or {},
        {},
        picture_records=records,
    )


def test_a_member_without_a_caption_is_an_evidence_gap_rather_than_a_share():
    evidence = evidence_of({"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("")})
    judge = Judge([])

    result = share.check_audience(judge, evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["parsed"] is False
    assert result["finding"] == "unavailable_evidence"
    assert result["missing_members"] == ["p1"]
    assert judge.stages == []


def test_a_private_activity_ends_the_check_before_any_coverage_review():
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A toddler on a potty.")}
    )
    judge = Judge([finding("toileting_or_changing", "Toilet training.")])

    result = share.check_audience(judge, evidence, "unit-1")

    assert result["verdict"] == "do_not_show"
    assert result["finding"] == "private_activity"
    assert result["activity"]["finding"] == "toileting_or_changing"
    assert judge.stages == ["unit-1-activity"]


def test_nudity_is_household_only_while_the_listed_private_activities_leave_no_export():
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A shirtless person.")}
    )

    result = share.check_audience(
        Judge([finding("nudity_shirtless_or_underwear")]), evidence, "unit-1"
    )

    assert result["verdict"] == "family_only"
    assert result["finding"] == "private_activity"
    assert share.allowed(result["verdict"], "sendable") is False


def test_an_answer_outside_the_owner_vocabulary_leaves_the_carrier_undecided():
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A person walking.")}
    )

    result = share.check_audience(Judge([finding("vibes_only")]), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "invalid_activity_verdict"
    assert result["activity"]["parsed"] is False


def test_an_exhausted_activity_stage_is_recorded_rather_than_guessed():
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A person walking.")}
    )

    result = share.check_audience(Judge([failure()]), evidence, "unit-1")

    assert result["finding"] == "undecided_activity"
    assert result["failed_stage"] == "unit-1-activity"
    assert result["completion_failure"]["attempts"][-1]["error"] == "empty again"


def test_an_unflagged_ordinary_picture_needs_no_coverage_review():
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A landscape at sunset.")}
    )
    judge = Judge([finding("none")])

    result = share.check_audience(judge, evidence, "unit-1")

    assert result["verdict"] == "share"
    assert result["finding"] == "none"
    assert judge.stages == ["unit-1-activity"]


def flagged_evidence():
    return evidence_of(
        {"asset_id": "solo", "members": ["solo"]},
        {"solo": Annotation("A person at the seaside.", (("nsfw_marqo", "yes"),))},
    )


def test_a_caption_that_explains_a_positive_exposure_signal_does_not_lift_the_hold():
    """A model reading only adds holds: the owner prefers a false positive to a miss."""
    judge = Judge([finding("none"), coverage({"p1": [["a person", "clothing"]]})])

    result = share.check_audience(judge, flagged_evidence(), "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "exposure_evidence"
    assert result["exposure"]["clearances"] == [{"member": "p1", "basis": "clothed_or_covered"}]
    assert judge.stages == ["unit-1-activity", "unit-1-exposure-1"]


def test_a_positive_signal_the_caption_never_explains_tightens_to_family_only():
    judge = Judge([finding("none"), coverage({"p1": [["a person", "unstated"]]})])

    result = share.check_audience(judge, flagged_evidence(), "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "unresolved_exposure"
    assert result["exposure"]["unresolved_members"] == ["p1"]


@pytest.mark.parametrize(
    "answer,expected",
    [
        (coverage({"p2": [["a person", "clothing"]]}), "invalid_exposure_verdict"),
        ("not an answer", "invalid_exposure_verdict"),
    ],
)
def test_an_unreadable_coverage_answer_never_clears_the_signal(answer, expected):
    judge = Judge([finding("none"), answer])

    result = share.check_audience(judge, flagged_evidence(), "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == expected
    assert result["exposure"]["parsed"] is False


def test_an_exhausted_coverage_stage_is_recorded_rather_than_guessed():
    judge = Judge([finding("none"), failure()])

    result = share.check_audience(judge, flagged_evidence(), "unit-1")

    assert result["finding"] == "undecided_exposure"
    assert result["failed_stage"] == "unit-1-exposure-1"
    assert result["exposure"]["groups"][0]["status"] == "failed"


def test_direct_body_observations_answer_the_coverage_question_without_a_second_call():
    unit = {"asset_id": "solo", "members": ["solo"]}
    evidence = evidence_of(
        unit,
        {"solo": Annotation("A person at the seaside.", (("nsfw_marqo", "yes"),))},
        records={"solo": observed("no")},
    )
    judge = Judge([finding("none")])

    result = share.check_audience(judge, evidence, "unit-1")

    # The observation answers the coverage question, but it cannot lift the detector's hold.
    assert result["verdict"] == "family_only" and result["finding"] == "exposure_evidence"
    assert result["exposure"]["basis"] == "direct_visual_body_observations"
    assert judge.stages == ["unit-1-activity"]


def test_an_ordinary_activity_cannot_clear_an_uncovered_person_the_pixels_showed():
    unit = {"asset_id": "solo", "members": ["solo"]}
    evidence = evidence_of(
        unit, {"solo": Annotation("A person indoors.")}, records={"solo": observed("yes")}
    )

    result = share.check_audience(Judge([finding("none")]), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "nudity_shirtless_or_underwear"


def test_an_unresolved_body_observation_keeps_the_carrier_out_of_a_sendable_export():
    unit = {"asset_id": "solo", "members": ["solo"]}
    evidence = evidence_of(
        unit, {"solo": Annotation("A person indoors.")}, records={"solo": observed("unclear")}
    )

    result = share.check_audience(Judge([finding("none")]), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "undecided_body_observation"
