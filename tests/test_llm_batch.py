"""What a provider batch does for a stage that fans out, and what it never does.

Every test here drives a real httpx client over a fake wire, so the shape being
asserted is the shape a provider would actually receive.
"""

from __future__ import annotations

import json

import httpx
import pytest

from immich_memories.analysis import llm_batch, llm_metrics
from immich_memories.analysis.editorial_text_gateway import SyncTextPromptRequester
from immich_memories.analysis.llm_batch import (
    BatchCoordinator,
    BatchPolicy,
    BatchPrompt,
    batch_prompt_key,
)
from immich_memories.analysis.llm_providers import batch_route_for
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cancellation import PipelineCancelled, cancellation_scope


@pytest.fixture(autouse=True)
def _forget_probed_routes():
    """The served-route memo is process-wide by design, like the dialect memo."""
    llm_batch._SERVED_ROUTES.clear()
    yield
    llm_batch._SERVED_ROUTES.clear()


def _config(**overrides) -> LLMConfig:
    return LLMConfig(
        **{
            "provider": "openai",
            "base_url": "https://api.example.test/v1",
            "model": "a-model",
            "api_key": "k",
            "batch": "auto",
            "batch_min_requests": 2,
            **overrides,
        }
    )


def _prompts(config: LLMConfig, count: int) -> tuple[BatchPrompt, ...]:
    return tuple(
        BatchPrompt(batch_prompt_key(config, f"ask {n}", max_tokens=100), f"ask {n}", 100)
        for n in range(count)
    )


def _coordinator(config: LLMConfig, handler, *, policy: BatchPolicy | None = None):
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def factory(headers: dict[str, str]) -> httpx.AsyncClient:
        # WHY: replaces the provider's HTTP endpoint, the one external boundary.
        return httpx.AsyncClient(transport=httpx.MockTransport(record), headers=headers)

    coordinator = BatchCoordinator(
        config,
        policy or BatchPolicy.from_config(config),
        client_factory=factory,
        sleep=_no_wait,
    )
    return coordinator, calls


async def _no_wait(_seconds: float) -> None:
    return None


def _openai_wire(*, statuses: list[str], answers: dict[str, str]):
    """A host that answers OpenAI's Batch API, walking the statuses given."""
    remaining = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/files/out/content"):
            lines = "\n".join(
                json.dumps(
                    {
                        "custom_id": key,
                        "response": {
                            "status_code": 200,
                            "body": {
                                "choices": [{"message": {"content": text}}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                            },
                        },
                    }
                )
                for key, text in answers.items()
            )
            return httpx.Response(200, text=lines)
        if path.endswith("/files"):
            return httpx.Response(200, json={"id": "in"})
        if "/batches/" in path:
            status = remaining.pop(0) if remaining else "completed"
            return httpx.Response(200, json={"status": status, "output_file_id": "out"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "b1"})
        return httpx.Response(200, json={"data": []})

    return handler


def test_a_provider_with_no_batch_shape_declares_no_route():
    assert batch_route_for(LLMConfig(provider="ollama", model="m")) is None
    assert batch_route_for(LLMConfig(provider="openai", model="m")) == "openai"
    assert batch_route_for(LLMConfig(provider="anthropic", model="m")) == "anthropic"


def test_a_stage_smaller_than_the_minimum_is_never_queued():
    config = _config(batch_min_requests=8)
    coordinator, calls = _coordinator(config, _openai_wire(statuses=[], answers={}))

    coordinator.prefill(_prompts(config, 3))

    assert calls == []


def test_batch_off_reaches_no_provider_however_large_the_stage():
    config = _config(batch="off")
    coordinator, calls = _coordinator(config, _openai_wire(statuses=[], answers={}))

    coordinator.prefill(_prompts(config, 50))

    assert calls == []


def test_an_openai_batch_answers_every_prompt_it_was_given():
    config = _config()
    prompts = _prompts(config, 3)
    answers = {prompt.key: f"answer to {prompt.prompt}" for prompt in prompts}
    coordinator, calls = _coordinator(
        config, _openai_wire(statuses=["in_progress", "completed", "completed"], answers=answers)
    )

    coordinator.prefill(prompts)

    assert [coordinator.answer_for(p.key) for p in prompts] == list(answers.values())
    submitted = next(c for c in calls if c.url.path.endswith("/files"))
    lines = [json.loads(line) for line in _multipart_file(submitted).splitlines()]
    assert [line["custom_id"] for line in lines] == [p.key for p in prompts]
    assert lines[0]["url"] == "/v1/chat/completions"
    assert lines[0]["body"]["messages"][0]["content"] == "ask 0"
    assert lines[0]["body"]["max_tokens"] == 100


def test_an_answer_is_handed_out_once_and_then_asked_for_real():
    config = _config()
    prompts = _prompts(config, 2)
    coordinator, _ = _coordinator(
        config, _openai_wire(statuses=["completed"], answers={prompts[0].key: "only this one"})
    )

    coordinator.prefill(prompts)

    assert coordinator.answer_for(prompts[0].key) == "only this one"
    assert coordinator.answer_for(prompts[0].key) is None
    assert coordinator.answer_for(prompts[1].key) is None


def test_a_host_that_does_not_serve_the_declared_route_is_asked_once():
    config = _config()
    coordinator, calls = _coordinator(config, lambda _request: httpx.Response(404))

    coordinator.prefill(_prompts(config, 4))
    coordinator.prefill(_prompts(config, 4))

    assert len(calls) == 1
    assert calls[0].url.path == "/v1/batches"


def test_a_queue_slower_than_the_ceiling_gives_the_stage_back():
    config = _config()
    coordinator, _ = _coordinator(
        config,
        _openai_wire(statuses=["in_progress"] * 50, answers={}),
        policy=BatchPolicy(mode="auto", min_requests=2, max_wait_minutes=1),
    )
    prompts = _prompts(config, 4)

    coordinator.prefill(prompts)

    assert [coordinator.answer_for(p.key) for p in prompts] == [None] * 4


def test_message_batch_results_are_read_by_custom_id_not_by_position():
    config = _config(provider="anthropic", base_url="https://claude.example.test")
    prompts = _prompts(config, 2)
    ordered = [prompts[1], prompts[0]]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/results"):
            return httpx.Response(
                200,
                text="\n".join(
                    json.dumps(
                        {
                            "custom_id": prompt.key,
                            "result": {
                                "type": "succeeded",
                                "message": {
                                    "content": [{"type": "text", "text": prompt.prompt.upper()}],
                                    "usage": {"input_tokens": 7, "output_tokens": 3},
                                },
                            },
                        }
                    )
                    for prompt in ordered
                ),
            )
        if request.method == "POST":
            return httpx.Response(200, json={"id": "msgbatch_1"})
        if request.url.path.endswith("/batches"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"processing_status": "ended"})

    coordinator, _ = _coordinator(config, handler)
    coordinator.prefill(prompts)

    assert coordinator.answer_for(prompts[0].key) == "ASK 0"
    assert coordinator.answer_for(prompts[1].key) == "ASK 1"


