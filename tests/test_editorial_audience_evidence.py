"""Activity exclusion and detector resolution have separate, ordered authority."""

import json
from types import SimpleNamespace

import httpx
import pytest

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure


def annotation(caption, **heads):
    return SimpleNamespace(description=caption, heads=tuple(heads.items()))


def evidence(caption, **heads):
    return share.evidence_for_unit(
        {"asset_id": "private-id"},
        {"private-id": annotation(caption, **heads)},
        {},
        {},
    )


def activity(finding="none", **fields):
    return json.dumps(
        {"why": "The caption describes ordinary family activity.", "finding": finding, **fields}
    )


def exposure(observations=None, **fields):
    return json.dumps(
        {
            "observations": observations
            if observations is not None
            else {"p1": [["person", "unstated"]]},
            "why": "The caption describes the depicted people and coverage.",
            **fields,
        }
    )


class Judge:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.calls = []

    def ask(self, stage, prompt, max_tokens):
        self.calls.append({"stage": stage, "prompt": prompt, "max_tokens": max_tokens})
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_no_signal_hospital_proximity_needs_only_the_activity_decision():
    item = evidence("A woman is lying in bed with her baby.", venue="medical", nsfw_marqo="no")
    judge = Judge(activity())
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "share"
    assert actual["exposure"] is None
    assert len(judge.calls) == 1
    assert "nsfw_marqo" not in judge.calls[0]["prompt"]


def test_a_private_activity_has_final_authority_even_when_a_detector_could_be_cleared():
    item = evidence("A mother breastfeeds her child under a cover.", nsfw_marqo="yes")
    judge = Judge(
        activity("breastfeeding_or_expressing_milk"),
        exposure({"p1": [["mother", "body_cover"], ["child", "body_cover"]]}),
    )
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "do_not_show"
    assert not share.allowed(actual["verdict"], "family")
    assert actual["finding"] == "private_activity"
    assert actual["exposure"] is None
    assert len(judge.calls) == 1


def test_active_surgery_has_its_own_final_activity_category():
    item = evidence("A surgeon is performing surgery on a patient's leg,", nsfw_marqo="no")
    judge = Judge(activity("graphic_medical_procedure"))
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "do_not_show"
    assert not share.allowed(actual["verdict"], "family")
    assert not share.allowed(actual["verdict"], "sendable")
    assert actual["finding"] == "private_activity"
    assert actual["activity"]["finding"] == "graphic_medical_procedure"
    assert actual["exposure"] is None
    assert len(judge.calls) == 1
    assert "preparation for surgery, recovery afterward" in judge.calls[0]["prompt"]


@pytest.mark.parametrize(
    "caption",
    [
        "A fully clothed patient receives a blood-pressure check from a nurse.",
        "A fully clothed patient smiles in a hospital bed while recovering after surgery.",
    ],
)
def test_ordinary_clothed_treatment_and_recovery_remain_eligible(caption):
    judge = Judge(activity())
    actual = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert actual["verdict"] == "share"
    assert len(judge.calls) == 1
    assert "Ordinary clothed treatment" in judge.calls[0]["prompt"]


def test_the_activity_prompt_has_no_detector_resolution_or_clearance_instruction():
    prompt = share.audience_check_prompt(evidence("A parent changes a baby on a changing table."))
    assert "Ordinary clothed care" not in prompt
    assert "changing a baby on a changing table" in prompt
    assert "breastfeeding_or_expressing_milk" in prompt
    assert "clearance" not in prompt
    assert "nsfw_marqo" not in prompt
    assert '"clear"' not in prompt


def test_the_activity_prompt_calls_a_baby_in_the_pool_family_content():
    prompt = share.audience_check_prompt(evidence("A parent holds a baby in a swimming pool."))
    assert (
        "Swimming is not bathing: a pool, a lake or the sea, including a parent holding a baby "
        "in the water and a baby's swimming lesson, does not match these categories."
    ) in prompt


