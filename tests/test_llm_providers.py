"""Provider adapters: one interface, native dialects behind it."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.analysis.llm_wire import (
    GROWN_REASONING_HEADROOM_TOKENS,
    REASONING_HEADROOM_TOKENS,
)
from immich_memories.config_models_llm import LLMConfig


def _anthropic_blocks(blocks, stop_reason="end_turn"):
    # WHY: the LLM server is the external boundary; these tests assert what
    # reaches it and how its answers are handled, through query_llm only.
    response = AsyncMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"content": blocks, "stop_reason": stop_reason})
    response.raise_for_status = lambda: None
    return response


def _anthropic_response(text='{"ok": true}', stop_reason="end_turn"):
    return _anthropic_blocks([{"type": "text", "text": text}], stop_reason)


class TestAnthropicProvider:
    @pytest.mark.asyncio
    async def test_speaks_the_messages_dialect(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3",
            api_key="k",
        )
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            result = await query_llm("Judge this cut", config, max_tokens=600)

        assert result == '{"ok": true}'
        url = (
            mock_post.call_args[0][0]
            if mock_post.call_args[0]
            else mock_post.call_args[1].get("url")
        )
        assert url.endswith("/v1/messages")
        payload = mock_post.call_args[1]["json"]
        assert payload["max_tokens"] == 600
        assert payload["messages"] == [{"role": "user", "content": "Judge this cut"}]

    @pytest.mark.asyncio
    async def test_thinking_sends_the_hosts_own_switch_and_the_default_temperature(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3",
            api_key="k",
            thinking=True,
        )
        # WHY: the LLM server is the external boundary this request reaches.
        # WHY: inspect the wire payload without calling the hosted Z.AI gateway.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["max_tokens"] >= 4000, "reasoning needs room to finish"
        assert "temperature" not in payload, "thinking requires the default temperature"

    @pytest.mark.asyncio
    async def test_the_older_budget_dialect_is_written_out_by_hand(self):
        """A host still on `enabled` plus a budget is reached without code changes."""
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://gateway.example.invalid/anthropic",
            model="older-claude",
            api_key="k",
            thinking=True,
            thinking_params={"thinking": {"type": "enabled", "budget_tokens": 2048}},
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert payload["max_tokens"] > 2048, "the budget has to fit under the ceiling"

    @pytest.mark.asyncio
    async def test_compatible_gateway_can_explicitly_disable_default_thinking(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
            no_thinking_params={"thinking": {"type": "disabled"}},
        )

        # WHY: inspect the wire payload without calling Anthropic from a unit test.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "disabled"}
        assert "temperature" not in payload, "Claude refuses a sampling parameter outright"

    @pytest.mark.asyncio
    async def test_native_anthropic_does_not_receive_qwen_default_params(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="anthropic", model="claude", api_key="k")

        # WHY: inspect the native Anthropic payload without making a hosted request.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "disabled"}, "bulk calls must not reason"
        assert "chat_template_kwargs" not in payload

    @pytest.mark.asyncio
    async def test_truncated_thinking_falls_back_to_a_fast_answer(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3",
            api_key="k",
            thinking=True,
        )
        truncated = _anthropic_response(text="unfinished reason", stop_reason="max_tokens")
        clean = _anthropic_response(text='{"drop": "B"}')
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[truncated, clean]) as mock_post:
            result = await query_llm("Judge this cut", config, thinking=True)

        assert result == '{"drop": "B"}'
        assert mock_post.call_args_list[1][1]["json"]["thinking"] == {"type": "disabled"}


class TestProviderPresets:
    """provider: openai / zai = the generic adapter plus the provider's dialect."""

    @pytest.mark.asyncio
    async def test_zai_preset_fills_the_anthropic_route_and_its_level(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="zai", model="glm-5.3", api_key="k", thinking=True)

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        assert mock_post.call_args[0][0] == "https://api.z.ai/api/anthropic/v1/messages"
        payload = mock_post.call_args[1]["json"]
        assert payload["thinking"] == {"type": "high"}
        assert "chat_template_kwargs" not in payload

    @pytest.mark.asyncio
    async def test_zai_on_its_openai_route_still_speaks_chat_completions(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-5.3",
            api_key="k",
            thinking=True,
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        url = mock_post.call_args[0][0] if mock_post.call_args[0] else mock_post.call_args[1]["url"]
        assert url == "https://api.z.ai/api/paas/v4/chat/completions"
        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "high"}

    @pytest.mark.asyncio
    async def test_openai_preset_fills_url_and_reasoning_dialect(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="openai", model="gpt-5.6-terra", api_key="k", thinking=True)

        def _ok(url, json):  # noqa: A002
            response = AsyncMock()
            response.status_code = 200
            response.json = MagicMock(
                return_value={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )
            response.raise_for_status = lambda: None
            return response

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_ok) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        url = mock_post.call_args[0][0] if mock_post.call_args[0] else mock_post.call_args[1]["url"]
        assert url.startswith("https://api.openai.com/v1")
        assert mock_post.call_args[1]["json"]["reasoning_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_zai_with_an_anthropic_base_speaks_the_messages_dialect(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            assert await query_llm("Judge this cut", config, max_tokens=600) == '{"ok": true}'

        assert mock_post.call_args[0][0] == "https://api.z.ai/api/anthropic/v1/messages"

    @pytest.mark.asyncio
    async def test_a_200_carrying_a_provider_error_envelope_names_its_code_and_message(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="openai-compatible",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )
        envelope = AsyncMock()
        envelope.status_code = 200
        envelope.json = MagicMock(
            return_value={"code": 500, "msg": "404 NOT_FOUND", "success": False}
        )
        envelope.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", return_value=envelope),
            pytest.raises(ValueError, match="404 NOT_FOUND") as caught,
        ):
            await query_llm("Judge this cut", config, max_tokens=600)

        assert "500" in str(caught.value) and "choices" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_generic_thinking_block_does_not_hide_the_providers_own_switch(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-5.3-flash",
            api_key="k",
            no_thinking_params={
                "chat_template_kwargs": {"enable_thinking": False},
                "repetition_penalty": 1.05,
            },
        )

        def _ok(url, json):  # noqa: A002
            response = AsyncMock()
            response.status_code = 200
            response.json = MagicMock(
                return_value={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )
            response.raise_for_status = lambda: None
            return response

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_ok) as mock_post:
            await query_llm("Judge this cut", config, thinking=False)

        payload = mock_post.call_args[1]["json"]
        assert payload["thinking"] == {"type": "low"}
        assert payload["repetition_penalty"] == 1.05


