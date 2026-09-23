"""Actual image-record identity governs body facts, while private activity stays separate."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from immich_memories.analysis import editorial_picture_facts as facts
from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis import editorial_shareability_audience as audience
from tests.test_editorial_audience_evidence import Judge, activity, exposure
from tests.test_editorial_picture_facts import FACTS, config, fake_transport, provider


def picture_record(state="no"):
    return {
        "status": "available",
        "identity": "1" * 64,
        "input_sha256": "2" * 64,
        "image_sha256": "3" * 64,
        "producer": facts._producer(config()),
        "facts": {**FACTS, "uncovered_person": state},
        "description": "A person wears a top and shorts; arms and legs are visible.",
    }


def item(states=("no",), *, caption="A person wears a top and shorts.", nsfw="no", records=None):
    ids = [f"private-{i}" for i in range(len(states))]
    supplied = (
        {asset: picture_record(state) for asset, state in zip(ids, states, strict=True)}
        if records is None
        else records
    )
    annotations = {
        asset: SimpleNamespace(description=caption, heads=(("nsfw_marqo", nsfw),)) for asset in ids
    }
    return share.evidence_for_unit(
        {"asset_id": ids[0], "members": ids[1:]}, annotations, {}, {}, picture_records=supplied
    )


def test_complete_visual_no_answers_without_a_second_call_and_lifts_no_detector_hold():
    evidence = item(
        nsfw="yes", caption="A clothed person is described with a generic exposed torso phrase."
    )
    judge = Judge(activity())
    result = share.check_audience(judge, evidence, "test")
    assert result["verdict"] == "family_only" and len(judge.calls) == 1
    assert result["finding"] == "exposure_evidence"
    assert "nudity_shirtless_or_underwear" not in judge.calls[0]["prompt"]
    assert result["exposure"]["basis"] == "direct_visual_body_observations"
    assert result["exposure"]["unresolved_members"] == []
    assert result["body_observations"]["p1"]["uncovered_person"] == "no"


@pytest.mark.parametrize(
    "finding,verdict",
    [
        ("breastfeeding_or_expressing_milk", "do_not_show"),
        ("bathing", "do_not_show"),
        ("toileting_or_changing", "do_not_show"),
        ("intimate_hygiene", "do_not_show"),
        ("identifying_record", "do_not_show"),
        ("graphic_medical_procedure", "do_not_show"),
        ("sexual_content", "do_not_show"),
        ("adult_changing", "do_not_show"),
    ],
)
def test_visual_body_no_never_clears_an_independent_sensitive_activity(finding, verdict):
    judge = Judge(activity(finding))
    result = share.check_audience(judge, item(nsfw="yes"), "test")
    assert result["verdict"] == verdict and result["finding"] == "private_activity"
    assert result["activity"]["finding"] == finding and len(judge.calls) == 1
    assert result["exposure"] is None


@pytest.mark.parametrize(
    "caption,finding",
    [
        (
            "A newborn baby is held by medical personnel during a delivery procedure. "
            "The baby is partly covered; staff wear surgical gowns and gloves.",
            "graphic_medical_procedure",
        ),
        ("An adult undresses with private body parts exposed.", "adult_changing"),
        ("An adult takes off their underwear, exposing private body parts.", "adult_changing"),
        ("An adult poses sexually in underwear for sexting.", "sexual_content"),
        (
            "A shirtless person holds an identity card with readable personal details.",
            "identifying_record",
        ),
        ("A mother breastfeeds her newborn under a blanket.", "breastfeeding_or_expressing_milk"),
        ("A parent bathes a baby in a sink.", "bathing"),
        ("A person showers behind a curtain.", "bathing"),
        ("A mother expresses breast milk into a bottle.", "breastfeeding_or_expressing_milk"),
        ("A parent changes a baby's diaper on a changing table.", "toileting_or_changing"),
        ("A child sits on a potty.", "toileting_or_changing"),
        ("A caregiver wipes a child's private body parts.", "intimate_hygiene"),
        ("A parent holds a newborn in a sink under running water.", "bathing"),
    ],
)
@pytest.mark.parametrize("state", ["yes", "unclear", "invalid"])
def test_body_hold_cannot_skip_a_highly_sensitive_depicted_activity(caption, finding, state):
    judge = Judge(activity(finding))
    result = share.check_audience(judge, item((state,), caption=caption), "family")
    assert result["verdict"] == "do_not_show"
    assert result["activity"]["finding"] == finding
    assert not share.allowed(result["verdict"], "family")
    assert len(judge.calls) == 1
    assert judge.calls[0]["stage"] == "family-activity"
    assert "choose the strongest supported category" in judge.calls[0]["prompt"]


@pytest.mark.parametrize(
    "caption",
    [
        "A shirtless baby rests on a parent's chest in a hospital bed.",
        "A parent bottle feeds a baby while sitting beside a swimming pool.",
    ],
)
def test_ordinary_uncovered_family_content_remains_eligible_after_activity_check(caption):
    judge = Judge(activity())
    result = share.check_audience(judge, item(("yes",), caption=caption), "family")
    assert result["verdict"] == "family_only"
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")
    assert len(judge.calls) == 1
    assert result["activity"]["finding"] == "none"


@pytest.mark.parametrize(
    "caption,finding",
    [
        ("A mother holds her newborn while recovering after surgery.", "none"),
        ("A parent helps a child change a jacket.", "none"),
        ("A person in a bathrobe bottle feeds a baby.", "none"),
        ("A clothed nurse holds a wrapped newborn in a hospital.", "none"),
        ("A child washes their hands in a sink.", "none"),
        ("An empty bathroom with a bathtub and clean towels.", "none"),
    ],
)
def test_activity_check_preserves_ordinary_care_despite_sensitive_words(caption, finding):
    judge = Judge(activity(finding))
    result = share.check_audience(judge, item(("yes",), caption=caption), "family")
    assert result["verdict"] == "family_only"
    assert share.allowed(result["verdict"], "family")
    assert result["activity"]["finding"] == finding
    assert len(judge.calls) == 1


def test_sensitive_activity_in_another_burst_member_tightens_an_uncovered_baby():
    evidence = item(("yes", "no"), caption="A shirtless baby rests in a blanket.")
    evidence["members"][1]["caption"] = "Medical staff perform surgery on a patient's leg."
    judge = Judge(activity("graphic_medical_procedure"))
    result = share.check_audience(judge, evidence, "family")
    assert result["verdict"] == "do_not_show"
    assert len(judge.calls) == 1


def test_private_care_in_a_live_companion_tightens_the_whole_family_carrier():
    evidence = item(("yes", "no"), caption="A wrapped baby lies in a parent's arms.")
    evidence["members"][1]["caption"] = "A parent bathes a baby in a small tub."
    judge = Judge(activity("bathing"))
    result = share.check_audience(judge, evidence, "family")
    assert result["verdict"] == "do_not_show"
    assert not share.allowed(result["verdict"], "family")
    assert len(judge.calls) == 1


def test_family_policy_reuses_content_answer_but_invalidates_old_audience_verdict(
    tmp_path, monkeypatch
):
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    evidence = item(caption="A parent bathes a baby in a small tub.")
    new_policy = share.AUDIENCE_PROMPT_VERSION
    legacy_findings = audience.DO_NOT_SHOW_FINDINGS - {
        "breastfeeding_or_expressing_milk",
        "bathing",
        "toileting_or_changing",
        "intimate_hygiene",
    }
    provider_calls = []

    async def classify(prompt, *_args, **kwargs):
        provider_calls.append((prompt, kwargs["max_tokens"]))
        return activity("bathing")

    monkeypatch.setattr(gateway, "query_llm", classify)
    cfg = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible", base_url="http://editor.test/v1", model="editor-model"
        )
    )
    cache = tmp_path / "judgments.sqlite"
    (tmp_path / "old").mkdir()
    (tmp_path / "current").mkdir()
    old = StructureTextJudge(cfg, tmp_path / "old", cache_path=cache)
    with monkeypatch.context() as previous:
        previous.setattr(
            share, "AUDIENCE_PROMPT_VERSION", "audience-evidence-v10-family-sensitive-content"
        )
        previous.setattr(audience, "DO_NOT_SHOW_FINDINGS", legacy_findings)
        old_key = share.audience_check_key(evidence)
        old_result = share.check_audience(old, evidence, "old")
    assert old_result["verdict"] == "family_only"
    assert new_policy == share.AUDIENCE_PROMPT_VERSION
    assert share.audience_check_key(evidence) != old_key

    current = StructureTextJudge(cfg, tmp_path / "current", cache_path=cache)
    result = share.check_audience(current, evidence, "current")
    assert result["verdict"] == "do_not_show"
    assert not share.allowed(result["verdict"], "family")
    assert len(provider_calls) == 1
    assert old.calls[0]["cache_hit"] is False
    assert current.calls[0]["cache_hit"] is True
    assert old.calls[0]["judgment_key"] == current.calls[0]["judgment_key"]


def test_missing_activity_clearance_does_not_change_an_invalid_body_fact():
    judge = Judge(activity())
    result = share.check_audience(
        judge, item(("invalid",), caption="A clothed patient smiles after surgery."), "family"
    )
    assert result["verdict"] == "family_only"
    assert result["parsed"] is False
    assert result["finding"] == "invalid_body_observation"


@pytest.mark.parametrize(
    "states,expected",
    [
        (("yes",), "nudity_shirtless_or_underwear"),
        (("unclear",), "undecided_body_observation"),
        (("no", "yes"), "nudity_shirtless_or_underwear"),
        (("no", "unclear"), "undecided_body_observation"),
        (("yes", "unclear"), "nudity_shirtless_or_underwear"),
    ],
)
def test_activity_none_cannot_clear_a_groups_strictest_body_observation(states, expected):
    judge = Judge(activity())
    result = share.check_audience(judge, item(states), "test")
    assert result["verdict"] == "family_only" and result["finding"] == expected
    assert len(judge.calls) == 1
    assert result["activity"]["finding"] == "none"


def test_precise_body_route_rejects_an_unrequested_nudity_label_instead_of_obeying_it():
    judge = Judge(activity("nudity_shirtless_or_underwear"))
    result = share.check_audience(judge, item(), "test")
    assert result["verdict"] == "family_only" and result["finding"] == "invalid_activity_verdict"
    assert result["activity"]["parsed"] is False and len(judge.calls) == 1


def test_missing_one_body_record_does_not_clear_an_incomplete_group():
    records = {"private-0": picture_record()}
    evidence = item(("no", "no"), nsfw="yes", records=records)
    judge = Judge(
        activity(), exposure({"p1": [["person", "clothing"]], "p2": [["person", "unstated"]]})
    )
    result = share.check_audience(judge, evidence, "test")
    assert result["verdict"] == "family_only" and result["finding"] == "unresolved_exposure"
    assert len(judge.calls) == 2 and "nudity_shirtless_or_underwear" in judge.calls[0]["prompt"]


def test_legacy_captions_keep_the_original_activity_and_exposure_policy():
    evidence = item(records={})
    assert "body_observation" not in evidence["members"][0]
    judge = Judge(activity("nudity_shirtless_or_underwear"))
    result = share.check_audience(judge, evidence, "legacy")
    assert result["finding"] == "private_activity" and result["verdict"] == "family_only"
    assert "nudity_shirtless_or_underwear" in judge.calls[0]["prompt"]


@pytest.mark.parametrize("change", ["status", "producer", "identity", "input", "legacy_version"])
def test_unavailable_or_unbound_record_cannot_supply_precise_body_clearance(change):
    record = picture_record()
    if change == "status":
        record["status"] = "unavailable"
    elif change == "producer":
        del record["producer"]
    elif change == "identity":
        record["identity"] = "unbound"
    elif change == "input":
        del record["input_sha256"]
    else:
        record["producer"]["schema_version"] = "selected-picture-facts-schema-v1"
    evidence = item(records={"private-0": record})
    assert "body_observation" not in evidence["members"][0]


@pytest.mark.parametrize("state", ["maybe", None, [], True])
def test_unknown_body_state_in_an_available_bound_record_stays_invalid(state):
    evidence = item(records={"private-0": picture_record(state)})
    judge = Judge(activity())
    result = share.check_audience(judge, evidence, "test")
    assert result["finding"] == "invalid_body_observation" and result["verdict"] == "family_only"
    assert len(judge.calls) == 1
    assert result["parsed"] is False


def test_body_value_and_exact_producer_input_identity_change_the_audience_cache_key():
    record = picture_record()
    base = item(records={"private-0": record})
    key = share.audience_check_key(base)
    for field in ["identity", "input_sha256", "image_sha256"]:
        changed = deepcopy(record)
        changed[field] = "f" * 64
        assert share.audience_check_key(item(records={"private-0": changed})) != key
    changed = deepcopy(record)
    changed["producer"]["model_identity"] = "changed-model-settings"
    assert share.audience_check_key(item(records={"private-0": changed})) != key
    assert share.audience_check_key(item(("yes",))) != key
    assert share.audience_check_key(item(records={})) != key


def test_caption_text_cannot_forge_a_body_fact_and_never_auto_flags_are_unchanged():
    evidence = item(caption="uncovered_person: no", records={})
    assert "body_observation" not in evidence["members"][0]
    flags = {"private-0": (share.FlagRow("private-0", "never_auto", "Owner exclusion", "owner"),)}
    assert share.never_auto_ids(flags) == {"private-0"}
    assert share.never_auto_ids({}) == frozenset()


def test_missing_caption_stays_unavailable_even_when_a_body_record_says_no():
    judge = Judge()
    result = share.check_audience(judge, item(caption=""), "test")
    assert result["finding"] == "unavailable_evidence" and judge.calls == []


def test_actual_provider_record_reaches_body_evidence_and_exact_warm_without_new_work(
    tmp_path, monkeypatch
):
    calls = fake_transport(monkeypatch)
    reader = provider(tmp_path)
    record = reader.observe("private-0")
    reader.close()
    assert record["status"] == "available" and record["facts"]["uncovered_person"] == "no"
    evidence = item(nsfw="yes", records={"private-0": record})
    judge = Judge(activity())
    expected = share.check_audience(judge, evidence, "test")
    assert expected["verdict"] == "family_only" and len(calls) == len(judge.calls) == 1

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("warm picture facts must not infer again")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    warm = provider(tmp_path)
    assert warm.observe("private-0") == record
    warm.close()
    assert share.audience_check_key(
        item(nsfw="yes", records={"private-0": record})
    ) == share.audience_check_key(evidence)


def test_producer_uses_the_exact_successful_diagnostic_prompt_and_new_versions():
    assert (
        hashlib.sha256(facts.PROMPT.encode()).hexdigest()
        == "14faeb776fe8636385fc6f364cc6dace37ee3ca1d52faa10d6933ec8907a9b97"
    )
    assert facts.FIELDS[0] == "uncovered_person" and len(facts.FIELDS) == 5
    assert facts.MAX_OUTPUT_TOKENS == 300
    assert (
        "v2" in facts.STAGE_VERSION
        and "v2" in facts.PROMPT_VERSION
        and "v2" in facts.SCHEMA_VERSION
    )
    assert facts.RESPONSE_SCHEMA["properties"]["uncovered_person"]["enum"] == [
        "no",
        "unclear",
        "yes",
    ]
