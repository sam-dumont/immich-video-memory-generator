"""Provider adapters: one interface, native dialects behind it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.config_models_llm import LLMConfig


def _anthropic_response(text='{"ok": true}', stop_reason="end_turn"):
    # WHY: the LLM server is the external boundary; these tests assert what
    # reaches it and how its answers are handled, through query_llm only.
    response = AsyncMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
        }
    )
    response.raise_for_status = lambda: None
    return response


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
    async def test_thinking_uses_the_native_budget_and_default_temperature(self):
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
        assert payload["thinking"]["type"] == "enabled"
        assert payload["thinking"]["budget_tokens"] < payload["max_tokens"]
        assert "temperature" not in payload, "thinking requires the default temperature"

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
        assert payload["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_native_anthropic_does_not_receive_qwen_default_params(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="anthropic", model="claude", api_key="k")

        # WHY: inspect the native Anthropic payload without making a hosted request.
        with patch("httpx.AsyncClient.post", return_value=_anthropic_response()) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        payload = mock_post.call_args.kwargs["json"]
        assert "thinking" not in payload
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
        assert "thinking" not in mock_post.call_args_list[1][1]["json"]


class TestProviderPresets:
    """provider: openai / zai = the generic adapter plus the provider's dialect."""

    @pytest.mark.asyncio
    async def test_zai_preset_fills_url_and_thinking_dialect(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="zai", model="glm-5.3", api_key="k", thinking=True)

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
        assert url.startswith("https://api.z.ai/api/paas/v4")
        payload = mock_post.call_args[1]["json"]
        assert payload["thinking"] == {"type": "enabled"}
        assert "chat_template_kwargs" not in payload

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

        config = LLMConfig(provider="zai", model="glm-5.3-flash", api_key="k")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "low"}

    @pytest.mark.asyncio
    async def test_a_model_that_can_be_told_not_to_reason_still_gets_disabled(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="zai", model="glm-4.6", api_key="k")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=_completion) as mock_post:
            await query_llm("Describe this wall", config, thinking=False)

        assert mock_post.call_args[1]["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_a_level_the_operator_names_wins_over_the_preset(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="zai",
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
        config = LLMConfig(provider="zai", model="glm-6.1-unreleased", api_key="k")
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