class TestAnthropicIsAGenericProvider:
    """`provider: anthropic` is Claude's own API and every host that copies it."""

    @pytest.mark.asyncio
    async def test_the_preset_fills_anthropics_own_url_and_pins_the_version(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="k")
        seen: list[tuple] = []

        # WHY: the LLM server is the external boundary; autospec hands the test
        # the client that would have made the request, headers and all.
        async def _post(client, url, json=None):  # noqa: A002
            seen.append((dict(client.headers), url, json))
            return _anthropic_response()

        with patch("httpx.AsyncClient.post", autospec=True, side_effect=_post):
            await query_llm("Judge this cut", config, max_tokens=600)

        headers, url, payload = seen[0]
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["x-api-key"] == "k"
        assert payload["model"] == "claude-sonnet-4-5"
        assert "temperature" not in payload, "Claude refuses a sampling parameter outright"

    @pytest.mark.asyncio
    async def test_any_messages_api_host_is_reached_by_naming_its_base_url(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://gateway.example.invalid/anthropic/",
            model="some-model",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, max_tokens=600)

        assert mock_post.call_args[0][0] == "https://gateway.example.invalid/anthropic/v1/messages"

    @pytest.mark.asyncio
    async def test_the_reader_sends_its_tiles_as_base64_blocks(self):
        import base64

        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="k")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Describe these", config, images=[b"\xff\xd8one", b"\xff\xd8two"])

        content = mock_post.call_args.kwargs["json"]["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "Describe these"}
        assert [base64.b64decode(block["source"]["data"]) for block in content[1:]] == [
            b"\xff\xd8one",
            b"\xff\xd8two",
        ]
        assert {block["source"]["media_type"] for block in content[1:]} == {"image/jpeg"}


class TestThinkingIsALevel:
    """One control, mapped to what each host takes: an effort here, a level there."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("level", ["low", "high", "max"])
    async def test_a_level_reaches_claude_as_the_effort_it_maps_to(self, level: str):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic", model="claude-sonnet-4-5", api_key="k", thinking=level
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"] == {"effort": level}

    @pytest.mark.asyncio
    async def test_auto_sends_no_reasoning_field_in_either_direction(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic", model="claude-sonnet-4-5", api_key="k", thinking="auto"
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)
            asked = mock_post.call_args.kwargs["json"]
            await query_llm("Describe this wall", config, thinking=False)
            bulk = mock_post.call_args.kwargs["json"]

        assert "thinking" not in asked
        assert "thinking" not in bulk

    @pytest.mark.asyncio
    async def test_auto_leaves_a_chat_completions_server_to_its_own_default(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="openai-compatible",
            base_url="http://localhost:8080/v1",
            model="qwen",
            thinking="auto",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert "chat_template_kwargs" not in mock_post.call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_disabled_refuses_to_reason_even_where_the_caller_asks(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic", model="claude-sonnet-4-5", api_key="k", thinking="disabled"
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, max_tokens=140, thinking=True)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "disabled"}
        assert "output_config" not in payload, "nothing to spend effort on"
        assert payload["max_tokens"] == 140, "a host told not to reason keeps the caller's cap"

    @pytest.mark.asyncio
    async def test_zai_gets_its_own_vocabulary_for_the_same_level(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="zai", model="glm-5.3", api_key="k", thinking="max")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        assert mock_post.call_args.kwargs["json"]["thinking"] == {"type": "max"}

    def test_the_switch_this_field_used_to_be_is_still_read(self):
        assert LLMConfig(thinking=True).thinking == "high"
        assert LLMConfig(thinking=False).thinking == "disabled"
        assert LLMConfig(thinking="true").reasons
        assert not LLMConfig(thinking="false").reasons


def _completion(url=None, json=None):  # noqa: A002
    # WHY: the LLM server is the external boundary; the fake stands in for a
    # plain accepted completion so the test can read what was posted to it.
    response = AsyncMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    )
    response.raise_for_status = lambda: None
    return response


class TestZaiThinkingLevel:
    """z.ai's switch is a level (disabled | low | high | max), not a boolean."""

    @pytest.mark.asyncio
    async def test_a_model_that_always_reasons_gets_the_cheapest_level_it_accepts(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-5.3-flash",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "low"}

    @pytest.mark.asyncio
    async def test_a_model_that_can_be_told_not_to_reason_still_gets_disabled(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-4.6",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_a_level_the_operator_names_wins_over_the_preset(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-5.3-flash",
            api_key="k",
            no_thinking_params={"thinking": {"type": "high"}},
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "high"}

    @pytest.mark.asyncio
    async def test_a_refusal_to_stop_reasoning_retries_once_at_the_lowest_level(self, caplog):
        import logging

        from immich_memories.analysis.llm_query import query_llm

        # A line the preset has never heard of, so it starts from "disabled".
        # A model name of its own too: the dialect a call negotiates is
        # remembered for the rest of the process, per server and model.
        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-6.1-unreleased",
            api_key="k",
        )
        refusal = httpx.Response(
            400,
            json={
                "error": {
                    "code": "1210",
                    "message": (
                        "This model always engages in thinking and cannot be "
                        "disabled; please use low, high, or max"
                    ),
                }
            },
            request=httpx.Request("POST", "https://api.z.ai/api/paas/v4/chat/completions"),
        )
        posted: list[dict] = []

        async def _refuse_then_answer(url, json):  # noqa: A002
            posted.append(dict(json))
            return refusal if len(posted) == 1 else _completion()

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", side_effect=_refuse_then_answer),
            caplog.at_level(logging.WARNING, logger="immich_memories.analysis.llm_query"),
        ):
            assert await query_llm("Describe this wall", config, thinking=False) == "ok"

        assert posted[0]["thinking"] == {"type": "disabled"}
        assert posted[1]["thinking"] == {"type": "low"}
        assert "1210" in caplog.text and "low" in caplog.text


