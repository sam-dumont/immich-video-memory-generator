"""A host that thinks whether or not it was asked still owes an answer.

Measured 2026-09-14 on Melious and on OpenAI: a reasoning model bills its
private thinking inside the same completion budget as its answer, so the
reader's 4,000-token ask bought 4,000 tokens of thinking, HTTP 200, and
content "". These cover the budget that fixes it and the record that names it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.analysis import llm_wire
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.llm_wire import (
    GROWN_REASONING_HEADROOM_TOKENS,
    REASONING_HEADROOM_TOKENS,
    THINKING_MIN_MAX_TOKENS,
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
@pytest.mark.parametrize(
    "error",
    [
        {"message": "Unknown parameter: 'reasoning_effort'."},
        {"message": "Unsupported parameter: 'reasoning_effort'."},
        {"code": "unsupported_parameter", "param": "reasoning_effort"},
    ],
)
async def test_a_host_that_refuses_the_effort_field_still_gets_the_room(error):
    refusal = AsyncMock(status_code=400)
    refusal.json = MagicMock(return_value={"error": error})
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", side_effect=[refusal, _reply()]) as post:
        await query_llm("read", _hosted(always_reasons=True), max_tokens=300)

    retried = post.call_args_list[1][1]["json"]
    assert "reasoning_effort" not in retried
    assert retried["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-luna-2026-09-01"])
async def test_luna_disables_reasoning_and_keeps_the_answer_budget_in_live_and_batch(model):
    config = LLMConfig(provider="openai", model=model, api_key="sk-test")
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("pick the moments", config, max_tokens=300)

    payload = post.call_args[1]["json"]
    assert payload["reasoning_effort"] == "none"
    assert payload["max_tokens"] == 300
    assert llm_wire.batch_text_payload(config, "pick the moments", max_tokens=300) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "unsupported_value",
            "Unsupported value: 'reasoning_effort' does not support 'minimal' with this model. "
            "Supported values are: 'none', 'low', 'medium', 'high'.",
        ),
        (None, "Unsupported value: 'reasoning_effort' does not support 'minimal'."),
        (None, "Unsupported parameter value for 'reasoning_effort': 'minimal'."),
    ],
)
async def test_an_unsupported_effort_value_is_reported_without_disabling_the_parameter(
    code, message
):
    config = LLMConfig(
        provider="openai",
        model="gpt-5.6-luna",
        no_thinking_params={"reasoning_effort": "minimal"},
    )
    refusal = httpx.Response(
        400,
        json={
            "error": {
                "code": code,
                "param": "reasoning_effort",
                "message": message,
            }
        },
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    # WHY: the provider's HTTP endpoint rejects a value, not the parameter.
    with patch("httpx.AsyncClient.post", side_effect=[refusal, _reply()]) as post:
        with pytest.raises(httpx.HTTPStatusError, match="minimal"):
            await query_llm("read", config)
        assert post.call_count == 1, "invalid settings must not silently enable default reasoning"
        valid = config.model_copy(update={"no_thinking_params": {"reasoning_effort": "high"}})
        await query_llm("judge", valid)

    assert post.call_args[1]["json"]["reasoning_effort"] == "high"
    assert llm_wire.batch_text_payload(valid, "judge", max_tokens=300)["reasoning_effort"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5-nano"])
async def test_older_openai_models_keep_minimal_effort_and_reasoning_room(model):
    config = LLMConfig(provider="openai", model=model)
    # WHY: older GPT-5 models still require reasoning; Luna's fix must not disable it for them.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("pick", config, max_tokens=300)

    payload = post.call_args[1]["json"]
    assert payload["reasoning_effort"] == "minimal"
    assert payload["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS
    assert llm_wire.batch_text_payload(config, "pick", max_tokens=300) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
async def test_luna_preserves_explicit_bulk_effort_in_live_and_batch(effort):
    config = LLMConfig(
        provider="openai",
        model="gpt-5.6-luna",
        no_thinking_params={"reasoning_effort": effort},
    )
    # WHY: the operator's supported value must win over the model preset on both routes.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("pick", config, max_tokens=300)

    assert post.call_args[1]["json"]["reasoning_effort"] == effort
    expected_tokens = 300 if effort == "none" else 300 + REASONING_HEADROOM_TOKENS
    assert post.call_args[1]["json"]["max_tokens"] == expected_tokens
    assert llm_wire.batch_text_payload(config, "pick", max_tokens=300) == post.call_args[1]["json"]


@pytest.mark.asyncio
async def test_luna_auto_still_leaves_reasoning_to_the_provider():
    config = LLMConfig(provider="openai", model="gpt-5.6-luna", thinking="auto")
    # WHY: auto deliberately delegates reasoning to the provider; it must stay distinct from off.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("pick", config, max_tokens=300)

    payload = post.call_args[1]["json"]
    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 300 + REASONING_HEADROOM_TOKENS
    assert llm_wire.batch_text_payload(config, "pick", max_tokens=300) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "effort", "max_tokens"),
    [
        ({"extra_params": {"reasoning_effort": "high"}}, "high", 300 + REASONING_HEADROOM_TOKENS),
        (
            {
                "no_thinking_params": {"reasoning_effort": "high"},
                "extra_params": {"reasoning_effort": "none"},
            },
            "none",
            300,
        ),
        ({"thinking": "auto", "extra_params": {"reasoning_effort": "none"}}, "none", 300),
        ({"drop_params": ["reasoning_effort"]}, None, 300 + REASONING_HEADROOM_TOKENS),
        (
            {
                "drop_params": ["reasoning_effort"],
                "extra_params": {"reasoning_effort": "none"},
            },
            "none",
            300,
        ),
        ({"always_reasons": True}, "none", 300 + REASONING_HEADROOM_TOKENS),
    ],
)
async def test_luna_budgets_for_the_bulk_effort_that_reaches_the_wire(
    overrides, effort, max_tokens
):
    config = LLMConfig(provider="openai", model="gpt-5.6-luna", **overrides)
    # WHY: provider shaping can replace or remove the preset's off switch before the POST.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("pick", config, max_tokens=300)

    payload = post.call_args[1]["json"]
    assert payload.get("reasoning_effort") == effort
    assert payload["max_tokens"] == max_tokens
    assert llm_wire.batch_text_payload(config, "pick", max_tokens=300) == payload


@pytest.mark.asyncio
async def test_luna_can_still_request_reasoning_with_an_explicit_effort():
    config = LLMConfig(
        provider="openai",
        model="gpt-5.6-luna",
        thinking=True,
        thinking_params={"reasoning_effort": "high"},
    )
    # WHY: the off preset only applies to bulk calls; deliberate reasoning keeps its budget.
    with patch("httpx.AsyncClient.post", return_value=_reply()) as post:
        await query_llm("judge", config, max_tokens=300, thinking=True)

    assert post.call_args[1]["json"]["reasoning_effort"] == "high"
    assert post.call_args[1]["json"]["max_tokens"] == THINKING_MIN_MAX_TOKENS


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


def _ollama(**overrides) -> LLMConfig:
    fields = {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "qwen3",
        "thinking": "disabled",
    }
    fields.update(overrides)
    return LLMConfig(**fields)


def _ollama_reply(*, thinking: str = "", done_reason: str = "stop"):
    # WHY: the Ollama server is the external boundary; what reaches it and how
    # its answer is read is the whole subject.
    reply = AsyncMock()
    reply.status_code = 200
    body: dict = {"response": '{"ok": true}', "done_reason": done_reason, "eval_count": 120}
    if thinking:
        body["thinking"] = thinking
    reply.json = MagicMock(return_value=body)
    reply.raise_for_status = lambda: None
    return reply


@pytest.mark.asyncio
async def test_a_thinking_call_asks_ollama_to_think_and_floors_its_budget():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_ollama_reply()) as post:
        await query_llm("judge this cut", _ollama(thinking="high"), max_tokens=120, thinking=True)

    payload = post.call_args[1]["json"]
    assert payload["think"] is True
    assert payload["options"]["num_predict"] == THINKING_MIN_MAX_TOKENS


@pytest.mark.asyncio
async def test_a_bulk_ollama_call_is_sent_no_reasoning_switch_at_all():
    """A model with no thinking mode answers `think` with a 400, so it is never sent."""
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_ollama_reply()) as post:
        await query_llm("read this sheet", _ollama(), max_tokens=120)

    assert "think" not in post.call_args[1]["json"]


@pytest.mark.asyncio
async def test_an_ollama_reply_that_thought_budgets_for_the_thinking_next_time():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch(
        "httpx.AsyncClient.post", side_effect=[_ollama_reply(thinking="hmm"), _ollama_reply()]
    ) as post:
        await query_llm("first", _ollama(), max_tokens=120)
        await query_llm("second", _ollama(), max_tokens=120)

    first, second = (call[1]["json"] for call in post.call_args_list)
    assert first["options"]["num_predict"] == 120
    assert second["options"]["num_predict"] == 120 + REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
async def test_a_declared_ollama_endpoint_pays_no_first_starved_call():
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_ollama_reply()) as post:
        await query_llm("only", _ollama(always_reasons=True), max_tokens=120)

    assert post.call_args[1]["json"]["options"]["num_predict"] == 120 + REASONING_HEADROOM_TOKENS


@pytest.mark.asyncio
async def test_an_explicit_num_predict_wins_over_the_computed_one():
    """The one lever a user has over an Ollama budget is not silently dropped."""
    config = _ollama(always_reasons=True, extra_params={"options": {"num_predict": 8000}})
    # WHY: the provider's HTTP endpoint is the one boundary these tests replace.
    with patch("httpx.AsyncClient.post", return_value=_ollama_reply()) as post:
        await query_llm("read this sheet", config, max_tokens=120)

    assert post.call_args[1]["json"]["options"]["num_predict"] == 8000
