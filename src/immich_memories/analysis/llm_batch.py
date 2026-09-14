"""Ask a stage's independent prompts once, as one asynchronous provider batch.

A reading stage that fans out — one prompt per episode, one per moment — asks
those prompts one at a time, because each is its own HTTP call. Every hosted
provider sells a cheaper deal for exactly that shape: hand over the whole pile,
get it back within the day, pay half. This module is the pile. It takes the
prompts a stage was about to ask in a loop, submits them on whichever batch
route the provider declares, waits a bounded time, and hands back the answers
it got.

Nothing here is load-bearing. An answer that does not arrive — a provider with
no batch route, a queue slower than the ceiling, one refused line — is simply
absent from the result, and the stage asks that prompt live as it always did.
The most a failure can cost a run is its discount.

Two routes carry every host that has one: OpenAI's Batch API (a JSONL file, a
batch over it, an output file) and Anthropic's Message Batches (the requests
inline, the results as JSONL). Measured 2026-09-14: OpenAI and Melious both
answer the first; z.ai's Anthropic route answers 404 to the second, so its
cells stay realtime with the reason in the log.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_providers import batch_route_for, resolved_llm_config
from immich_memories.analysis.llm_query import build_llm_timeout
from immich_memories.analysis.llm_text_identity import text_judgment_key
from immich_memories.analysis.llm_wire import (
    LLMReply,
    anthropic_headers,
    batch_text_payload,
    learn_reasoning,
    openai_headers,
    read_batch_answer,
)
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cancellation import check_cancelled
from immich_memories.operations.cut_progress import StageUpdate, announce_stage

logger = logging.getLogger(__name__)

__all__ = [
    "BatchCoordinator",
    "BatchPolicy",
    "BatchPrompt",
    "batch_prompt_key",
]

# OpenAI's batch lines name the API path they stand for, and both hosts that
# serve this route serve it under /v1 whatever their configured base URL says.
OPENAI_BATCH_TARGET = "/v1/chat/completions"

# The widest window both routes offer. Nothing waits this long — the stage's own
# ceiling gives up far sooner — but asking for a shorter one only narrows the
# provider's room to schedule the work cheaply.
COMPLETION_WINDOW = "24h"

# Polling: often enough that a fast queue is not left sitting, slow enough that
# an hour of waiting is a few dozen requests rather than a few thousand.
FIRST_POLL_SECONDS = 5.0
POLL_BACKOFF = 1.5
MAX_POLL_SECONDS = 60.0

# Submitting and reading results are quick calls whatever the queue is doing.
CONTROL_TIMEOUT_SECONDS = 120.0

_SERVED_ROUTES: dict[tuple[str, str], bool] = {}


@dataclass(frozen=True)
class BatchPrompt:
    """One independent prompt, keyed by the realtime request it stands in for."""

    key: str
    prompt: str
    max_tokens: int


@dataclass(frozen=True)
class BatchPolicy:
    """When a stage's fan-out is worth queueing, and how long it may be waited on."""

    mode: str = "off"
    min_requests: int = 8
    max_wait_minutes: int = 60

    @classmethod
    def from_config(cls, config: LLMConfig) -> BatchPolicy:
        return cls(
            mode=config.batch,
            min_requests=config.batch_min_requests,
            max_wait_minutes=config.batch_max_wait_minutes,
        )


def batch_prompt_key(config: LLMConfig, prompt: str, *, max_tokens: int) -> str:
    """The identity the realtime path would bank this exact question under."""
    return text_judgment_key(
        resolved_llm_config(config),
        prompt,
        thinking=False,
        max_tokens=max_tokens,
        temperature=0.0,
        require_complete=True,
    )