class TestProviderErrorsAreLegible:
    """httpx names the status; only the body says why the call was refused."""

    @pytest.mark.asyncio
    async def test_a_4xx_carries_the_providers_code_and_message(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="zai", model="glm-5.3-flash", api_key="k")
        refused = httpx.Response(
            429,
            json={"error": {"code": "1113", "message": "Insufficient balance"}},
            request=httpx.Request("POST", "https://api.z.ai/api/paas/v4/chat/completions"),
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", return_value=refused),
            pytest.raises(httpx.HTTPStatusError) as caught,
        ):
            await query_llm("Describe this wall", config)

        assert "1113" in str(caught.value) and "Insufficient balance" in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_messages_dialect_reports_its_refusals_the_same_way(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )
        refused = httpx.Response(
            400,
            json={"error": {"code": "1210", "message": "cannot be disabled"}},
            request=httpx.Request("POST", "https://api.z.ai/api/anthropic/v1/messages"),
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", return_value=refused),
            pytest.raises(httpx.HTTPStatusError) as caught,
        ):
            await query_llm("Describe this wall", config)

        assert "1210" in str(caught.value) and "cannot be disabled" in str(caught.value)

    @pytest.mark.asyncio
    async def test_ollama_names_its_refusal_too(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="ollama", base_url="http://localhost:11434", model="qwen3")
        refused = httpx.Response(
            404,
            json={"error": 'model "qwen3" not found, try pulling it first'},
            request=httpx.Request("POST", "http://localhost:11434/api/generate"),
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", return_value=refused),
            pytest.raises(httpx.HTTPStatusError) as caught,
        ):
            await query_llm("Describe this wall", config)

        assert "try pulling it first" in str(caught.value)


