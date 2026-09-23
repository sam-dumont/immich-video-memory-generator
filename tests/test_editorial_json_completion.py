"""JSON completion and transport completion have distinct replay contracts.

The local-thread field test belongs to the retired thread lane and stays on the probe branch.
"""

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_json_completion import (
    complete_final_json,
    json_format_repair_prompt,
)
from immich_memories.analysis.editorial_text_gateway import QueryTextRequester
from immich_memories.config_models_llm import LLMConfig


def test_complete_revision_survives_unfinished_trailing_explanation():
    raw = '{"threads":[{"claim":"draft"}]}\nRevised:\n{"threads":[{"claim":"final"}]}\nThis meets the rules because'
    assert complete_final_json(raw) == '{"threads":[{"claim":"final"}]}'


@pytest.mark.parametrize(
    "raw",
    [
        '{"threads":[{"claim":"finished inner object"}]',
        '{"threads":[]}\nRevision: {"threads":[',
        '{"threads":[]} }',
        "no decision",
    ],
)
def test_incomplete_parent_or_revision_never_reuses_a_complete_fragment(raw):
    with pytest.raises(ValueError):
        complete_final_json(raw)


def test_braces_in_strings_do_not_split_objects():
    raw = '{"text":"literal { and } inside a string"}\nExplanation ends here'
    assert complete_final_json(raw) == '{"text":"literal { and } inside a string"}'


def test_field_incomplete_revision_never_falls_back_to_complete_draft():
    with pytest.raises(ValueError, match="exactly the requested fields"):
        complete_final_json('{"a":1,"b":2}\nRevision: {"b":3}', fields=("a", "b"))


def test_failed_field_repair_never_banks_partial_result(tmp_path):
    from immich_memories.cache.judgment_cache import JudgmentCache

    budgets = []

    async def query(_prompt, _config, **kwargs):
        budgets.append(kwargs["max_tokens"])
        return '{"last":true}'

    request = TextRequest(
        "schema",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
        json_fields=("first", "last"),
    )
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with (
        # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
        patch("immich_memories.analysis.editorial_text_gateway.query_llm", query),
        pytest.raises(ValueError, match="exactly the requested fields"),
    ):
        asyncio.run(QueryTextRequester().request(request))
    assert budgets == [400, 800]
    cache = JudgmentCache(request.cache_path)
    try:
        assert cache.answer_for(request.judgment_key) is None
    finally:
        cache.close()


def test_field_identity_changes_with_contract_but_not_field_order(tmp_path):
    request = TextRequest(
        "schema",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
        json_fields=("first", "last"),
    )
    assert request.judgment_key != replace(request, json_fields=()).judgment_key
    assert request.judgment_key != replace(request, json_fields=("first",)).judgment_key
    assert request.judgment_key == replace(request, json_fields=("last", "first")).judgment_key
    with pytest.raises(ValueError, match="JSON fields require"):
        replace(request, json_object=False)


def test_complete_disjoint_field_sequence_preserves_values_without_model_retry(tmp_path):
    import json

    raw = '  {"claim":"A ride {together}","anchors":["F02","F01"]},\n{"extra":{"literal":"é"}}  '
    calls, decoded = [], []

    async def query(_prompt, _config, **kwargs):
        calls.append(kwargs["max_tokens"])
        return raw

    request = TextRequest(
        "original evidence",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
        json_fields=("claim", "anchors", "extra"),
    )
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with patch("immich_memories.analysis.editorial_text_gateway.query_llm", query):
        requester = QueryTextRequester(json_decoding_observer=decoded.append)
        first = asyncio.run(requester.request(request))
        warm = asyncio.run(requester.request(request))
    assert json.loads(first.raw) == {
        "claim": "A ride {together}",
        "anchors": ["F02", "F01"],
        "extra": {"literal": "é"},
    }
    assert warm.raw == first.raw and warm.cache_hit
    assert calls == [400]
    assert len(decoded) == 1 and decoded[0]["raw"] == raw
    assert decoded[0]["decoded"] == first.raw


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1},{"a":2,"b":3}',
        '{"a":1,"a":2,"b":3}',
        '{"a":{"x":1,"x":2},"b":3}',
        '{"a":1},{"b":',
        '{"a":1},{"b":2},',
        '{"a":1},{"b":2} explanation',
        '[{"a":1},{"b":2}]',
        '[{"a":1},{"b":2}',
        '{"a":1},{"b":2},{"c":3}',
        '{"a":1}\nRevision: {"b":2}',
    ],
)
def test_field_sequences_reject_overwrite_incomplete_or_ambiguous_content(raw):
    with pytest.raises(ValueError):
        complete_final_json(raw, fields=("a", "b"))


