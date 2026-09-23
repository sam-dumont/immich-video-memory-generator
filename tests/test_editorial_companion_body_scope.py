"""A material still cannot clear a warning on a different, unobserved video."""

from copy import deepcopy

import pytest

from immich_memories.analysis import editorial_shareability as share
from tests.test_editorial_audience_evidence import Judge, activity, annotation, exposure
from tests.test_editorial_visual_body_audience import picture_record


def evidence(*, records=None, flags=None, two=False, caption=None, warning="head"):
    videos = ["private-video-a", "private-video-b"] if two else ["private-video-a"]
    annotations = {"private-still": annotation("A fully clothed person waves.", nsfw_marqo="yes")}
    rows = dict(flags or {})
    for video in videos:
        annotations[video] = annotation(caption, nsfw_marqo="yes" if warning == "head" else "no")
        if warning == "flag":
            rows[video] = (
                *rows.get(video, ()),
                share.FlagRow(video, "review", "exposure=nude", "public-exposure-v2"),
            )
    return share.evidence_for_unit(
        {"asset_id": "private-still", "members": ["private-still"], "video_ids": videos},
        annotations,
        rows,
        {},
        picture_records={"private-still": picture_record("no"), **(records or {})},
    )


@pytest.mark.parametrize("warning", ["head", "flag"])
def test_unobserved_positive_companion_stays_unresolved_after_activity_without_exposure_call(
    warning,
):
    item = evidence(warning=warning)
    judge = Judge(activity())
    result = share.check_audience(judge, item, "audience")
    assert result["verdict"] == "family_only"
    assert result["finding"] == "unresolved_companion_exposure"
    assert result["unresolved_companions"] == ["v1"]
    assert result["companion_body_warnings"][0]["body_observation"] is None
    assert result["activity"]["finding"] == "none"
    assert [call["stage"] for call in judge.calls] == ["audience-activity"]
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")
    assert "private-video" not in str(item)


@pytest.mark.parametrize("state", ["yes", "unclear", "invalid"])
def test_companion_positive_or_unresolved_own_observation_cannot_be_cleared_by_still(state):
    item = evidence(records={"private-video-a": picture_record(state)})
    judge = Judge(activity())
    result = share.check_audience(judge, item, "audience")
    assert result["finding"] == "unresolved_companion_exposure"
    assert result["verdict"] == "family_only"
    assert result["activity"]["finding"] == "none"
    assert [call["stage"] for call in judge.calls] == ["audience-activity"]
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")


def test_valid_companion_no_resolves_only_its_warning_and_keeps_activity_authority():
    item = evidence(records={"private-video-a": picture_record("no")})
    ordinary = Judge(activity())
    shared = share.check_audience(ordinary, item, "audience")
    # Its warning is resolved, but the still's own detector hold stands.
    assert shared["verdict"] == "family_only" and len(ordinary.calls) == 1
    assert shared["finding"] == "exposure_evidence"
    assert shared["companion_body_warnings"][0]["body_observation"]["record_identity"] == "1" * 64
    private = Judge(activity("bathing"))
    held = share.check_audience(private, item, "audience")
    assert held["finding"] == "private_activity" and held["verdict"] == "do_not_show"
    assert not share.allowed(held["verdict"], "family")
    assert not share.allowed(held["verdict"], "sendable")
    two = evidence(records={"private-video-a": picture_record("no")}, two=True)
    second_judge = Judge(activity())
    unresolved = share.check_audience(second_judge, two, "audience")
    assert unresolved["unresolved_companions"] == ["v2"]
    assert unresolved["finding"] == "unresolved_companion_exposure"
    assert unresolved["verdict"] == "family_only" and len(second_judge.calls) == 1


@pytest.mark.parametrize("invalid", ["producer", "identity", "status"])
def test_unbound_or_unavailable_companion_record_does_not_clear_warning(invalid):
    record = picture_record("no")
    if invalid == "producer":
        record["producer"]["schema_version"] = "old"
    elif invalid == "identity":
        record["identity"] = "not-an-identity"
    else:
        record["status"] = "unavailable"
    judge = Judge(activity())
    result = share.check_audience(judge, evidence(records={"private-video-a": record}), "a")
    assert result["finding"] == "unresolved_companion_exposure"
    assert result["verdict"] == "family_only" and result["activity"]["finding"] == "none"
    assert [call["stage"] for call in judge.calls] == ["a-activity"]