class TestAnthropicReasoningBlocks:
    """z.ai's /api/anthropic route reasons in front of the answer, on its own terms."""

    @pytest.mark.asyncio
    async def test_a_reply_that_is_all_reasoning_names_the_stop_reason(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary. This is the body z.ai
        # returned to a caption-shaped ask at a 140-token cap on 2026-09-14:
        # the whole budget went into the reasoning and no answer ever began.
        with (
            patch(
                "httpx.AsyncClient.post",
                return_value=_anthropic_blocks(
                    [{"type": "thinking", "thinking": "The user wants", "signature": "s"}],
                    stop_reason="max_tokens",
                ),
            ),
            pytest.raises(ValueError, match="max_tokens"),
        ):
            await query_llm("Caption this picture", config, max_tokens=140)

    @pytest.mark.asyncio
    async def test_the_callers_cap_buys_the_answer_and_not_the_reasoning(self):
        from immich_memories.analysis.llm_query import query_llm
        from immich_memories.analysis.llm_wire import REASONING_HEADROOM_TOKENS

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Caption this picture", config, max_tokens=140)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 140 + REASONING_HEADROOM_TOKENS

    @pytest.mark.asyncio
    async def test_the_reasoning_in_front_of_the_answer_is_skipped(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )
        reasoned = _anthropic_blocks(
            [
                {"type": "thinking", "thinking": "The user wants JSON", "signature": "s"},
                {"type": "text", "text": '{"ok": true}'},
            ]
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=reasoned):
            assert await query_llm("Caption this picture", config, max_tokens=140) == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_a_server_that_reasons_only_when_asked_keeps_the_callers_cap(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="anthropic", model="claude", api_key="k")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Caption this picture", config, max_tokens=140)

        assert mock_post.call_args.kwargs["json"]["max_tokens"] == 140

    @pytest.mark.asyncio
    async def test_the_models_level_reaches_the_route_without_the_generic_keys(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
            no_thinking_params={
                "chat_template_kwargs": {"enable_thinking": False},
                "repetition_penalty": 1.05,
            },
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Caption this picture", config, max_tokens=140)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "low"}
        assert "chat_template_kwargs" not in payload
        assert "repetition_penalty" not in payload

    @pytest.mark.asyncio
    async def test_reasoning_that_truncates_still_falls_back_to_a_fast_answer(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
            thinking=True,
        )
        all_reasoning = _anthropic_blocks(
            [{"type": "thinking", "thinking": "still weighing", "signature": "s"}],
            stop_reason="max_tokens",
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post", side_effect=[all_reasoning, _anthropic_response()]
        ) as mock_post:
            assert await query_llm("Judge this cut", config, thinking=True) == '{"ok": true}'

        assert mock_post.call_args_list[1].kwargs["json"]["thinking"] == {"type": "low"}


_LIVE = pytest.mark.skipif(
    not (os.environ.get("ZAI_API_KEY") and os.environ.get("ZAI_BASE_URL")),
    reason="needs a z.ai coding-plan key and its Anthropic base; absent in CI",
)


class TestAnthropicImageCallsGetReasoningRoom:
    """Measured 2026-09-14 on z.ai's /api/anthropic with glm-5.3-flash.

    Six probe shapes answered `end_turn` with a text block; the one carrying a
    picture spent its whole 64-token budget inside the thinking block and came
    back with `stop_reason max_tokens` and blocks ['thinking']. That is the
    reasoning-budget defect #968 fixed on the OpenAI dialects, on the one route
    it did not reach.
    """

    @staticmethod
    def _config():
        return LLMConfig(
            provider="zai",
            base_url="https://api.z.ai/api/anthropic",
            model="glm-5.3-flash",
            api_key="k",
        )

    @pytest.mark.asyncio
    async def test_a_picture_that_bought_only_thinking_is_asked_again_with_room(self):
        from immich_memories.analysis.llm_query import query_llm

        starved = _anthropic_blocks(
            [{"type": "thinking", "thinking": "Looking at the image", "signature": "s"}],
            stop_reason="max_tokens",
        )
        starved.json = MagicMock(
            return_value={
                "content": [{"type": "thinking", "thinking": "Looking", "signature": "s"}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 12, "output_tokens": 64},
            }
        )
        answered = _anthropic_response('{"subject_action": "a dock"}')

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[starved, answered]) as mock_post:
            result = await query_llm(
                "Inspect this one image", self._config(), max_tokens=64, images=(b"jpegbytes",)
            )

        assert result == '{"subject_action": "a dock"}'
        first, second = (call.kwargs["json"] for call in mock_post.call_args_list)
        assert first["max_tokens"] == 64 + REASONING_HEADROOM_TOKENS
        assert second["max_tokens"] == 64 + GROWN_REASONING_HEADROOM_TOKENS

    @pytest.mark.asyncio
    async def test_a_reply_that_thought_teaches_the_endpoint_even_when_it_answered(self):
        from immich_memories.analysis.llm_query import query_llm
        from immich_memories.analysis.llm_wire import _REASONING_HEADROOM

        config = LLMConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="k")
        reasoned = _anthropic_blocks(
            [
                {"type": "thinking", "thinking": "Considering", "signature": "s"},
                {"type": "text", "text": "Blue"},
            ]
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=reasoned):
            assert await query_llm("What colour", config, max_tokens=140) == "Blue"

        # A host told `disabled` that thinks anyway has said so; the next call pays for it.
        assert _REASONING_HEADROOM[("https://api.anthropic.com", "claude-sonnet-4-5")] == (
            REASONING_HEADROOM_TOKENS
        )


