"""Tests for generic LLM text query utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_models_llm import LLMConfig


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


def _openai_400(message):
    # WHY: the LLM server is the external boundary; this fakes its 400 contract.
    import httpx

    response = AsyncMock()
    response.status_code = 400
    response.json = MagicMock(return_value={"error": {"message": message}})
    request = httpx.Request("POST", "http://localhost/v1/chat/completions")
    http_response = httpx.Response(400, request=request, json={"error": {"message": message}})
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(message, request=request, response=http_response)
    )
    return response


class TestServerParameterDialects:
    """Measured on real OpenAI: gpt-5 models reject max_tokens (want
    max_completion_tokens) and any non-default temperature. The 400 bodies
    name the offending parameter, so the call adapts and remembers."""

    def setup_method(self):
        from immich_memories.analysis import llm_query

        llm_query._PARAM_ADAPTATIONS.clear()

    @pytest.mark.asyncio
    async def test_max_tokens_rejection_adapts_and_retries(self):
        from immich_memories.analysis.llm_query import query_llm

        rejected = _openai_400(
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead."
        )
        ok = _openai_response(content='{"drop": "B"}')
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[rejected, ok]) as mock_post:
            result = await query_llm("Judge this cut", _thinking_config(thinking=False))

        assert result == '{"drop": "B"}'
        retry = mock_post.call_args_list[1][1]["json"]
        assert "max_tokens" not in retry
        assert retry["max_completion_tokens"] == 500

    @pytest.mark.asyncio
    async def test_temperature_rejection_drops_it_and_retries(self):
        from immich_memories.analysis.llm_query import query_llm

        rejected = _openai_400(
            "Unsupported value: 'temperature' does not support 0.3 with this model. "
            "Only the default (1) value is supported."
        )
        ok = _openai_response(content='{"drop": "B"}')
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[rejected, ok]) as mock_post:
            result = await query_llm("Judge this cut", _thinking_config(thinking=False))

        assert result == '{"drop": "B"}'
        assert "temperature" not in mock_post.call_args_list[1][1]["json"]

    @pytest.mark.asyncio
    async def test_adaptation_is_remembered_for_the_next_call(self):
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking=False)
        rejected = _openai_400(
            "'max_tokens' is not supported... Use 'max_completion_tokens' instead."
        )
        ok1, ok2 = _openai_response(), _openai_response()
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", side_effect=[rejected, ok1, ok2]) as mock_post:
            await query_llm("first", config)
            await query_llm("second", config)

        assert mock_post.call_count == 3
        second_call = mock_post.call_args_list[2][1]["json"]
        assert "max_completion_tokens" in second_call and "max_tokens" not in second_call

    @pytest.mark.asyncio
    async def test_an_unrelated_400_still_raises(self):
        import httpx

        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with (
            patch("httpx.AsyncClient.post", return_value=_openai_400("Invalid API key provided")),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await query_llm("Judge this cut", _thinking_config(thinking=False))


class TestConfigurableRequestShape:
    """A provider's dialect can be declared up front instead of negotiated."""

    @pytest.mark.asyncio
    async def test_token_param_name_is_configurable(self):
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking=False, max_tokens_param="max_completion_tokens")
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", config, max_tokens=700)

        payload = mock_post.call_args[1]["json"]
        assert payload["max_completion_tokens"] == 700
        assert "max_tokens" not in payload

    @pytest.mark.asyncio
    async def test_drop_params_removes_fields_the_server_rejects(self):
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking=False, drop_params=["temperature"])
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", config)

        assert "temperature" not in mock_post.call_args[1]["json"]

    @pytest.mark.asyncio
    async def test_extra_params_are_merged_into_every_request(self):
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking=False, extra_params={"do_sample": False})
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", config)

        assert mock_post.call_args[1]["json"]["do_sample"] is False


class TestTimeoutShape:
    """A stuck server must fail while connecting, not hold the read budget.

    The scalar form gave connecting the whole generation budget — the
    documented stall-class bug (a one-hour read budget applied to connect)."""

    @pytest.mark.asyncio
    async def test_openai_client_gets_a_per_phase_timeout(self):
        import httpx

        from immich_memories.analysis.llm_query import CONNECT_TIMEOUT_SECONDS, query_llm

        # WHY: httpx is the transport boundary; asserting the timeout shape handed to it.
        with patch("httpx.AsyncClient", autospec=True) as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_openai_response()
            )
            await query_llm(
                "Judge this cut", _thinking_config(thinking=False), timeout_seconds=3600
            )

        timeout = mock_client.call_args[1]["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == CONNECT_TIMEOUT_SECONDS
        assert timeout.read == 3600

    @pytest.mark.asyncio
    async def test_anthropic_client_gets_a_per_phase_timeout(self):
        import httpx

        from immich_memories.analysis.llm_query import CONNECT_TIMEOUT_SECONDS, query_llm

        config = LLMConfig(provider="anthropic", model="m", api_key="k")
        anthropic_ok = AsyncMock()
        anthropic_ok.status_code = 200
        anthropic_ok.json = MagicMock(
            return_value={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
        )
        anthropic_ok.raise_for_status = lambda: None
        # WHY: httpx is the transport boundary; asserting the timeout shape handed to it.
        with patch("httpx.AsyncClient", autospec=True) as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=anthropic_ok
            )
            await query_llm("Judge this cut", config, timeout_seconds=3600)

        timeout = mock_client.call_args[1]["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == CONNECT_TIMEOUT_SECONDS
