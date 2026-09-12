"""Cache the exact request, including a completed bounded recovery.

The matrix planner replay stays on the probe branch.
"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_text_gateway import QueryTextRequester
from immich_memories.analysis.llm_query import query_llm
from immich_memories.config_models_llm import LLMConfig


def _config():
    return LLMConfig(provider="openai-compatible", base_url="http://first.test/v1", model="same")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"llm_config": _config().model_copy(update={"base_url": "http://second.test/v1"})},
        {"llm_config": _config().model_copy(update={"no_thinking_params": {}})},
        {"max_tokens": 1500},
        {"temperature": 0.7},
        {"require_complete": True},
    ],
)
async def test_answer_changing_request_settings_miss_the_transport_cache(tmp_path, changed):
    calls = []

    async def dispatch(*args):
        calls.append(args)
        return f"answer {len(calls)}"

    request = {
        "prompt": "same evidence",
        "llm_config": _config(),
        "cache_path": tmp_path / "text.db",
    }
    # WHY: the HTTP dispatch is the external boundary; replies are scripted so cache identity is what varies
    with patch("immich_memories.analysis.llm_query._dispatch", dispatch):
        first = await query_llm(**request)
        second = await query_llm(**(request | changed))
        repeated = await query_llm(**(request | changed))

    assert len(calls) == 2
    assert first != second
    assert repeated == second


@pytest.mark.asyncio
async def test_recovered_gateway_answer_replays_without_repeating_the_failed_budget(tmp_path):
    calls = []

    async def dispatch(*args):
        calls.append(args)
        if args[3] == 100:
            raise ValueError("LLM returned incomplete content")
        return "complete answer"

    request = TextRequest("same evidence", _config(), tmp_path / "text.db", 100, 30)
    # WHY: the HTTP dispatch is the external boundary; replies are scripted so cache identity is what varies
    with patch("immich_memories.analysis.llm_query._dispatch", dispatch):
        first = await QueryTextRequester().request(request)
        second = await QueryTextRequester().request(request)
        operational_change = replace(request, timeout_seconds=60)
        third = await QueryTextRequester().request(operational_change)

    assert [args[3] for args in calls] == [100, 200]
    assert first.raw == second.raw == third.raw == "complete answer"
    assert not first.cache_hit
    assert second.cache_hit and third.cache_hit
