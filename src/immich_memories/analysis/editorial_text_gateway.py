"""Production transport adapter for banked post-card text judgments."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextvars import copy_context
from dataclasses import dataclass, replace

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.editorial_case import TextCall, TextRequest
from immich_memories.analysis.editorial_json_completion import (
    JSON_EMPTY_ARRAY_POLICY,
    JSONDecisionError,
    complete_final_json,
    json_format_repair_prompt,
)
from immich_memories.analysis.editorial_text_artifacts import TextPromptArtifacts
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure
from immich_memories.analysis.llm_batch import BatchCoordinator, BatchPrompt, batch_prompt_key
from immich_memories.analysis.llm_providers import reader_concurrency, resolved_llm_config
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.llm_single_flight import TEXT_JUDGMENTS
from immich_memories.analysis.llm_text_identity import text_model_identity
from immich_memories.analysis.llm_wire import (
    LLMIncompleteResponse,
    LLMReply,
    LLMTransportAttempt,
    batch_answer,
)
from immich_memories.analysis.provider_failure import (
    RETRY_ATTEMPTS,
    THROTTLE,
    announce_retry,
    group_wait,
    provider_failure,
    retry_wait,
)
from immich_memories.analysis.provider_status import watch_provider
from immich_memories.cache.judgment_cache import JudgmentCache
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cancellation import check_cancelled

logger = logging.getLogger(__name__)

__all__ = [
    "QueryTextRequester",
    "SyncTextPromptRequester",
    "semantic_text_model_identity",
]


class BilledReply:
    """The last completed POST's own account of what it billed, kept for the record.

    A reasoning host charges its private thinking to the same budget as the
    answer, so "the reply was empty" and "the reply had no room left to be
    written" look identical on disk without the split.
    """

    def __init__(self) -> None:
        self._attempt: LLMTransportAttempt | None = None

    def watching(
        self, downstream: Callable[[LLMTransportAttempt], None]
    ) -> Callable[[LLMTransportAttempt], None]:
        def observe(attempt: LLMTransportAttempt) -> None:
            if attempt.finish_reason is not None:
                self._attempt = attempt
            downstream(attempt)

        return observe

    def as_record(self) -> dict:
        if self._attempt is None:
            return {}
        return {
            "finish_reason": self._attempt.finish_reason,
            "completion_tokens": self._attempt.completion_tokens,
            "reasoning_tokens": self._attempt.reasoning_tokens,
        }


class QueryTextRequester:
    """Execute one exact text request with the matrix runner's bounded retry."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        json_failure_observer: Callable[[dict], None] | None = None,
        json_decoding_observer: Callable[[dict], None] | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._json_failure_observer = json_failure_observer
        self._json_decoding_observer = json_decoding_observer

    async def request(
        self, request: TextRequest, *, accepts: Callable[[str], bool] | None = None
    ) -> TextCall:
        """Return the complete answer and whether the exact prompt was already banked.

        `accepts` is the calling contract's own check. Only an answer it can read is
        kept or replayed: banking one it rejects makes every rerun fail from the bank
        without ever asking the provider again.
        """
        check_cancelled()
        started = self._monotonic()
        cache = JudgmentCache(request.cache_path)
        try:
            banked = self._usable_banked_answer(cache, request, accepts)
            if banked is None:
                # Overlapping readers can carry the same question. Only one of them
                # pays for it; the rest wait here and read what it banked.
                with TEXT_JUDGMENTS.key(request.judgment_key):
                    banked = self._usable_banked_answer(cache, request, accepts)
                    raw = (
                        banked if banked is not None else await self._paid(cache, request, accepts)
                    )
            else:
                raw = banked
        finally:
            cache.close()
        return TextCall(
            prompt=request.prompt,
            raw=raw,
            wall_seconds=self._monotonic() - started,
            cache_hit=banked is not None,
            thinking=request.thinking,
        )

    async def _paid(
        self, cache: JudgmentCache, request: TextRequest, accepts: Callable[[str], bool] | None
    ) -> str:
        """Ask the provider once, unless this exact question is already banked as failed."""
        failure = TextCompletionFailure.from_record(
            None if request.refresh else cache.completion_failure_for(request.judgment_key)
        )
        if failure is not None:
            llm_metrics.record_cache_hit()
            raise failure
        raw = await self._bounded_query(request, cache)
        if accepts is None or accepts(raw):
            cache.remember(request.judgment_key, raw)
        return raw

    @staticmethod
    def _usable_banked_answer(
        cache: JudgmentCache, request: TextRequest, accepts: Callable[[str], bool] | None
    ) -> str | None:
        """Drop a banked answer the caller refuses; replaying it can never recover."""
        if request.refresh:
            return None
        raw = cache.answer_for(request.judgment_key)
        if raw is None:
            return None
        if accepts is not None and not accepts(raw):
            cache.forget(request.judgment_key)
            return None
        llm_metrics.record_cache_hit()
        return raw

    async def _bounded_query(self, request, cache):
        """The original attempt and existing doubled-budget retry, with typed exhaustion."""
        failures = []
        current = request
        for attempt in range(2):
            budget = request.max_tokens * (attempt + 1)
            billed = BilledReply()
            try:
                return await self._query(current, max_tokens=budget, billed=billed)
            except (KeyError, ValueError) as exc:
                bounded = isinstance(exc, (LLMIncompleteResponse, JSONDecisionError))
                failures.append(
                    {
                        "outcome": "invalid_json"
                        if isinstance(exc, JSONDecisionError)
                        else "incomplete",
                        "raw": exc.raw
                        if isinstance(exc, (LLMIncompleteResponse, JSONDecisionError))
                        else "",
                        "error": str(exc),
                        "max_tokens": budget,
                        **billed.as_record(),
                        "bounded": bounded,
                    }
                )
                if attempt == 1:
                    if all(row["bounded"] for row in failures):
                        failure = TextCompletionFailure(
                            [{k: v for k, v in row.items() if k != "bounded"} for row in failures]
                        )
                        cache.remember_completion_failure(request.judgment_key, failure.as_record())
                        raise failure from exc
                    raise
                if isinstance(exc, JSONDecisionError):
                    current = replace(request, prompt=json_format_repair_prompt(request.prompt))
        raise AssertionError("bounded completion must return or raise")

    @staticmethod
    async def _transported(request: TextRequest, *, max_tokens: int, billed: BilledReply) -> str:
        """Ask the provider, waiting out one that is only shedding load.

        A hosted reader throttled for a minute used to end the whole run on a 429
        that a later attempt would have answered. The billed-reply observer stays on
        every attempt, so the last completed POST is still the one on the record.
        """
        watch = billed.watching(watch_provider("reader", request.llm_config))
        held = THROTTLE.pause()
        if held:
            # Another reader is already waiting out a rate limit on this key. Joining
            # it beats spending an attempt discovering the same throttle.
            await asyncio.sleep(held)
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return await query_llm(
                    request.prompt,
                    request.llm_config,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    timeout_seconds=request.timeout_seconds,
                    thinking=request.thinking,
                    cache_path=None,  # The gateway banks the complete bounded-recovery request.
                    transport_observer=watch,
                    require_complete=not request.json_object,
                )
            except httpx.HTTPStatusError as exc:
                refusal = provider_failure(exc, images_attached=False)
                wait = retry_wait(refusal, attempt)
                if wait is None or refusal is None:
                    raise
                announce_retry(logger, refusal, wait, attempt)
                await asyncio.sleep(group_wait(refusal, wait))
        raise AssertionError("rate-limited text request must return or raise")

    async def _query(self, request: TextRequest, *, max_tokens: int, billed: BilledReply) -> str:
        raw = await self._transported(request, max_tokens=max_tokens, billed=billed)
        if request.json_object:
            try:
                decoded = complete_final_json(
                    raw,
                    fields=request.json_fields,
                    empty_array_pairs=request.json_empty_array_pairs,
                )
                if request.json_fields and decoded != raw and self._json_decoding_observer:
                    self._json_decoding_observer(
                        {
                            "prompt": request.prompt,
                            "raw": raw,
                            "decoded": decoded,
                            "fields": list(request.json_fields),
                            "max_tokens": max_tokens,
                            **(
                                {
                                    "empty_array_policy": JSON_EMPTY_ARRAY_POLICY,
                                    "empty_array_pairs": sorted(request.json_empty_array_pairs),
                                }
                                if request.json_empty_array_pairs
                                else {}
                            ),
                        }
                    )
                return decoded
            except ValueError as exc:
                if self._json_failure_observer:
                    self._json_failure_observer(
                        {
                            "prompt": request.prompt,
                            "raw": raw,
                            "error": str(exc),
                            "max_tokens": max_tokens,
                        }
                    )
                raise JSONDecisionError(str(exc), raw=raw) from exc
        return raw