def test_owner_clear_is_member_local_and_model_clear_is_not_authority():
    owner = share.FlagRow("private-video-a", "cleared", "owner reviewed", "owner")
    item = evidence(flags={"private-video-a": (owner,)})
    assert "companion_body_warnings" not in item
    # The clip is the owner's to clear; the still's own detector hold is not cleared.
    assert share.check_audience(Judge(activity()), item, "a")["finding"] == "exposure_evidence"
    two = evidence(flags={"private-video-a": (owner,)}, two=True)
    assert len(two["companion_body_warnings"]) == 1
    two_judge = Judge(activity())
    two_result = share.check_audience(two_judge, two, "a")
    assert two_result["verdict"] == "family_only"
    assert two_result["finding"] == "unresolved_companion_exposure"
    assert len(two_judge.calls) == 1
    model = share.FlagRow("private-video-a", "cleared", "reader says covered", "reader-30b")
    model_judge = Judge(activity())
    model_result = share.check_audience(
        model_judge, evidence(flags={"private-video-a": (model,)}), "a"
    )
    assert model_result["verdict"] == "family_only"
    assert model_result["finding"] == "unresolved_companion_exposure"
    assert len(model_judge.calls) == 1


def test_bound_no_does_not_lift_hard_never_auto_for_the_companion():
    flags = {
        "private-video-a": (share.FlagRow("private-video-a", "never_auto", "private", "detector"),)
    }
    unit = {"asset_id": "private-still", "video_ids": ["private-video-a"]}
    kept, excluded = share.partition_units([unit], share.never_auto_ids(flags))
    assert not kept and excluded == [unit]


def test_captioned_companion_still_uses_existing_activity_and_exposure_requests():
    item = evidence(caption="A fully clothed person waves.")
    assert "companion_body_warnings" not in item
    judge = Judge(
        activity(), exposure({"p1": [["person", "clothing"]], "p2": [["person", "clothing"]]})
    )
    result = share.check_audience(judge, item, "a")
    assert result["verdict"] == "family_only" and len(judge.calls) == 2
    assert result["finding"] == "exposure_evidence"


def test_no_companion_warning_leaves_observed_false_positive_keys_and_requests_unchanged():
    item = evidence(warning="none")
    assert "companion_body_warnings" not in item
    expected = {
        "version": share.AUDIENCE_CHECK_POLICY_VERSION,
        "members": item["members"],
        "companion_detectors": [{"nsfw_marqo": "no"}],
        "companion_flags": [],
    }
    assert item == expected
    assert share.audience_check_key(item) == share.audience_check_key(expected)
    actual_judge, expected_judge = Judge(activity()), Judge(activity())
    assert share.check_audience(actual_judge, item, "a") == share.check_audience(
        expected_judge, expected, "a"
    )
    assert actual_judge.calls == expected_judge.calls
    assert len(actual_judge.calls) == 1


def test_derived_bank_cannot_reuse_pre_fix_clearance_and_exact_warm_needs_no_model():
    item = evidence()
    old = {key: value for key, value in item.items() if key != "companion_body_warnings"}
    bank = {share.audience_check_key(old): {"verdict": "share"}}
    key = share.audience_check_key(item)
    assert key not in bank
    judge = Judge(activity())
    bank[key] = share.check_audience(judge, item, "a")
    assert [call["stage"] for call in judge.calls] == ["a-activity"]
    warm_key = share.audience_check_key(deepcopy(item))
    assert warm_key == key and bank[warm_key]["finding"] == "unresolved_companion_exposure"
    assert bank[warm_key]["activity"]["finding"] == "none"
    assert len(judge.calls) == 1  # Exact bank lookup does not repeat full assessment.
    resolved = evidence(records={"private-video-a": picture_record("no")})
    changed = deepcopy(resolved)
    changed["companion_body_warnings"][0]["body_observation"]["input_sha256"] = "9" * 64
    assert share.audience_check_key(resolved) not in bank
    assert share.audience_check_key(resolved) != share.audience_check_key(changed)