def test_positive_detector_cannot_disappear_behind_a_vague_caption():
    item = evidence("A woman is holding a newborn baby on her chest.", nsfw_marqo="yes")
    judge = Judge(activity(), exposure())
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "family_only"
    assert actual["finding"] == "unresolved_exposure"
    assert actual["exposure"]["unresolved_members"] == ["p1"]
    assert [call["stage"] for call in judge.calls] == ["test-activity", "test-exposure-1"]
    assert {call["max_tokens"] for call in judge.calls} == {120}


def test_code_maps_closed_categories_and_ignores_extra_audience_verdict_metadata():
    item = evidence("A mother breastfeeds her child under a cover.", nsfw_marqo="yes")
    judge = Judge(
        activity("breastfeeding_or_expressing_milk", verdict="share", quote="under a cover")
    )
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "do_not_show"
    assert actual["parsed"] is True
    assert actual["activity"]["finding"] == "breastfeeding_or_expressing_milk"
    assert len(judge.calls) == 1


def test_harmless_extra_citation_does_not_invert_an_allowed_content_classification():
    judge = Judge(
        activity(source="p1.caption", quote="An ordinary hospital scene.", verdict="family_only")
    )
    result = share.check_audience(judge, evidence("A clothed family holds a newborn."), "test")
    assert result["verdict"] == "share"
    assert result["activity"]["finding"] == "none"
    assert result["parsed"] is True


def test_positive_landscape_detector_has_a_benign_caption_observation():
    item = evidence("An empty rocky beach with a white lighthouse.", nsfw_marqo="yes")
    judge = Judge(activity(), exposure({"p1": []}))
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "share"
    assert actual["exposure"]["unresolved_members"] == []
    assert actual["exposure"]["groups"][0]["captions"] == {
        "p1": "An empty rocky beach with a white lighthouse."
    }
    assert "nsfw_marqo" not in judge.calls[1]["prompt"]


def test_every_positive_member_requires_its_own_supported_clearance():
    item = share.evidence_for_unit(
        {"asset_id": "a", "members": ["a", "b"]},
        {
            "a": annotation("A fully clothed person sits on a chair.", nsfw_marqo="yes"),
            "b": annotation("A person is sitting on a bed.", nsfw_marqo="yes"),
        },
        {},
        {},
    )
    actual = share.check_audience(
        Judge(
            activity(),
            exposure({"p1": [["person", "clothing"]], "p2": [["person", "unstated"]]}),
        ),
        item,
        "test",
    )
    assert actual["verdict"] == "family_only"
    assert actual["exposure"]["unresolved_members"] == ["p2"]
    incomplete = Judge(activity(), exposure({"p2": [["person", "clothing"]]}))
    assert share.check_audience(incomplete, item, "test")["parsed"] is False


@pytest.mark.parametrize(
    "caption",
    [
        "A newborn baby is wrapped in a light blue blanket and held by a person's hand.",
        "A man is holding a newborn baby wrapped in a white blanket.",
    ],
)
def test_partial_person_coverage_cannot_resolve_a_picture_warning(caption):
    item = evidence(caption, nsfw_marqo="yes")
    judge = Judge(activity(), exposure({"p1": [["adult", "unstated"], ["baby", "body_cover"]]}))
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "family_only"
    assert actual["finding"] == "unresolved_exposure"
    assert actual["exposure"]["groups"][0]["captions"] == {"p1": caption}
    prompt = judge.calls[1]["prompt"]
    assert "for each human separately" in prompt
    assert "Clothing described for one person is that person's attribute" in prompt
    assert "through a hand or arm is still a human mention" in prompt
    assert "private-id" not in prompt


def test_explicit_coverage_for_every_person_can_resolve_a_picture_warning():
    item = evidence(
        "A man wearing a t-shirt holds a baby wrapped in a white blanket.", nsfw_marqo="yes"
    )
    actual = share.check_audience(
        Judge(activity(), exposure({"p1": [["man", "clothing"], ["baby", "body_cover"]]})),
        item,
        "test",
    )
    assert actual["verdict"] == "share"
    assert actual["exposure"]["unresolved_members"] == []


