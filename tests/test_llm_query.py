"""Tests for generic LLM text query utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis.llm_query import THINKING_MIN_MAX_TOKENS
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

    @pytest.mark.asyncio
    async def test_extra_params_reach_ollama_without_losing_its_options(self):
        """Ollama keeps num_ctx and friends under `options`, beside temperature."""
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(
            provider="ollama",
            base_url="http://localhost:11434",
            model="llava",
            extra_params={"format": "json", "options": {"num_ctx": 8192}},
        )
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"response": "{}"})
        mock_response.raise_for_status = lambda: None

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await query_llm("Generate a title", config)

        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["format"] == "json"
        assert call_payload["options"] == {"temperature": 0.3, "num_ctx": 8192}


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
    async def test_config_off_refuses_a_calls_request_to_think(self):
        """A call may opt in; the config decides, and the server is told.

        `thinking` says whether to reason, `no_thinking_params` says whether
        the server speaks this dialect — they used to be the same field, which
        left a config with reasoning off silently reasoning anyway.
        """
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", _thinking_config(thinking=False), thinking=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["max_tokens"] < THINKING_MIN_MAX_TOKENS

    @pytest.mark.asyncio
    async def test_a_server_that_does_not_speak_the_dialect_stays_clean(self):
        """Servers that reason only when asked want neither switch."""
        from immich_memories.analysis.llm_query import query_llm

        config = _thinking_config(thinking=False, no_thinking_params={})
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Judge this cut", config)

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
        # The retry exists to get a fast answer, so it must say so. Dropping
        # the switch is not enough on a server that reasons by default: the
        # retry would reason again and truncate again.
        retry_payload = mock_post.call_args_list[1][1]["json"]
        assert retry_payload["chat_template_kwargs"] == {"enable_thinking": False}

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


class TestBulkCallsDoNotThink:
    """A server that thinks by default thinks on every call it is not told to skip.

    Measured 2026-08-24 against the live endpoint after server-side thinking
    was switched on: the photo prompt at its 500-token budget came back
    finish_reason=length, 1800 chars of unterminated reasoning and no JSON,
    3 times out of 3. The same prompt with the switch sent off parsed 3/3 in
    1.5s. Omitting the enable switch is not the same as disabling it.
    """

    @pytest.mark.asyncio
    async def test_a_bulk_call_asks_a_thinking_server_not_to_think(self):
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Describe this photo", _thinking_config(), max_tokens=500)

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    @pytest.mark.asyncio
    async def test_reasoning_turned_off_still_tells_the_server_so(self):
        """llm.thinking: false says "don't reason", not "this server won't".

        A user who turns reasoning off on a server whose template reasons by
        default gets the same truncated bulk calls, so the off-switch hangs
        off the switch itself rather than off the reasoning setting.
        """
        from immich_memories.analysis.llm_query import query_llm

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response()) as mock_post:
            await query_llm("Describe this photo", _thinking_config(thinking=False))

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    @pytest.mark.asyncio
    async def test_a_server_that_rejects_the_switch_is_asked_without_it(self):
        """Sending the kwarg by default is only safe if a refusal adapts."""
        import httpx

        from immich_memories.analysis.llm_query import query_llm

        refusal = MagicMock(status_code=400)
        refusal.json = MagicMock(
            return_value={
                "error": {"message": "Unrecognized request argument: chat_template_kwargs"}
            }
        )
        refusal.text = "Unrecognized request argument: chat_template_kwargs"
        rejected = AsyncMock()
        rejected.status_code = 400
        rejected.json = refusal.json
        rejected.text = refusal.text
        rejected.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("400", request=MagicMock(), response=refusal)
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post", side_effect=[rejected, _openai_response()]
        ) as mock_post:
            await query_llm("Describe this photo", _thinking_config(model="picky"))

        assert "chat_template_kwargs" not in mock_post.call_args_list[-1][1]["json"]


@pytest.mark.asyncio
async def test_transport_observer_records_each_null_content_wire_retry() -> None:
    from immich_memories.analysis.llm_query import query_llm

    attempts = []
    responses = [_openai_response(content=None), _openai_response(content=None), _openai_response()]
    # WHY: the LLM server is the external boundary; null-content retries happen at its response.
    with patch("httpx.AsyncClient.post", side_effect=responses):
        await query_llm(
            "Describe this",
            _thinking_config(thinking=False),
            transport_observer=attempts.append,
        )

    assert [(attempt.attempt, attempt.outcome, attempt.status_code) for attempt in attempts] == [
        (1, "null_content", 200),
        (2, "null_content", 200),
        (3, "response", 200),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "anthropic"])
async def test_transport_observer_records_provider_http_failures(provider: str) -> None:
    import httpx

    from immich_memories.analysis.llm_query import query_llm

    response = MagicMock(status_code=503)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=response
    )
    attempts = []
    config = LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision")

    # WHY: a provider's rejected HTTP response is still one real wire attempt.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await query_llm("look", config, transport_observer=attempts.append)

    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "http_error", 503)
    ]


@pytest.mark.asyncio
async def test_transport_observer_records_openai_adaptation_and_success() -> None:
    from immich_memories.analysis.llm_query import query_llm

    rejected = AsyncMock(status_code=400)
    rejected.json = MagicMock(
        return_value={"error": {"message": "Unrecognized request argument: chat_template_kwargs"}}
    )
    attempts = []

    # WHY: the compatibility retry posts twice, once rejected and once accepted.
    with (
        patch("httpx.AsyncClient.post", side_effect=[rejected, _openai_response()]),
        patch("immich_memories.analysis.llm_query._PARAM_ADAPTATIONS", {}),
    ):
        await query_llm("look", _thinking_config(), transport_observer=attempts.append)

    assert [
        (event.attempt, event.outcome, event.status_code, event.adaptation) for event in attempts
    ] == [(1, "dialect_adaptation", 400, "no_chat_template_kwargs"), (2, "response", 200, None)]


@pytest.mark.asyncio
async def test_transport_observer_records_connection_errors() -> None:
    import httpx

    from immich_memories.analysis.llm_query import query_llm

    attempts = []
    # WHY: no response object exists when the wire connection itself fails.
    with (
        patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("offline")),
        pytest.raises(httpx.ConnectError),
    ):
        await query_llm("look", _thinking_config(), transport_observer=attempts.append)

    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "connection_error", None)
    ]


@pytest.mark.asyncio
async def test_transport_observer_survives_thinking_fallback() -> None:
    from immich_memories.analysis.llm_query import query_llm

    attempts = []
    # WHY: the fallback owns a new request but must retain the original observer.
    with patch(
        "httpx.AsyncClient.post",
        side_effect=[_openai_response(finish_reason="length"), _openai_response()],
    ):
        await query_llm(
            "judge", _thinking_config(), thinking=True, transport_observer=attempts.append
        )

    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "thinking_fallback", 200),
        (2, "response", 200),
    ]


@pytest.mark.asyncio
async def test_transport_observers_are_isolated_across_concurrent_queries() -> None:
    import asyncio

    from immich_memories.analysis.llm_query import query_llm

    first: list = []
    second: list = []
    # WHY: per-query attempt numbers must not leak between concurrent callers.
    with patch("httpx.AsyncClient.post", return_value=_openai_response()):
        await asyncio.gather(
            query_llm("first", _thinking_config(), transport_observer=first.append),
            query_llm("second", _thinking_config(), transport_observer=second.append),
        )

    assert [(event.attempt, event.outcome) for event in first] == [(1, "response")]
    assert [(event.attempt, event.outcome) for event in second] == [(1, "response")]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "anthropic", "openai-compatible"])
async def test_each_provider_payload_carries_the_exact_jpeg_bytes(provider: str) -> None:
    import base64

    from immich_memories.analysis.llm_query import query_llm

    image = b"exact-contact-sheet"
    config = LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision")
    response = _openai_response()
    if provider == "ollama":
        response.json = MagicMock(return_value={"response": "ok"})
    elif provider == "anthropic":
        response.json = MagicMock(return_value={"content": [{"type": "text", "text": "ok"}]})

    # WHY: the provider is the external boundary; this decodes its real dialect payload.
    with patch("httpx.AsyncClient.post", return_value=response) as post:
        await query_llm("look", config, images=(image,))

    payload = post.call_args.kwargs["json"]
    if provider == "ollama":
        encoded = payload["images"][0]
    elif provider == "anthropic":
        encoded = payload["messages"][0]["content"][0]["source"]["data"]
    else:
        encoded = payload["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == image


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("ollama", {"response": "partial", "done_reason": "length"}),
        (
            "anthropic",
            {"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens"},
        ),
        (
            "openai-compatible",
            {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
        ),
    ],
)
async def test_visual_completion_mode_rejects_known_truncation(provider: str, body: dict) -> None:
    from immich_memories.analysis.llm_query import query_llm

    response = AsyncMock(status_code=200)
    response.json = MagicMock(return_value=body)
    response.raise_for_status = lambda: None
    attempts = []
    # WHY: the provider response status is the external completion boundary.
    with patch("httpx.AsyncClient.post", return_value=response), pytest.raises(ValueError):
        await query_llm(
            "look",
            LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision"),
            images=(b"jpeg",),
            require_complete=True,
            transport_observer=attempts.append,
        )
    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "incomplete", 200)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "anthropic", "openai-compatible"])
async def test_transport_observer_records_invalid_json_for_each_provider(provider: str) -> None:
    from immich_memories.analysis.llm_query import query_llm

    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    attempts = []

    # WHY: a successful POST with unparseable provider content still costs one call.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        pytest.raises(ValueError, match="not json"),
    ):
        await query_llm(
            "look",
            LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision"),
            transport_observer=attempts.append,
        )

    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "invalid_response", 200)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("ollama", {"response": []}),
        ("anthropic", {"content": "not blocks"}),
        ("openai-compatible", {"choices": "not choices"}),
    ],
)
async def test_transport_observer_records_invalid_shape_for_each_provider(
    provider: str, body: dict
) -> None:
    from immich_memories.analysis.llm_query import query_llm

    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = body
    attempts = []

    # WHY: a syntactically valid but unusable provider body also spent one wire attempt.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        pytest.raises((KeyError, TypeError, AttributeError)),
    ):
        await query_llm(
            "look",
            LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision"),
            transport_observer=attempts.append,
        )

    assert [(event.attempt, event.outcome, event.status_code) for event in attempts] == [
        (1, "invalid_response", 200)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "body", "usage"),
    [
        ("ollama", {"prompt_eval_count": 3, "eval_count": 5, "response": []}, (3, 5)),
        (
            "anthropic",
            {"usage": {"input_tokens": 3, "output_tokens": 5}, "content": "not blocks"},
            (3, 5),
        ),
        (
            "openai-compatible",
            {
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                "choices": [{"message": {}}],
            },
            (3, 5),
        ),
    ],
)
async def test_parseable_malformed_content_keeps_legacy_reply_metrics(
    provider: str, body: dict, usage: tuple[int, int]
) -> None:
    from immich_memories.analysis.llm_query import query_llm

    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = body

    # WHY: content may be malformed after the provider has supplied billable usage.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        patch("immich_memories.analysis.llm_query.llm_metrics.record_reply") as record_reply,
        pytest.raises((KeyError, TypeError, AttributeError)),
    ):
        await query_llm(
            "look", LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision")
        )

    assert record_reply.call_args.kwargs == {
        "prompt_tokens": usage[0],
        "completion_tokens": usage[1],
    }
    assert record_reply.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "anthropic", "openai-compatible"])
async def test_invalid_json_does_not_create_legacy_reply_metrics(provider: str) -> None:
    from immich_memories.analysis.llm_query import query_llm

    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")

    # WHY: a body that cannot be decoded has no trustworthy usage to count.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        patch("immich_memories.analysis.llm_query.llm_metrics.record_reply") as record_reply,
        pytest.raises(ValueError, match="not json"),
    ):
        await query_llm(
            "look", LLMConfig(provider=provider, base_url="http://localhost/v1", model="vision")
        )

    record_reply.assert_not_called()
