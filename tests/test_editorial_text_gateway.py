"""Production transport adapter for post-card text judgments."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from immich_memories.analysis import editorial_text_gateway as gateway
from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.cache.judgment_cache import JudgmentCache
from immich_memories.config_models_llm import LLMConfig


class _Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def _request(tmp_path: Path, *, thinking: bool = False) -> TextRequest:
    return TextRequest(
        prompt="Choose the exact lived sequence.",
        llm_config=LLMConfig(
            provider="openai-compatible",
            base_url="http://editor.test/v1",
            model="editor-model",
            api_key="not-a-real-key",
            thinking=True,
        ),
        cache_path=tmp_path / "judgments.sqlite",
        max_tokens=1200,
        timeout_seconds=45,
        thinking=thinking,
    )


def test_query_text_requester_preserves_the_exact_request_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_query(prompt: str, llm_config: Any, **kwargs: Any) -> str:
        seen.append((prompt, llm_config, kwargs))
        return '{"ok":true}'

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    request = _request(tmp_path, thinking=True)

    result = asyncio.run(gateway.QueryTextRequester(monotonic=_Clock(10.0, 12.5)).request(request))

    assert result.prompt == request.prompt
    assert result.raw == '{"ok":true}'
    assert result.wall_seconds == 2.5
    assert result.cache_hit is False
    assert result.thinking is True
    # The reader is watched: a dropped connection is announced with its endpoint.
    assert callable(seen[0][2].pop("transport_observer"))
    assert seen == [
        (
            request.prompt,
            request.llm_config,
            {
                "temperature": 0.0,
                "max_tokens": 1200,
                "timeout_seconds": 45,
                "thinking": True,
                "cache_path": None,  # The gateway owns the bounded request cache.
                "require_complete": True,
            },
        )
    ]


def test_query_text_requester_reports_an_exact_warm_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    cache = JudgmentCache(request.cache_path)
    cache.remember(request.judgment_key, "already banked")
    cache.close()

    async def fake_query(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("an exact gateway hit must not reach transport")

    monkeypatch.setattr(gateway, "query_llm", fake_query)

    result = asyncio.run(gateway.QueryTextRequester(monotonic=_Clock(20.0, 20.25)).request(request))

    assert result.raw == "already banked"
    assert result.cache_hit is True


def test_query_text_requester_retries_one_incomplete_answer_with_double_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[int] = []

    async def fake_query(*_args: Any, **kwargs: Any) -> str:
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            raise ValueError("incomplete response")
        return '{"repaired":true}'

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    request = _request(tmp_path)

    result = asyncio.run(gateway.QueryTextRequester(monotonic=_Clock(30.0, 31.0)).request(request))

    assert budgets == [1200, 2400]
    assert result.raw == '{"repaired":true}'
    assert result.cache_hit is False


def test_query_text_requester_does_not_retry_unrelated_transport_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_query(*_args: Any, **_kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("provider deadline")

    monkeypatch.setattr(gateway, "query_llm", fake_query)

    with pytest.raises(TimeoutError, match="provider deadline"):
        asyncio.run(gateway.QueryTextRequester().request(_request(tmp_path)))

    assert calls == 1


def test_semantic_text_model_identity_includes_endpoint_and_dialect_not_credentials() -> None:
    first = LLMConfig(
        provider="openai-compatible",
        base_url="http://first.test/v1/",
        model="same-model",
        api_key="first-secret",
        timeout_seconds=30,
    )
    operational_change = first.model_copy(
        update={"api_key": "second-secret", "timeout_seconds": 600}
    )
    endpoint_change = first.model_copy(update={"base_url": "http://second.test/v1"})
    dialect_change = first.model_copy(update={"no_thinking_params": {}})

    identity = gateway.semantic_text_model_identity(first, thinking=False)

    assert identity == gateway.semantic_text_model_identity(
        operational_change,
        thinking=False,
    )
    assert identity != gateway.semantic_text_model_identity(endpoint_change, thinking=False)
    assert identity != gateway.semantic_text_model_identity(dialect_change, thinking=False)
    assert "secret" not in identity


def test_sync_prompt_requester_never_banks_unvalidated_raw_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[int] = []
    cache_paths: list[object] = []

    async def fake_query(*_args: Any, **kwargs: Any) -> str:
        budgets.append(kwargs["max_tokens"])
        cache_paths.append(kwargs["cache_path"])
        if len(budgets) == 1:
            raise KeyError("missing content")
        return '{"schema_version":"validated-by-caller"}'

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    requester = gateway.SyncTextPromptRequester(
        llm_config=_request(Path("unused")).llm_config,
        max_tokens=900,
        timeout_seconds=60,
    )

    answer = requester("Read only this evidence.")

    assert answer == '{"schema_version":"validated-by-caller"}'
    assert budgets == [900, 1800]
    assert cache_paths == [None, None]


def test_sync_prompt_requester_is_safe_when_called_inside_an_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(prompt: str, *_args: Any, **_kwargs: Any) -> str:
        return prompt.upper()

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    requester = gateway.SyncTextPromptRequester(
        llm_config=_request(Path("unused")).llm_config,
        max_tokens=900,
        timeout_seconds=60,
    )

    async def caller() -> str:
        return requester("still synchronous")

    assert asyncio.run(caller()) == "STILL SYNCHRONOUS"


def test_sync_prompt_requester_honors_a_smaller_budget_but_never_doubles_past_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[int] = []

    async def fake_query(*_args: Any, **kwargs: Any) -> str:
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            raise ValueError("incomplete response")
        return '{"complete":true}'

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    requester = gateway.SyncTextPromptRequester(
        llm_config=_request(Path("unused")).llm_config,
        max_tokens=1200,
        timeout_seconds=60,
    )

    assert requester.request_with_budget("bounded episode pack", max_tokens=700) == (
        '{"complete":true}'
    )
    assert budgets == [700, 1200]

    with pytest.raises(ValueError, match="configured ceiling"):
        requester.request_with_budget("too much", max_tokens=1201)


def test_an_answer_the_caller_refuses_is_asked_again_instead_of_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(['{"keep":["M01 | 2024-06-01T08:15 | ..."]}', '{"keep":["M01"]}'])
    asked: list[str] = []

    async def fake_query(prompt: str, llm_config: Any, **kwargs: Any) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    request = _request(tmp_path)
    requester = gateway.QueryTextRequester()
    refused = asyncio.run(requester.request(request, accepts=lambda raw: "|" not in raw))
    repeated = asyncio.run(requester.request(request, accepts=lambda raw: "|" not in raw))
    replayed = asyncio.run(requester.request(request, accepts=lambda raw: "|" not in raw))

    assert refused.raw == '{"keep":["M01 | 2024-06-01T08:15 | ..."]}'
    assert (repeated.raw, repeated.cache_hit) == ('{"keep":["M01"]}', False)
    assert (replayed.raw, replayed.cache_hit) == ('{"keep":["M01"]}', True)
    assert len(asked) == 2


def test_a_poisoned_bank_from_an_earlier_run_is_forgotten_and_asked_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(prompt: str, llm_config: Any, **kwargs: Any) -> str:
        return '{"keep":["M01"]}'

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    request = _request(tmp_path)
    cache = JudgmentCache(request.cache_path)
    cache.remember(request.judgment_key, "not a pick this contract can read")
    cache.close()

    call = asyncio.run(
        gateway.QueryTextRequester().request(request, accepts=lambda raw: raw.startswith("{"))
    )

    assert (call.raw, call.cache_hit) == ('{"keep":["M01"]}', False)


def test_a_bounded_failure_row_names_the_reasoning_that_took_the_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two empty answers read the same on disk unless the token split is on the row."""
    from immich_memories.analysis.llm_wire import LLMTransportAttempt

    async def starved(_prompt: str, _config: Any, **kwargs: Any) -> str:
        kwargs["transport_observer"](
            LLMTransportAttempt(
                1,
                "response",
                200,
                None,
                finish_reason="length",
                completion_tokens=8492,
                reasoning_tokens=8492,
            )
        )
        return ""

    monkeypatch.setattr(gateway, "query_llm", starved)
    request = replace(_request(tmp_path), json_object=True)

    with pytest.raises(gateway.TextCompletionFailure) as raised:
        asyncio.run(gateway.QueryTextRequester().request(request))

    assert [row["reasoning_tokens"] for row in raised.value.attempts] == [8492, 8492]
    assert [row["finish_reason"] for row in raised.value.attempts] == ["length", "length"]
    assert [row["max_tokens"] for row in raised.value.attempts] == [1200, 2400]