def test_long_burst_exposure_is_bounded_and_stops_at_an_unresolved_group():
    annotations = {
        str(i): annotation("A fully clothed person poses.", nsfw_marqo="yes") for i in range(6)
    }
    item = share.evidence_for_unit(
        {"asset_id": "0", "members": list(annotations)}, annotations, {}, {}
    )
    judge = Judge(
        activity(),
        exposure({"p1": [["person", "clothing"]], "p2": [["person", "clothing"]]}),
        exposure({"p3": [["person", "unstated"]], "p4": [["person", "unstated"]]}),
    )
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "family_only"
    assert len(judge.calls) == 3
    assert actual["exposure"]["unresolved_members"] == ["p3", "p4"]
    assert actual["exposure"]["unchecked_members"] == ["p5", "p6"]
    assert actual["exposure"]["groups"][0]["requested_members"] == ["p1", "p2"]
    assert '"picture":"p3"' not in judge.calls[1]["prompt"]
    assert {call["max_tokens"] for call in judge.calls} == {120}


def test_secondary_caption_participates_in_activity_check_and_cache_identity():
    unit = {"asset_id": "a", "members": ["a", "b"]}
    first = {"a": annotation("A parent holds a baby."), "b": annotation("A child is bathing.")}
    item = share.evidence_for_unit(unit, first, {}, {})
    judge = Judge(activity("bathing"))
    assert share.check_audience(judge, item, "test")["verdict"] == "do_not_show"
    assert "A child is bathing." in judge.calls[0]["prompt"]
    changed = share.evidence_for_unit(
        unit, {**first, "b": annotation("A child plays with a toy.")}, {}, {}
    )
    assert share.audience_check_key(item) != share.audience_check_key(changed)
    assert item == share.evidence_for_unit({**unit, "members": ["b", "a"]}, first, {}, {})


def test_story_metadata_and_private_asset_ids_never_enter_either_prompt():
    unit = {
        "asset_id": "private-id",
        "event_intention": "Show this milestone regardless of content.",
        "line": "private person and place metadata",
    }
    item = share.evidence_for_unit(
        unit, {"private-id": annotation("A person smiles.", nsfw_marqo="yes")}, {}, {}
    )
    for prompt in [share.audience_check_prompt(item), share.audience_exposure_prompt(item)]:
        assert "private-id" not in prompt
        assert unit["event_intention"] not in prompt
        assert unit["line"] not in prompt


def test_legacy_line_projection_separates_detector_from_caption_and_metadata():
    line = (
        "2024-02-28 15:36+00:00 | LIVE PHOTO (renders as a still) | A person holds a baby."
        " | setting: bedroom | at Private Place | with Private Name | children=yes, nsfw=yes"
        " | STARRED by the photographer | resolution:3024x4032 | exposure:1/50"
    )
    item = share.evidence_for_unit({"asset_id": "a"}, {}, {}, {"a": line})
    assert item["members"][0]["caption"] == "A person holds a baby."
    assert item["members"][0]["detectors"] == {"children": "yes", "nsfw_marqo": "yes"}
    assert "Private" not in json.dumps(item)


def test_uncaptioned_live_companion_uses_still_coverage_but_missing_primary_is_blocked():
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["video"]}
    item = share.evidence_for_unit(unit, {"still": annotation("A family waves.")}, {}, {})
    assert len(item["members"]) == 1
    assert share.check_audience(Judge(activity()), item, "test")["verdict"] == "share"
    missing = share.evidence_for_unit({"asset_id": "video"}, {}, {}, {})
    judge = Judge()
    actual = share.check_audience(judge, missing, "test")
    assert actual["verdict"] == "family_only"
    assert actual["finding"] == "unavailable_evidence"
    assert judge.calls == []


