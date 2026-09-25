"""A prose seat asks for its JSON shape and states its own sampling; a server that refuses either is still answered."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from immich_memories.analysis.llm_text_identity import text_model_identity
from immich_memories.config_models_llm import LLMConfig
from tests.test_llm_query import _openai_400, _openai_response

SHAPE = {
    "type": "json_schema",
    "json_schema": {
        "name": "title",
        "schema": {"type": "object", "properties": {"title": {"type": "string"}}},
    },
}


def _local(**overrides) -> LLMConfig:
    return LLMConfig(
        provider="openai-compatible",
        base_url="http://localhost:9999/v1",
        model="small",
        **overrides,
    )


async def _sent(config, **options):
    from immich_memories.analysis.llm_query import query_llm

    # WHY: the LLM server is the external boundary this request reaches.
    with patch("httpx.AsyncClient.post", return_value=_openai_response()) as post:
        await query_llm("Name this film", config, **options)
    return post.call_args[1]["json"]


@pytest.mark.asyncio
async def test_a_seat_s_json_shape_reaches_the_server():
    body = await _sent(_local(), response_format=SHAPE)

    assert body["response_format"] == SHAPE


@pytest.mark.asyncio
async def test_structured_output_off_sends_no_shape():
    body = await _sent(_local(structured_output=False), response_format=SHAPE)

    assert "response_format" not in body


@pytest.mark.asyncio
async def test_a_local_server_is_told_the_repetition_penalty_rather_than_left_to_its_default():
    body = await _sent(_local())

    assert body["repetition_penalty"] == 1.0


@pytest.mark.asyncio
async def test_a_hosted_openai_endpoint_gets_no_repetition_penalty():
    body = await _sent(
        LLMConfig(provider="openai", base_url="https://api.openai.com/v1", model="gpt")
    )

    assert "repetition_penalty" not in body


@pytest.mark.asyncio
async def test_a_server_that_refuses_the_shape_is_asked_again_without_it_and_remembers():
    from immich_memories.analysis.llm_query import query_llm

    answers = iter(
        [
            _openai_400("response_format is not supported by this server"),
            _openai_response(),
            _openai_response(),
        ]
    )
    sent = []

    def server(url, json):
        sent.append(dict(json))
        return next(answers)

    # WHY: the LLM server is the external boundary this request reaches.
    with patch("httpx.AsyncClient.post", side_effect=server):
        await query_llm("Name this film", _local(), response_format=SHAPE)
        await query_llm("Name this film again", _local(), response_format=SHAPE)

    first, retry, later = sent
    assert "response_format" in first
    assert "response_format" not in retry
    assert "response_format" not in later


@pytest.mark.asyncio
async def test_a_server_that_refuses_the_penalty_is_asked_again_without_it():
    from immich_memories.analysis.llm_query import query_llm

    refused = _openai_400("Unrecognized request argument supplied: repetition_penalty")
    # WHY: the LLM server is the external boundary this request reaches.
    with patch("httpx.AsyncClient.post", side_effect=[refused, _openai_response()]) as post:
        await query_llm("Name this film", _local())

    assert "repetition_penalty" not in post.call_args_list[1][1]["json"]


@pytest.mark.asyncio
async def test_ollama_gets_the_shape_as_its_format_and_the_penalty_as_an_option():
    from unittest.mock import AsyncMock, MagicMock

    from immich_memories.analysis.llm_query import query_llm

    config = LLMConfig(provider="ollama", base_url="http://localhost:11434", model="small")
    answer = AsyncMock(status_code=200, json=MagicMock(return_value={"response": "{}"}))
    answer.raise_for_status = lambda: None
    # WHY: the LLM server is the external boundary this request reaches.
    with patch("httpx.AsyncClient.post", return_value=answer) as post:
        await query_llm("Name this film", config, response_format=SHAPE)
    body = post.call_args[1]["json"]

    assert body["format"] == SHAPE["json_schema"]["schema"]
    assert body["options"]["repeat_penalty"] == 1.0


def test_changing_either_setting_is_another_model_identity():
    base = text_model_identity(_local(), thinking=False)

    assert text_model_identity(_local(structured_output=False), thinking=False) != base
    assert text_model_identity(_local(repetition_penalty=1.1), thinking=False) != base
