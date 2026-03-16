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

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("Generate a title", config)

        assert result == '{"title": "Cycling 2024"}'
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["messages"][0]["content"] == "Generate a title"
        assert call_payload["model"] == "omlx"