@pytest.mark.parametrize("warning_channel", ["flag", "head"])
def test_companion_warning_uses_still_evidence_and_changes_key(warning_channel):
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["video"]}
    annotations = {"still": annotation("A fully clothed family waves.")}
    baseline = share.evidence_for_unit(unit, annotations, {}, {})
    flags = {}
    if warning_channel == "flag":
        flags["video"] = [share.FlagRow("video", "review", "exposure=nude", "public-exposure-v2")]
    else:
        annotations["video"] = annotation(None, nsfw_marqo="yes")
    item = share.evidence_for_unit(unit, annotations, flags, {})
    assert share.audience_check_key(baseline) != share.audience_check_key(item)
    assert (
        share.check_audience(Judge(activity(), exposure()), item, "test")["verdict"]
        == "family_only"
    )
    resolved = Judge(activity(), exposure({"p1": [["family", "clothing"]]}))
    assert share.check_audience(resolved, item, "test")["verdict"] == "share"


def test_venue_only_review_flags_do_not_trigger_exposure_review():
    item = share.evidence_for_unit(
        {"asset_id": "a"},
        {"a": annotation("A family poses on a hospital bed.")},
        {"a": [share.FlagRow("a", "review", "venues=bedroom,medical", "interim-nominator-v1")]},
        {},
    )
    judge = Judge(activity())
    assert share.check_audience(judge, item, "test")["verdict"] == "share"
    assert len(judge.calls) == 1


@pytest.mark.parametrize(
    "observations",
    [
        {},
        {"p2": []},
        {"p1": "share"},
        {"p1": [["person", "share"]]},
        {"p1": [["", "clothing"]]},
        {"p1": [["person"]]},
        {"p1": [{"person": "clothing"}]},
    ],
)
def test_exposure_requires_complete_known_aliases_and_closed_observations(observations):
    judge = Judge(activity(), exposure(observations))
    result = share.check_audience(judge, evidence("A person smiles.", nsfw_marqo="yes"), "test")
    assert result["verdict"] == "family_only"
    assert result["finding"] == "invalid_exposure_verdict"
    assert result["parsed"] is False


def test_extra_exposure_approval_cannot_override_unclear_caption_evidence():
    judge = Judge(activity(), exposure({"p1": [["person", "unstated"]]}, verdict="share"))
    result = share.check_audience(
        judge, evidence("A person holds a baby.", nsfw_marqo="yes"), "test"
    )
    assert result["verdict"] == "family_only"
    assert result["finding"] == "unresolved_exposure"


def test_exposure_keeps_model_rows_as_unverified_claims_without_inventing_missing_people():
    rows = {"p1": [["person represented by hand", "unstated"]]}
    judge = Judge(activity(), exposure(rows))
    item = evidence("A wrapped baby is held by a person's hand.", nsfw_marqo="yes")
    result = share.check_audience(judge, item, "test")
    record = result["exposure"]["groups"][0]
    assert result["verdict"] == "family_only"
    assert record["observations"] == rows
    assert "unverified" in record["observation_basis"]
    assert record["captions"] == {"p1": "A wrapped baby is held by a person's hand."}


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"finding":[]}',
        '{"finding":"private_health","why":"A health result."}',
        '{"finding":"none","why":[]}',
    ],
)
def test_invalid_activity_contract_cannot_reach_exposure_review(raw):
    judge = Judge(raw)
    actual = share.check_audience(judge, evidence("A person smiles.", nsfw_marqo="yes"), "test")
    assert actual["verdict"] == "family_only"
    assert actual["parsed"] is False
    assert len(judge.calls) == 1


def completion_failure():
    return TextCompletionFailure(
        [
            {
                "outcome": "incomplete",
                "raw": '{"observations":',
                "max_tokens": budget,
                "error": "incomplete response",
            }
            for budget in (120, 240)
        ]
    )