class TestLiveAnthropicCompatibleHost:
    """Real calls, so a change of shape at the host's end fails here and not in a run."""

    @_LIVE
    @pytest.mark.asyncio
    async def test_the_generic_provider_reaches_a_compatible_host(self):
        """`provider: anthropic` plus somebody else's base URL is the whole promise."""
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="anthropic",
            base_url=os.environ["ZAI_BASE_URL"],
            model="glm-5.3-flash",
            api_key="${ZAI_API_KEY}",
            timeout_seconds=120,
        )

        answer = await query_llm(
            'Reply with only this JSON object and nothing else: {"ok": true}',
            config,
            max_tokens=140,
            timeout_seconds=120,
        )

        assert json.loads(answer.strip()) == {"ok": True}

    @_LIVE
    @pytest.mark.asyncio
    async def test_a_small_json_ask_comes_back_as_a_text_block(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
            base_url=os.environ["ZAI_BASE_URL"],
            model="glm-5.3-flash",
            api_key="${ZAI_API_KEY}",
            timeout_seconds=120,
        )

        answer = await query_llm(
            'Reply with only this JSON object and nothing else: {"ok": true}',
            config,
            max_tokens=140,
            timeout_seconds=120,
        )

        # The failure this guards is an empty answer: every token spent on
        # reasoning and no text block behind it.
        assert answer.strip()
        assert "ok" in answer