def test_field_sequence_encoding_policy_is_part_of_only_field_request_identity(
    tmp_path, monkeypatch
):
    from immich_memories.analysis import editorial_json_completion

    request = TextRequest(
        "evidence",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
        json_fields=("a", "b"),
    )
    ordinary_json = replace(request, json_fields=())
    before, unchanged = request.judgment_key, ordinary_json.judgment_key
    monkeypatch.setattr(editorial_json_completion, "JSON_FIELDS_POLICY", "exact-fields-v1")
    assert request.judgment_key != before
    assert ordinary_json.judgment_key == unchanged


def test_json_contract_retries_only_incomplete_decision_then_warm_is_exact(tmp_path):
    calls = []

    async def query(*args, **kwargs):
        calls.append(kwargs)
        return '{"threads":[' if len(calls) == 1 else '{"threads":[]}\nTrailing explanation was cut'

    request = TextRequest(
        "same evidence",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
    )
    assert request.judgment_key != replace(request, json_object=False).judgment_key
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with patch("immich_memories.analysis.editorial_text_gateway.query_llm", query):
        first = asyncio.run(QueryTextRequester().request(request))
        warm = asyncio.run(QueryTextRequester().request(request))
    assert first.raw == warm.raw == '{"threads":[]}'
    assert warm.cache_hit and not first.cache_hit
    assert [call["max_tokens"] for call in calls] == [400, 800]
    assert all(call["require_complete"] is False for call in calls)


def test_short_malformed_fields_get_one_format_correction_and_only_valid_result_is_banked(tmp_path):
    broken = '{"claim1":"occasion","anchors1":["F01"]},"claim2":"outing","anchors2":["F02"]}'
    valid = '{"claim1":"occasion","anchors1":["F01"],"claim2":"outing","anchors2":["F02"]}'
    seen, failures = [], []

    async def query(prompt, _config, **kwargs):
        seen.append((prompt, kwargs["max_tokens"]))
        return broken if len(seen) == 1 else valid

    request = TextRequest(
        "evidence and schema",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
    )
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with patch("immich_memories.analysis.editorial_text_gateway.query_llm", query):
        requester = QueryTextRequester(json_failure_observer=failures.append)
        first = asyncio.run(requester.request(request))
        warm = asyncio.run(requester.request(request))
    assert first.raw == warm.raw == valid
    assert seen == [
        (request.prompt, 400),
        (json_format_repair_prompt(request.prompt, failures[0]["error"]), 800),
    ]
    assert warm.cache_hit
    assert len(failures) == 1
    assert failures[0]["raw"] == broken
    assert failures[0]["max_tokens"] == 400


def test_failed_format_repair_is_preserved_and_never_banked_as_a_decision(tmp_path):
    from immich_memories.cache.judgment_cache import JudgmentCache

    failures, budgets = [], []

    async def query(_prompt, _config, **kwargs):
        budgets.append(kwargs["max_tokens"])
        return '{"partial":true},"outside":true}'

    request = TextRequest(
        "evidence",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
    )
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with (
        # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
        patch("immich_memories.analysis.editorial_text_gateway.query_llm", query),
        pytest.raises(ValueError, match="no complete final JSON"),
    ):
        asyncio.run(QueryTextRequester(json_failure_observer=failures.append).request(request))
    assert budgets == [400, 800]
    assert len(failures) == 2
    cache = JudgmentCache(request.cache_path)
    try:
        assert cache.answer_for(request.judgment_key) is None
    finally:
        cache.close()


def test_json_recovery_policy_splits_only_json_request_identity(tmp_path, monkeypatch):
    from immich_memories.analysis import editorial_json_completion

    request = TextRequest(
        "evidence",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
    )
    plain = replace(request, json_object=False)
    current, unchanged = request.judgment_key, plain.judgment_key
    monkeypatch.setattr(editorial_json_completion, "JSON_RECOVERY_POLICY", "complete-final-json-v1")
    assert request.judgment_key != current
    assert plain.judgment_key == unchanged


def test_the_repair_request_names_the_field_the_reply_left_out(tmp_path):
    prompts = []

    async def query(prompt, _config, **_kwargs):
        prompts.append(prompt)
        return '{"last":true,"extra":1}' if len(prompts) == 1 else '{"first":1,"last":true}'

    request = TextRequest(
        "schema",
        LLMConfig(model="synthetic"),
        tmp_path / "bank.sqlite",
        400,
        30,
        json_object=True,
        json_fields=("first", "last"),
    )
    # WHY: the model provider is the external boundary; the scripted reply drives the parser under test
    with patch("immich_memories.analysis.editorial_text_gateway.query_llm", query):
        answer = asyncio.run(QueryTextRequester().request(request))
    assert answer.raw == '{"first":1,"last":true}'
    repair = prompts[1].removeprefix(request.prompt)
    assert 'missing ["first"]' in repair
    assert 'unexpected ["extra"]' in repair
