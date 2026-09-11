"""Story weighting validates complete domain decisions, including banked replies."""

import json

import pytest

from immich_memories.analysis import editorial_text_gateway as gateway
from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_story_weight_contract import (
    WEIGHING_CONTRACT_VERSION,
    StoryWeightDecisionError,
    validate_weight_reply,
)
from immich_memories.analysis.editorial_structure_io import StructureTextJudge
from immich_memories.cache.judgment_cache import JudgmentCache
from immich_memories.config import Config
from immich_memories.config_models_llm import LLMConfig
from tests.test_editorial_story_reading import ScriptedJudge
from tests.test_editorial_story_weight_audit import weigh


def complete():
    return {"about": [], "weights": {"K01": "major", "K02": "minor", "K03": "none"}}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("about", None),
        ("about", {}),
        ("about", "K01"),
        ("about", [None]),
        ("about", [["K01"]]),
        ("about", [{"key": "K01"}]),
        ("about", ["K99"]),
        ("about", ["K01", "K01"]),
        ("about", ["K01", "K02", "K03"]),
        ("weights", None),
        ("weights", []),
        ("weights", "major"),
        ("weights", {}),
        ("weights", {"K01": "major", "K02": "minor", "K03": "none", "K99": "minor"}),
        ("weights", {"K01": "dominant", "K02": "minor", "K03": "none"}),
        ("weights", {"K01": "majority", "K02": "minor", "K03": "none"}),
        ("weights", {"K01": True, "K02": "minor", "K03": "none"}),
        ("weights", {"K01": {}, "K02": "minor", "K03": "none"}),
        ("join", None),
        ("join", {}),
        ("join", "K01"),
        ("join", [["K01"]]),
        ("join", [["K01", "K99"]]),
        ("join", [["K01", "K01"]]),
        ("join", [["K01", ["K02"]]]),
        ("join", [{"left": "K01", "right": "K02"}]),
        ("retitle", None),
        ("retitle", []),
        ("retitle", "A new name"),
        ("retitle", {"K99": "A new name"}),
        ("retitle", {"K01": " "}),
        ("retitle", {"K01": {"title": "A new name"}}),
    ],
)
def test_invalid_domain_fields_are_rejected_with_a_typed_audit(field, value):
    payload = {**complete(), field: value}

    with pytest.raises(StoryWeightDecisionError) as failure:
        validate_weight_reply(
            payload, story_keys=["K01", "K02", "K03"], candidates=["K01", "K02", "K03"]
        )

    assert failure.value.audit["coverage_complete"] is False


@pytest.mark.parametrize("field", ["about", "weights"])
def test_required_fields_cannot_be_silently_defaulted(field):
    payload = complete()
    del payload[field]
    with pytest.raises(StoryWeightDecisionError) as failure:
        validate_weight_reply(payload, story_keys=["K01", "K02", "K03"], candidates=[])
    assert failure.value.audit["shape_valid"] is False


def test_optional_edits_default_empty_and_central_weight_can_be_redundant():
    payload = {**complete(), "about": ["K01"]}
    answer, audit = validate_weight_reply(
        payload, story_keys=["K01", "K02", "K03"], candidates=["K01"]
    )
    assert answer == {**payload, "join": [], "retitle": {}}
    assert audit["coverage_complete"] is True


def test_known_story_is_not_an_allowed_central_candidate_without_reading_support():
    with pytest.raises(StoryWeightDecisionError) as failure:
        validate_weight_reply(
            {**complete(), "about": ["K01"]}, story_keys=["K01", "K02", "K03"], candidates=["K03"]
        )
    assert failure.value.audit["ignored_about_entries"] == ["K01"]


