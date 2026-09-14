"""A host that thinks whether or not it was asked still owes an answer.

Measured 2026-09-14 on Melious and on OpenAI: a reasoning model bills its
private thinking inside the same completion budget as its answer, so the
reader's 4,000-token ask bought 4,000 tokens of thinking, HTTP 200, and
content "". These cover the budget that fixes it and the record that names it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis import llm_wire
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.llm_wire import (
    GROWN_REASONING_HEADROOM_TOKENS,
    REASONING_HEADROOM_TOKENS,
    LLMIncompleteResponse,
)
from immich_memories.config_models_llm import LLMConfig


@pytest.fixture(autouse=True)
def _forget_learned_endpoints():
    """Both memories are per-process, so a test must not inherit another's server."""
    llm_wire._REASONING_HEADROOM.clear()
    llm_wire.PARAM_ADAPTATIONS.clear()
    yield
    llm_wire._REASONING_HEADROOM.clear()
    llm_wire.PARAM_ADAPTATIONS.clear()


def _reply(
    *,
    content='{"ok": true}',
    finish_reason="stop",
    completion_tokens=120,
    reasoning_tokens=0,
):
    # WHY: the LLM server is the external boundary; everything under test is
    # what reaches it and how its answers are read, through query_llm only.
    response = AsyncMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": 4000,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
        }
    )
    response.raise_for_status = lambda: None
    return response


def _hosted(**overrides) -> LLMConfig:
    fields = {
        "provider": "openai-compatible",
        "base_url": "https://api.example.test/v1",
        "model": "reasoner",
        "thinking": "disabled",
        "no_thinking_params": {},
    }
    fields.update(overrides)
    return LLMConfig(**fields)


@pytest.mark.asyncio
async def test_a_reply_that_billed_reasoning_budgets_for_it_next_time():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch(
        "httpx.AsyncClient.post",
        side_effect=[_reply(reasoning_tokens=40), _reply(reasoning_tokens=40)],
    ) as post:
        await query_llm("first", _hosted(), max_tokens=4000)
        await query_llm("second", _hosted(), max_tokens=4000)

    first, second = (call[1]["json"] for call in post.call_args_list)
    assert first["max_tokens"] == 4000 and "reasoning_effort" not in first
    assert second["max_tokens"] == 4000 + REASONING_HEADROOM_TOKENS
    assert second["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_a_declared_reasoning_endpoint_pays_no_first_empty_call():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_reply(reasoning_tokens=40)) as post:
        await query_llm("only", _hosted(always_reasons=True), max_tokens=300)

    payload = post.call_args[1]["json"]
    assert payload["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS
    assert payload["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_an_endpoint_that_never_reasons_keeps_the_caller_s_cap_exact():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("only", _hosted(), max_tokens=300)

    payload = post.call_args[1]["json"]
    assert payload["max_tokens"] == 300
    assert "reasoning_effort" not in payload


@pytest.mark.asyncio
async def test_reasoning_that_ate_the_whole_budget_is_retried_with_more_room():
    starved = _reply(
        content="", finish_reason="length", completion_tokens=8492, reasoning_tokens=8492
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch(
        "httpx.AsyncClient.post", side_effect=[starved, _reply(content='{"read": true}')]
    ) as post:
        answer = await query_llm(
            "read the month",
            _hosted(always_reasons=True),
            max_tokens=300,
            require_complete=True,
        )

    assert answer == '{"read": true}'
    assert post.call_args_list[1][1]["json"]["max_tokens"] == 300 + GROWN_REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
async def test_a_reader_starved_twice_is_told_reasoning_took_the_budget():
    starved = _reply(
        content="", finish_reason="length", completion_tokens=16684, reasoning_tokens=16684
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with (
        patch("httpx.AsyncClient.post", side_effect=[starved, starved]),
        pytest.raises(LLMIncompleteResponse, match="reasoning used 16684 of 16684 tokens"),
    ):
        await query_llm(
            "read the month", _hosted(always_reasons=True), max_tokens=300, require_complete=True
        )


@pytest.mark.asyncio
async def test_a_truncated_answer_is_not_mistaken_for_starved_reasoning():
    """A partial answer means the budget reached the answer channel; more room buys prose."""
    cut_off = _reply(
        content='{"episodes": [{"epi',
        finish_reason="length",
        completion_tokens=8492,
        reasoning_tokens=600,
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with (
        patch("httpx.AsyncClient.post", side_effect=[cut_off]) as post,
        pytest.raises(LLMIncompleteResponse) as raised,
    ):
        await query_llm(
            "read the month", _hosted(always_reasons=True), max_tokens=300, require_complete=True
        )

    assert post.call_count == 1
    assert "reasoning used" not in str(raised.value)


@pytest.mark.asyncio
async def test_the_transport_record_carries_what_the_provider_billed():
    seen = []
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_reply(reasoning_tokens=622)):
        await query_llm("read", _hosted(), max_tokens=4000, transport_observer=seen.append)

    accepted = [attempt for attempt in seen if attempt.outcome == "response"][-1]
    assert accepted.finish_reason == "stop"
    assert accepted.reasoning_tokens == 622
    assert accepted.completion_tokens == 120


@pytest.mark.asyncio
async def test_an_incomplete_record_carries_what_the_provider_billed():
    seen = []
    starved = _reply(
        content="", finish_reason="length", completion_tokens=16684, reasoning_tokens=16684
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with (
        patch("httpx.AsyncClient.post", side_effect=[starved, starved]),
        pytest.raises(LLMIncompleteResponse),
    ):
        await query_llm(
            "read",
            _hosted(always_reasons=True),
            max_tokens=300,
            require_complete=True,
            transport_observer=seen.append,
        )

    incomplete = [attempt for attempt in seen if attempt.outcome == "incomplete"][-1]
    assert incomplete.finish_reason == "length"
    assert incomplete.reasoning_tokens == 16684


@pytest.mark.asyncio
async def test_a_host_that_refuses_the_effort_field_still_gets_the_room():
    refusal = AsyncMock(status_code=400)
    refusal.json = MagicMock(
        return_value={"error": {"message": "Unknown parameter: 'reasoning_effort'."}}
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", side_effect=[refusal, _reply()]) as post:
        await query_llm("read", _hosted(always_reasons=True), max_tokens=300)

    retried = post.call_args_list[1][1]["json"]
    assert "reasoning_effort" not in retried
    assert retried["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
async def test_openai_asks_for_the_least_reasoning_its_own_family_sells():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm(
            "pick the moments",
            LLMConfig(provider="openai", model="gpt-5.6-luna", api_key="sk-test"),
            max_tokens=300,
        )

    payload = post.call_args[1]["json"]
    assert payload["reasoning_effort"] == "minimal"
    assert payload["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
async def test_an_unfunded_first_call_that_thought_is_asked_again_with_room():
    """gemma-4-31b spent 3,163 of its 4,000 tokens thinking and cut the answer off."""
    cut_short = _reply(
        content='{"episodes": [{"epi',
        finish_reason="length",
        completion_tokens=4000,
        reasoning_tokens=3163,
    )
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch(
        "httpx.AsyncClient.post", side_effect=[cut_short, _reply(content='{"read": true}')]
    ) as post:
        answer = await query_llm(
            "read the month", _hosted(), max_tokens=4000, require_complete=True
        )

    assert answer == '{"read": true}'
    assert post.call_args_list[1][1]["json"]["max_tokens"] == 4000 + REASONING_HEADROOM_TOKENS
