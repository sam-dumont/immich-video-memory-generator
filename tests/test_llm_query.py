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


class TestQueryLlmWithImages:
    """Pictures go to whichever provider is configured, like the text does."""

    @pytest.mark.asyncio
    async def test_ollama_gets_its_own_endpoint_and_bare_base64(self):
        """The vision path posted OpenAI-style whatever the provider was.

        Every Ollama user therefore 404ed on every day of a scan, and the
        broad except above it logged at DEBUG and called the day ordinary —
        so the scan "succeeded" with an empty catalogue.
        """
        import base64

        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="ollama", base_url="http://localhost:11434/", model="llava")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"response": '{"special": true}'})
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary; the request sent to it is the subject.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("What is this?", config, images=[b"\xff\xd8jpeg"])

        assert result == '{"special": true}'
        assert mock_post.call_args[0][0] == "http://localhost:11434/api/generate"
        payload = mock_post.call_args[1]["json"]
        assert payload["images"] == [base64.b64encode(b"\xff\xd8jpeg").decode()]

    @pytest.mark.asyncio
    async def test_a_trailing_slash_does_not_double_in_the_vision_url(self):
        """The text path strips it; the vision path built the URL by hand."""
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="openai-compatible", base_url="http://localhost:8080/v1/", model="omlx"
        )
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": '{"special": false}'}}]}
        )
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary; the URL built for it is the subject.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await query_llm("What is this?", config, images=[b"\xff\xd8jpeg"])

        assert mock_post.call_args[0][0] == "http://localhost:8080/v1/chat/completions"
        content = mock_post.call_args[1]["json"]["messages"][0]["content"]
        assert content[0]["text"] == "What is this?"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