@dataclass(frozen=True)
class SyncTextPromptRequester:
    """Synchronous validated-store input adapter for episode and period reads."""

    llm_config: LLMConfig
    max_tokens: int
    timeout_seconds: int
    thinking: bool = False
    artifacts: TextPromptArtifacts | None = None
    batch: BatchCoordinator | None = None
    retry_larger: bool = True

    def __post_init__(self) -> None:
        if self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("text prompt request budgets must be positive")

    def __call__(self, prompt: str) -> str:
        """Ask without raw prompt caching; the semantic caller validates before banking."""
        if not prompt.strip():
            raise ValueError("text prompt cannot be blank")
        ceiling = None if self.retry_larger else self.max_tokens
        return _run_sync(self._request(prompt, max_tokens=self.max_tokens, ceiling=ceiling))

    def request_with_budget(self, prompt: str, *, max_tokens: int) -> str:
        """Honor a caller-sized completion budget under this adapter's hard ceiling."""
        if not prompt.strip() or max_tokens <= 0:
            raise ValueError("text prompt and completion budget must be positive")
        if max_tokens > self.max_tokens:
            raise ValueError("text completion budget exceeds the configured ceiling")
        return _run_sync(self._request(prompt, max_tokens=max_tokens, ceiling=self.max_tokens))

    def prefetch(self, asked: Sequence[tuple[str, int]]) -> None:
        """Offer a stage's independent prompts to the provider's batch route at once.

        Only prompts whose answers are not each other's input belong here: a
        batch is submitted whole, so a prompt written from another's answer
        cannot be in it. What the batch does not answer is asked live below,
        which is why nothing downstream has to know which arrived how.
        """
        if self.batch is None:
            return
        self.batch.prefill(
            [
                BatchPrompt(
                    batch_prompt_key(self.llm_config, prompt, max_tokens=budget), prompt, budget
                )
                for prompt, budget in asked
            ]
        )

    def iter_independent(self, work, items):
        """Use the endpoint's reader limit while preserving usage and cancellation context."""
        from immich_memories.analysis.editorial_reader_concurrency import iter_reader_jobs

        return iter_reader_jobs(self, work, items, limit=reader_concurrency(self.llm_config))

    def _batched(self, prompt: str, max_tokens: int) -> LLMReply | None:
        if self.batch is None:
            return None
        key = batch_prompt_key(self.llm_config, prompt, max_tokens=max_tokens)
        return self.batch.answer_for(key)

    async def _request(self, prompt: str, *, max_tokens: int, ceiling: int | None) -> str:
        try:
            return await self._query(prompt, max_tokens=max_tokens)
        except (KeyError, ValueError):
            retry_tokens = max_tokens * 2
            if ceiling is not None:
                retry_tokens = min(retry_tokens, ceiling)
            if retry_tokens <= max_tokens:
                raise
            return await self._query(prompt, max_tokens=retry_tokens)

    def _read_queued(self, prompt: str, reply: LLMReply, *, max_tokens: int) -> str | None:
        """Put one queued reply on the record, and say whether it answered at all."""
        if self.artifacts:
            self.artifacts.finish(
                self.artifacts.start(
                    prompt,
                    self.llm_config,
                    max_tokens=max_tokens,
                    timeout_seconds=self.timeout_seconds,
                    thinking=self.thinking,
                    transport="batch",
                ),
                raw=reply.content or "",
                billed={
                    "finish_reason": reply.finish_reason,
                    "completion_tokens": reply.completion_tokens,
                    "reasoning_tokens": reply.reasoning_tokens,
                },
            )
        answer = batch_answer(reply)
        if answer is None:
            llm_metrics.record_truncation()
            logger.warning(
                "Queued line wrote no answer (%s; reasoning %d of %d tokens); asking it live",
                reply.finish_reason or "no finish reason",
                reply.reasoning_tokens,
                reply.completion_tokens,
            )
        return answer

    async def _query(self, prompt: str, *, max_tokens: int) -> str:
        queued = self._batched(prompt, max_tokens)
        if queued is not None:
            answer = self._read_queued(prompt, queued, max_tokens=max_tokens)
            if answer is not None:
                return answer
        call = (
            self.artifacts.start(
                prompt,
                self.llm_config,
                max_tokens=max_tokens,
                timeout_seconds=self.timeout_seconds,
                thinking=self.thinking,
                transport="realtime",
            )
            if self.artifacts
            else None
        )
        billed = BilledReply()
        try:
            raw = await query_llm(
                prompt,
                self.llm_config,
                temperature=0.0,
                max_tokens=max_tokens,
                timeout_seconds=self.timeout_seconds,
                thinking=self.thinking,
                cache_path=None,
                transport_observer=billed.watching(watch_provider("reader", self.llm_config)),
                require_complete=True,
            )
        except BaseException as exc:
            if self.artifacts:
                self.artifacts.finish(call, error=exc, billed=billed.as_record())
            raise
        if self.artifacts:
            self.artifacts.finish(call, raw=raw, billed=billed.as_record())
        return raw


def semantic_text_model_identity(config: LLMConfig, *, thinking: bool) -> str:
    """Hash every non-secret provider setting capable of changing a text answer."""
    return text_model_identity(resolved_llm_config(config), thinking=thinking)


def _run_sync(coroutine: object) -> str:
    """Run one text coroutine from synchronous CLI/UI code, even inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)  # type: ignore[arg-type]

    result: list[str] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coroutine))  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread.
            errors.append(exc)

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(runner), daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    if not result:
        raise RuntimeError("text request thread returned no result")
    return result[0]
