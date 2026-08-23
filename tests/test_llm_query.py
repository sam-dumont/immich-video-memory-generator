"""Tests for generic LLM text query utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_models import LLMConfig


class TestQueryLlmOllama:
    """Ollama provider: text-only query."""

    @pytest.mark.asyncio
    async def test_sends_text_prompt_to_ollama(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="ollama", base_url="http://localhost:11434", model="llama3")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"response": '{"title": "Summer 2024"}'})
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("Generate a title", config)

        assert result == '{"title": "Summer 2024"}'
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["prompt"] == "Generate a title"
        assert call_payload["model"] == "llama3"
        assert "images" not in call_payload


class TestQueryLlmOpenAI:
    """OpenAI-compatible provider: text-only query."""

    @pytest.mark.asyncio
    async def test_sends_text_prompt_to_openai(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="openai-compatible",
            base_url="http://localhost:8080/v1",
            model="omlx",
            api_key="sk-test",
        )
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={
                "choices": [{"message": {"content": '{"title": "Cycling 2024"}'}}],
            }
        )
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("Generate a title", config)

        assert result == '{"title": "Cycling 2024"}'
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["messages"][0]["content"] == "Generate a title"
        assert call_payload["model"] == "omlx"


def _openai_response(content='{"ok": true}', finish_reason="stop"):
    # WHY: the LLM server is the external boundary; these tests assert what
    # reaches it and how its answers are handled, through query_llm only.
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(
        return_value={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
        }
    )
    mock_response.raise_for_status = lambda: None
    return mock_response


def _thinking_config(**overrides) -> LLMConfig:
    fields = {
        "provider": "openai-compatible",
        "base_url": "http://localhost:8080/v1",
        "model": "qwen-reasoning",
        "thinking": True,
    }
    fields.update(overrides)
    return LLMConfig(**fields)


class TestThinkingMode:
    """Load-bearing calls may ask a reasoning model to actually reason."""

    @pytest.mark.asyncio
    async def test_thinking_call_carries_the_template_switch(self):
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", _thinking_config(), thinking=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": True}

    @pytest.mark.asyncio
    async def test_config_off_keeps_the_payload_clean_for_any_server(self):
        """A call may opt in, but only the config knows the server accepts the kwarg."""
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", _thinking_config(thinking=False), thinking=True)

        assert "chat_template_kwargs" not in mock_post.call_args[1]["json"]

    @pytest.mark.asyncio
    async def test_ollama_path_ignores_thinking(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="ollama", base_url="http://localhost:11434", model="llama3", thinking=True
        )
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"response": "ok"})
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        assert "chat_template_kwargs" not in mock_post.call_args[1]["json"]

    @pytest.mark.asyncio
    async def test_thinking_floors_the_token_budget(self):
        """Measured: 500 tokens truncates mid-think and reasoning leaks into content."""
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", _thinking_config(), max_tokens=500, thinking=True)

        assert mock_post.call_args[1]["json"]["max_tokens"] >= 4000

    @pytest.mark.asyncio
    async def test_truncated_thinking_falls_back_to_a_fast_answer(self):
        """Measured: finish=length under thinking means the content IS the
        unfinished reasoning — never parseable. The call must retry without
        thinking and return the fast answer instead."""
        from immich_memories.analysis.llm_query import query_llm

        contaminated = _openai_response(
            content="Here's a thinking process:\n1. Analyze...", finish_reason="length"
        )
        clean = _openai_response(content='{"drop": "B"}')

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[contaminated, clean]) as mock_post:
            result = await query_llm("Judge this cut", _thinking_config(), thinking=True)

        assert result == '{"drop": "B"}'
        retry_payload = mock_post.call_args_list[1][1]["json"]
        assert "chat_template_kwargs" not in retry_payload

    @pytest.mark.asyncio
    async def test_thinking_params_are_configurable_per_server(self):
        """Not every OpenAI-compatible server speaks Qwen's dialect — the
        payload addition is whatever the config says (e.g. reasoning_effort)."""
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking_params={"reasoning_effort": "medium"})
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", config, thinking=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["reasoning_effort"] == "medium"
        assert "chat_template_kwargs" not in payload