def test_a_batched_reply_is_counted_apart_from_a_live_one():
    config = _config()
    prompts = _prompts(config, 2)
    answers = {prompt.key: "text" for prompt in prompts}
    coordinator, _ = _coordinator(config, _openai_wire(statuses=["completed"], answers=answers))

    with llm_metrics.collecting() as counters:
        coordinator.prefill(prompts)

    assert (counters.calls, counters.batch_calls) == (2, 2)
    assert counters.batch_prompt_tokens == 20
    assert counters.prompt_tokens == 20


def test_the_requester_reads_a_queued_answer_without_reaching_the_provider():
    config = _config()
    coordinator, calls = _coordinator(
        config,
        _openai_wire(
            statuses=["completed"],
            answers={
                batch_prompt_key(config, f"ask {n}", max_tokens=100): f"banked {n}"
                for n in range(2)
            },
        ),
    )
    requester = SyncTextPromptRequester(
        config, max_tokens=100, timeout_seconds=30, batch=coordinator
    )

    requester.prefetch([("ask 0", 100), ("ask 1", 100)])

    assert requester.request_with_budget("ask 0", max_tokens=100) == "banked 0"
    assert not [c for c in calls if c.url.path.endswith("/chat/completions")]


def _multipart_file(request: httpx.Request) -> str:
    """The JSONL body out of the upload, without a multipart parser to do it."""
    body = request.content.decode()
    start = body.index("\r\n\r\n", body.index('name="file"')) + 4
    return body[start : body.index("\r\n--", start)]


def test_a_stop_during_the_wait_is_not_read_as_a_provider_failure():
    """A user stop has to end the run, not quietly turn into a realtime re-ask."""
    config = _config()
    coordinator, _ = _coordinator(config, _openai_wire(statuses=["in_progress"] * 5, answers={}))
    checked: list[int] = []

    def stop() -> None:
        # The scope checks once on entry; the batch's own check is the next one.
        checked.append(1)
        if len(checked) > 1:
            raise PipelineCancelled

    with cancellation_scope(stop), pytest.raises(PipelineCancelled):
        coordinator.prefill(_prompts(config, 4))