@pytest.mark.parametrize("failed_step", ["activity", "exposure"])
def test_bounded_failure_is_an_undecided_private_carrier_without_another_attempt(failed_step):
    failure = completion_failure()
    judge = Judge(*([activity()] if failed_step == "exposure" else []), failure)
    actual = share.check_audience(judge, evidence("A person smiles.", nsfw_marqo="yes"), "test")
    assert actual["verdict"] == "family_only"
    assert actual["parsed"] is False
    assert actual["finding"] == f"undecided_{failed_step}"
    assert actual["failed_stage"] == f"test-{failed_step}" + (
        "-1" if failed_step == "exposure" else ""
    )
    assert actual["completion_failure"] == failure.as_record()
    assert "cache_hit" not in json.dumps(actual)
    assert len(judge.calls) == (2 if failed_step == "exposure" else 1)
    if failed_step == "exposure":
        assert actual["activity"]["finding"] == "none"
        assert actual["exposure"]["parsed"] is False
        assert actual["exposure"]["unresolved_members"] == ["p1"]
    else:
        assert actual["activity"] is None
        assert actual["exposure"] is None


def test_later_exposure_failure_preserves_prior_observations_and_unchecked_members():
    ids = ["a", "b", "c", "d", "e"]
    item = share.evidence_for_unit(
        {"asset_id": "a", "members": ids},
        {
            asset_id: annotation("A fully clothed person smiles.", nsfw_marqo="yes")
            for asset_id in ids
        },
        {},
        {},
    )
    first_rows = {"p1": [["person", "clothing"]], "p2": [["person", "clothing"]]}
    judge = Judge(activity(), exposure(first_rows), completion_failure())
    actual = share.check_audience(judge, item, "test")
    assert actual["verdict"] == "family_only"
    assert actual["parsed"] is False
    assert actual["failed_stage"] == "test-exposure-2"
    assert actual["activity"]["finding"] == "none"
    coverage = actual["exposure"]
    assert coverage["groups"][0]["observations"] == first_rows
    assert [row["member"] for row in coverage["clearances"]] == ["p1", "p2"]
    assert coverage["groups"][1] == {
        "parsed": False,
        "status": "failed",
        "requested_members": ["p3", "p4"],
    }
    assert coverage["unresolved_members"] == ["p3", "p4"]
    assert coverage["unchecked_members"] == ["p5"]
    assert len(judge.calls) == 3


@pytest.mark.parametrize("failed_step", ["activity", "exposure"])
@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), KeyError("implementation bug")])
def test_unrelated_network_and_programming_errors_propagate(failed_step, error):
    judge = Judge(*([activity()] if failed_step == "exposure" else []), error)
    with pytest.raises(type(error)) as caught:
        share.check_audience(judge, evidence("A person smiles.", nsfw_marqo="yes"), "test")
    assert caught.value is error
    assert len(judge.calls) == (2 if failed_step == "exposure" else 1)


@pytest.mark.parametrize("failed_step", ["activity", "exposure"])
def test_real_gateway_cold_and_cached_failure_have_identical_audience_semantics(
    tmp_path, monkeypatch, failed_step
):
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    item = evidence("A person smiles.", nsfw_marqo="yes")
    activity_prompt = share.audience_check_prompt(item)
    transport_budgets = []

    async def fake_query(prompt, *_args, **kwargs):
        transport_budgets.append(kwargs["max_tokens"])
        if failed_step == "exposure" and prompt == activity_prompt:
            return activity()
        raise gateway.LLMIncompleteResponse('{"observations":')

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible", base_url="http://editor.test/v1", model="editor-model"
        )
    )
    cache = tmp_path / "judgments.sqlite"
    (tmp_path / "cold").mkdir()
    (tmp_path / "warm").mkdir()
    cold = StructureTextJudge(config, tmp_path / "cold", cache_path=cache)
    warm = StructureTextJudge(config, tmp_path / "warm", cache_path=cache)
    cold_result = share.check_audience(cold, item, "test")
    assert transport_budgets == ([120, 120, 240] if failed_step == "exposure" else [120, 240])
    count_after_cold = len(transport_budgets)
    warm_result = share.check_audience(warm, item, "test")
    assert cold_result == warm_result
    assert cold_result["finding"] == f"undecided_{failed_step}"
    assert cold_result["verdict"] == "family_only"
    assert len(transport_budgets) == count_after_cold
    assert all(call["cache_hit"] is False for call in cold.calls)
    assert all(call["cache_hit"] is True for call in warm.calls)
