"""Provider adapters: one interface, native dialects behind it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