def test_repair_explains_candidate_and_label_errors_without_assigning_weights():
    invalid = json.dumps(
        {"about": ["K02"], "weights": {"K01": "background", "K02": "none", "K03": "maybe"}}
    )
    corrected = json.dumps({"about": ["K03"], "weights": {"K01": "none", "K02": "minor"}})
    judge = ScriptedJudge(
        lambda stage, _prompt: invalid if stage == "story-weighing-source" else corrected
    )

    result = weigh(judge)

    repair = judge.prompt("story-weighing-source-repair")
    assert 'Invalid "about" entries: ["K02"]' in repair
    assert 'The only eligible keys for "about" are: ["K03"]' in repair
    assert "Choose at most two of these, or use []" in repair
    assert 'Invalid weight labels for: ["K01", "K03"]' in repair
    assert 'only "major", "minor", "glimpse", or "none"' in repair
    assert [row["weight"] for row in result] == ["none", "minor", "dominant"]
    assert len(judge.asked) == 3


def test_repair_with_no_central_candidates_requires_an_empty_about():
    judge = ScriptedJudge(lambda _stage, _prompt: json.dumps({**complete(), "about": ["K01"]}))

    with pytest.raises(StoryWeightDecisionError):
        weigh(judge, candidates=())

    repair = judge.prompt("story-weighing-source-repair")
    assert 'The only eligible keys for "about" are: []' in repair
    assert 'The corrected object must contain "about": []' in repair
    assert (
        'Never output the input reading labels "remarkable", "maybe", or "background" as weights.'
        in repair
    )
    assert len(judge.asked) == 3


def test_stringified_join_is_rejected_and_repair_shows_nested_array_shape():
    invalid = json.dumps({**complete(), "join": ["K01,K02"]})
    judge = ScriptedJudge(
        lambda stage, _prompt: invalid if stage.endswith("source") else json.dumps(complete())
    )
    weigh(judge, candidates=())
    repair = judge.prompt("story-weighing-source-repair")
    assert '"join": [["key_a", "key_b"]]' in repair
    assert '"key_a,key_b" is not a pair' in repair
    assert len(judge.asked) == 3


def test_cached_incomplete_reply_is_validated_then_repaired_and_reused_offline(
    tmp_path, monkeypatch
):
    valid = '{"about":["K03"],"weights":{"K01":"minor","K02":"none"}}'
    scripted = ScriptedJudge(lambda _stage, _prompt: valid)
    weigh(scripted)
    config = Config(
        llm=LLMConfig(
            provider="openai-compatible", base_url="http://editor.test/v1", model="synthetic-editor"
        )
    )
    cache_path = tmp_path / "judgments.sqlite"
    cache = JudgmentCache(cache_path)
    requests = []
    for call in scripted.asked:
        request = TextRequest(
            prompt=call["prompt"],
            llm_config=config.llm,
            cache_path=cache_path,
            max_tokens=1200,
            timeout_seconds=int(config.llm.timeout_seconds),
        )
        requests.append(request)
        cache.remember(
            request.judgment_key,
            '{"about":[],"weights":{}}' if call["stage"].endswith("source") else valid,
        )
    cache.close()
    sent = []

    async def synthetic_completion(prompt, _config, **kwargs):
        sent.append((prompt, kwargs))
        return valid

    # WHY: intercept the provider transport only; use the real production judge,
    # request identity and SQLite bank to exercise semantic validation on hits.
    monkeypatch.setattr(gateway, "query_llm", synthetic_completion)
    (tmp_path / "cold").mkdir()
    (tmp_path / "warm").mkdir()
    cold = StructureTextJudge(config, tmp_path / "cold", cache_path=cache_path)
    warm = StructureTextJudge(config, tmp_path / "warm", cache_path=cache_path)

    first = weigh(cold)
    second = weigh(warm)

    assert first == second
    assert [row["cache_hit"] for row in cold.calls] == [True, False, True]
    assert [row["cache_hit"] for row in warm.calls] == [True, True, True]
    assert len(sent) == 1
    assert sent[0][0].startswith(requests[0].prompt)
    assert "PREVIOUS ANSWER REJECTED" in sent[0][0]
    assert sent[0][1]["require_complete"] is True
    assert cold.calls[0]["judgment_key"] != cold.calls[1]["judgment_key"]
    assert WEIGHING_CONTRACT_VERSION in requests[0].prompt
    for judge in (cold, warm):
        saved = sorted((judge.out / "calls").glob("*story-weighing-source.response.private.txt"))
        assert len(saved) == 1
        assert json.loads(saved[0].read_text()) == {"about": [], "weights": {}}