class BatchCoordinator:
    """Answer as many of a stage's independent prompts as one batch will."""

    def __init__(
        self,
        config: LLMConfig,
        policy: BatchPolicy,
        *,
        client_factory: Callable[[dict[str, str]], httpx.AsyncClient] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._policy = policy
        self._client_factory = client_factory or _default_client
        self._sleep = sleep
        self._now = now
        self._answers: dict[str, LLMReply] = {}

    def answer_for(self, key: str) -> LLMReply | None:
        """The batched answer to one exact question, or None to ask it live."""
        return self._answers.pop(key, None)

    def prefill(self, prompts: Sequence[BatchPrompt]) -> None:
        """Queue a whole stage at once, keeping whatever comes back in time."""
        route = self._eligible_route(prompts)
        if route is None:
            return
        wanted = tuple(p for p in prompts if p.key not in self._answers)
        try:
            self._answers.update(_run(self._fill(route, wanted)))
        except Exception as exc:  # WHY: a batch is an optimisation, never a dependency
            logger.warning(
                "Batch route %s did not answer (%s: %s); asking in real time",
                route,
                type(exc).__name__,
                str(exc)[:200],
            )

    def _eligible_route(self, prompts: Sequence[BatchPrompt]) -> str | None:
        """Say why this stage is not being batched, once, in the operator's log."""
        if self._policy.mode != "auto" or not prompts:
            return None
        route = batch_route_for(self._config)
        if route is None:
            logger.info(
                "Provider %s declares no batch route; reading in real time",
                self._config.provider,
            )
            return None
        if len(prompts) < self._policy.min_requests:
            logger.info(
                "Stage has %d independent prompts, fewer than the %d a batch is worth",
                len(prompts),
                self._policy.min_requests,
            )
            return None
        return route

    async def _fill(self, route: str, prompts: Sequence[BatchPrompt]) -> dict[str, LLMReply]:
        adapter = _ADAPTERS[route]
        headers = adapter.headers(self._config)
        async with self._client_factory(headers) as client:
            if not await self._serves(adapter, client):
                return {}
            handle = await adapter.submit(client, self._config, prompts)
            submitted = self._now()
            logger.info(
                "Submitted %d prompts to the %s batch route as %s",
                len(prompts),
                route,
                handle,
            )
            if not await self._wait(adapter, client, handle, len(prompts), submitted):
                return {}
            return await adapter.collect(client, self._config, handle)

    async def _serves(self, adapter: BatchRoute, client: httpx.AsyncClient) -> bool:
        """Ask the declared route once per host; a 404 there is a whole provider's answer."""
        base = self._config.base_url.rstrip("/")
        memo = (base, adapter.name)
        served = _SERVED_ROUTES.get(memo)
        if served is None:
            response = await client.get(adapter.listing_url(base), params={"limit": 1})
            served = response.is_success
            _SERVED_ROUTES[memo] = served
            if not served:
                logger.warning(
                    "%s does not serve the %s batch route (HTTP %d); reading in real time",
                    base,
                    adapter.name,
                    response.status_code,
                )
        return served

    async def _wait(
        self,
        adapter: BatchRoute,
        client: httpx.AsyncClient,
        handle: str,
        count: int,
        submitted: datetime,
    ) -> bool:
        """Poll with backoff until the batch ends or the run's patience does."""
        deadline = submitted.timestamp() + self._policy.max_wait_minutes * 60
        delay = FIRST_POLL_SECONDS
        while True:
            check_cancelled()
            announce_stage(StageUpdate(_waiting_line(adapter.name, count, submitted)))
            if await adapter.ended(client, handle):
                return True
            if self._now().timestamp() + delay > deadline:
                logger.warning(
                    "Batch %s had not finished after %d minutes; asking the rest in real time",
                    handle,
                    self._policy.max_wait_minutes,
                )
                return False
            await self._sleep(delay)
            delay = min(delay * POLL_BACKOFF, MAX_POLL_SECONDS)


def _waiting_line(provider: str, count: int, submitted: datetime) -> str:
    return f"waiting on {provider} batch: {count} prompts, submitted {submitted:%H:%M}"


class BatchRoute(Protocol):
    """One provider dialect's moves: where to probe, submit, poll, and read."""

    name: str

    def headers(self, config: LLMConfig) -> dict[str, str]: ...

    def listing_url(self, base: str) -> str: ...

    async def submit(
        self, client: httpx.AsyncClient, config: LLMConfig, prompts: Sequence[BatchPrompt]
    ) -> str: ...

    async def ended(self, client: httpx.AsyncClient, handle: str) -> bool: ...

    async def collect(
        self, client: httpx.AsyncClient, config: LLMConfig, handle: str
    ) -> dict[str, LLMReply]: ...


class OpenAIBatchAdapter:
    """OpenAI's Batch API, and every host that copied it (Melious, measured)."""

    name = "openai"

    @staticmethod
    def headers(config: LLMConfig) -> dict[str, str]:
        return openai_headers(resolved_llm_config(config))

    @staticmethod
    def listing_url(base: str) -> str:
        return f"{base}/batches"

    @staticmethod
    async def submit(
        client: httpx.AsyncClient, config: LLMConfig, prompts: Sequence[BatchPrompt]
    ) -> str:
        base = resolved_llm_config(config).base_url.rstrip("/")
        lines = "\n".join(
            json.dumps(
                {
                    "custom_id": prompt.key,
                    "method": "POST",
                    "url": OPENAI_BATCH_TARGET,
                    "body": batch_text_payload(config, prompt.prompt, max_tokens=prompt.max_tokens),
                },
                ensure_ascii=False,
            )
            for prompt in prompts
        )
        uploaded = await client.post(
            f"{base}/files",
            files={"file": ("requests.jsonl", lines.encode(), "application/jsonl")},
            data={"purpose": "batch"},
        )
        uploaded.raise_for_status()
        created = await client.post(
            f"{base}/batches",
            json={
                "input_file_id": uploaded.json()["id"],
                "endpoint": OPENAI_BATCH_TARGET,
                "completion_window": COMPLETION_WINDOW,
            },
        )
        created.raise_for_status()
        return f"{base}/batches/{created.json()['id']}"

    @staticmethod
    async def ended(client: httpx.AsyncClient, handle: str) -> bool:
        body = await _status(client, handle)
        status = body.get("status")
        if status in {"failed", "expired", "cancelled", "canceled"}:
            raise RuntimeError(f"batch {handle} ended as {status}")
        return status == "completed"

    @staticmethod
    async def collect(
        client: httpx.AsyncClient, config: LLMConfig, handle: str
    ) -> dict[str, LLMReply]:
        body = await _status(client, handle)
        output = body.get("output_file_id")
        if not output:
            return {}
        base = resolved_llm_config(config).base_url.rstrip("/")
        content = await client.get(f"{base}/files/{output}/content")
        content.raise_for_status()
        return _read_results(config, content.text, _openai_result)


class AnthropicBatchAdapter:
    """Anthropic's Message Batches, on any host that answers /v1/messages/batches."""

    name = "anthropic"

    @staticmethod
    def headers(config: LLMConfig) -> dict[str, str]:
        return anthropic_headers(resolved_llm_config(config))

    @staticmethod
    def listing_url(base: str) -> str:
        return f"{base}/v1/messages/batches"

    @staticmethod
    async def submit(
        client: httpx.AsyncClient, config: LLMConfig, prompts: Sequence[BatchPrompt]
    ) -> str:
        base = resolved_llm_config(config).base_url.rstrip("/")
        created = await client.post(
            f"{base}/v1/messages/batches",
            json={
                "requests": [
                    {
                        "custom_id": prompt.key,
                        "params": batch_text_payload(
                            config, prompt.prompt, max_tokens=prompt.max_tokens
                        ),
                    }
                    for prompt in prompts
                ]
            },
        )
        created.raise_for_status()
        return f"{base}/v1/messages/batches/{created.json()['id']}"

    @staticmethod
    async def ended(client: httpx.AsyncClient, handle: str) -> bool:
        body = await _status(client, handle)
        return body.get("processing_status") == "ended"

    @staticmethod
    async def collect(
        client: httpx.AsyncClient, config: LLMConfig, handle: str
    ) -> dict[str, LLMReply]:
        body = await _status(client, handle)
        url = body.get("results_url") or f"{handle}/results"
        results = await client.get(url)
        results.raise_for_status()
        return _read_results(config, results.text, _anthropic_result)


_ADAPTERS: dict[str, BatchRoute] = {
    "openai": OpenAIBatchAdapter(),
    "anthropic": AnthropicBatchAdapter(),
}


async def _status(client: httpx.AsyncClient, handle: str) -> dict:
    """Read one batch's current state; the handle is stored as its own URL."""
    response = await client.get(handle)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("batch status body is not an object")
    return body


def _read_results(
    config: LLMConfig, jsonl: str, reader: Callable[[dict], dict | None]
) -> dict[str, LLMReply]:
    """Key every answered line by its custom_id; skip the lines that carried none.

    Results arrive in whatever order the provider finished them, which is why
    the custom_id is the realtime request's own judgment key rather than a
    position: a stage can read them back in any order it likes.
    """
    answers: dict[str, LLMReply] = {}
    for line in jsonl.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        body = reader(row)
        if body is None:
            continue
        try:
            reply = _batch_reply(config, body)
            answers[row["custom_id"]] = reply
        except (KeyError, TypeError, ValueError) as exc:
            logger.info("Batch line %s was unreadable (%s)", row.get("custom_id"), exc)
            continue
        llm_metrics.record_batch_reply(
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            reasoning_tokens=reply.reasoning_tokens,
        )
        resolved = resolved_llm_config(config)
        if resolved.provider == "openai-compatible":
            learn_reasoning((resolved.base_url.rstrip("/"), resolved.model), reply)
    return answers


def _openai_result(row: dict) -> dict | None:
    response = row.get("response")
    if not isinstance(response, dict) or response.get("status_code") != 200:
        return None
    body = response.get("body")
    return body if isinstance(body, dict) else None


def _anthropic_result(row: dict) -> dict | None:
    result = row.get("result")
    if not isinstance(result, dict) or result.get("type") != "succeeded":
        return None
    message = result.get("message")
    return message if isinstance(message, dict) else None


def _batch_reply(config: LLMConfig, body: dict) -> LLMReply:
    """Keep the answer and its billed reasoning together, including empty replies."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = usage.get("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = (usage.get("input_tokens") or 0) + (
            usage.get("cache_read_input_tokens") or 0
        )
    choices = body.get("choices") or [{}]
    details = usage.get("completion_tokens_details") or {}
    return LLMReply(
        content=read_batch_answer(config, body),
        finish_reason=str(choices[0].get("finish_reason") or body.get("stop_reason") or ""),
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens") or details.get("reasoning_tokens") or 0),
    )


def _default_client(headers: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=build_llm_timeout(CONTROL_TIMEOUT_SECONDS), headers=headers)


def _run(coroutine: object) -> dict[str, LLMReply]:
    """Run the batch from synchronous stage code, inside an event loop or not."""
    from immich_memories.analysis.editorial_async_bridge import _run_sync

    return _run_sync(coroutine)
