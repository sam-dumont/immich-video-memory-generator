"""The live transport: one prompt, one connection, one answer.

Sends a prompt — with pictures alongside it, where the caller has them — to
the configured LLM provider and returns the raw response string. Caller handles
JSON parsing and validation.

Routing lives here and nowhere else. A second vision call that POSTed
OpenAI-style regardless of the configured provider 404ed on every Ollama server
it met, and the caller read the failure as "not special". What each request
looks like on the wire is `llm_wire`, which `llm_batch` reads too so a queued
answer and a live one are the same answer.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_providers import resolved_llm_config
from immich_memories.analysis.llm_text_identity import text_judgment_key
from immich_memories.analysis.llm_wire import (
    DEFAULT_TEMPERATURE,
    PARAM_ADAPTATIONS,
    THINKING_MIN_TIMEOUT_SECONDS,
    adaptation_for,
    announce_adaptation,
    anthropic_headers,
    anthropic_payload,
    apply_adaptations,
    apply_anthropic_reasoning,
    apply_thinking_budget,
    no_choices_detail,
    openai_answer_content,
    openai_headers,
    openai_payload,
    reasoning_only_detail,
    shape_for_provider,
)
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cancellation import check_cancelled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)


class LLMIncompleteResponse(ValueError):
    """A provider-confirmed output limit, with only the partial final-answer channel."""

    def __init__(self, raw: object = None):
        super().__init__("LLM returned incomplete content")
        self.raw = raw.rsplit("</think>", 1)[-1].lstrip() if isinstance(raw, str) else ""
        if self.raw.startswith("<think>"):
            self.raw = ""  # A truncated reasoning block contains no final-answer evidence.


# Dropped connections are retried this many times before the call fails; a
# provider that stopped answering is announced on the first drop (provider_status).
TRANSPORT_RETRIES = 3


@dataclass(frozen=True)
class LLMTransportAttempt:
    """One actual HTTP POST outcome, kept separate from accepted-reply metrics."""

    attempt: int
    outcome: str
    status_code: int | None
    adaptation: str | None = None


# A stuck server should fail while connecting, not hold the full generation
# budget. httpx timeouts are per-I/O-operation rather than per-request totals,
# so a short connect budget cannot starve a legitimately slow local model.
CONNECT_TIMEOUT_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 30.0
POOL_TIMEOUT_SECONDS = 10.0


async def _post_adapted(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    adaptations: set[str],
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
) -> httpx.Response:
    """POST, negotiating parameter dialects on explicit 400s (one per rule)."""
    # The shared set can grow while sibling requests are in flight. Track what
    # this payload has actually received separately: another request learning
    # an adaptation does not retroactively rewrite our already-built payload.
    applied_adaptations = adaptations.copy()
    transport_drops = 0
    while True:
        try:
            resp = await client.post(url, json=payload)
        except httpx.TransportError:
            # A peer-closed connection or dropped read is transient: the server
            # is restarting a worker or shedding load, not refusing the request.
            # One such drop killed a 26-minute run (owner ruling 2026-09-01:
            # never fatal). Backoff, retry, and only then give up.
            transport_drops += 1
            _observe(transport_observer, transport_drops, "connection_error", None)
            if transport_drops >= TRANSPORT_RETRIES:
                raise
            await asyncio.sleep(2.0 * transport_drops)
            continue
        except httpx.HTTPError:
            _observe(transport_observer, 1, "connection_error", None)
            raise
        if resp.status_code != 400:
            return resp
        try:
            error = _response_body(resp).get("error", {})
            if not isinstance(error, dict):
                raise TypeError("LLM error body is not an object")
            adaptation = adaptation_for(error)
        except (TypeError, ValueError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        if adaptation is None or adaptation in applied_adaptations:
            return resp
        _observe(transport_observer, 1, "dialect_adaptation", resp.status_code, adaptation)
        adaptations.add(adaptation)
        applied_adaptations.add(adaptation)
        before = payload.get("thinking")
        apply_adaptations(payload, adaptations)
        announce_adaptation(adaptation, before, payload.get("thinking"))


def build_llm_timeout(read_timeout: float) -> httpx.Timeout:
    """Per-phase timeout: long read budget, short connect/write/pool."""
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=read_timeout,
        write=WRITE_TIMEOUT_SECONDS,
        pool=POOL_TIMEOUT_SECONDS,
    )


async def query_llm(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 500,
    timeout_seconds: int = 30,
    thinking: bool = False,
    images: Sequence[bytes] = (),
    image_detail: str = "low",
    cache_path: Path | None = None,
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
    require_complete: bool = False,
) -> str:
    """Send a prompt, optionally with JPEG images, and return the response.

    cache_path opts this call into reuse: an identical question to an identical
    model gets the answer it got before, rather than being paid for again.
    Deliberately opt-in — a health probe must reach the server every time — and
    deliberately refused for image-bearing calls, whose pictures a prompt hash
    cannot see.

    thinking=True asks a reasoning model to reason before answering — only
    honored when the config says the server supports it (llm.thinking), never
    on the Ollama path, and never alongside images: multi-image reasoning is a
    measured runaway, and bulk vision is the fast tier by design. Reserve it
    for judgement calls: measured cost is 5-10x latency and 10-20x tokens.
    """
    check_cancelled()
    llm_config = resolved_llm_config(llm_config)
    # A prompt hash cannot see the pictures, so an image-bearing call with a
    # fixed prompt template — "one line per picture, in order" — would key
    # identically for two entirely different days and serve one the other's
    # answer. Vision is cached per asset upstream, where the key is the asset
    # id, so there is nothing for this layer to add and everything to get
    # wrong.
    key = (
        text_judgment_key(
            llm_config,
            prompt,
            thinking=thinking,
            max_tokens=max_tokens,
            temperature=temperature,
            require_complete=require_complete,
        )
        if not images
        else None
    )
    remembered = _remembered(cache_path, key) if key is not None else None
    if remembered is not None:
        logger.debug("Reusing the answer to an identical question")
        llm_metrics.record_cache_hit()
        return remembered
    attempt_number = 0

    def observe(attempt: LLMTransportAttempt) -> None:
        nonlocal attempt_number
        attempt_number += 1
        if transport_observer is not None:
            transport_observer(
                LLMTransportAttempt(
                    attempt_number, attempt.outcome, attempt.status_code, attempt.adaptation
                )
            )

    started = time.monotonic()
    effective_thinking = bool(thinking and llm_config.reasons and not images)
    total_timeout = float(timeout_seconds)
    if effective_thinking:
        total_timeout = max(total_timeout, float(THINKING_MIN_TIMEOUT_SECONDS))
    try:
        async with asyncio.timeout(total_timeout):
            answer = await _dispatch(
                prompt,
                llm_config,
                temperature,
                max_tokens,
                timeout_seconds,
                thinking,
                images,
                image_detail,
                observe,
                require_complete,
            )
    finally:
        # In `finally` so a failed call still shows the time it burned; a run
        # that spent four minutes on a dead server should not read as free.
        llm_metrics.record_wall(time.monotonic() - started)
    if key is not None:
        _remember(cache_path, key, answer)
    return answer


def _remembered(cache_path: Path | None, key: str) -> str | None:
    """Read the exact transport request, closing the connection on every path."""
    if cache_path is None:
        return None
    from immich_memories.cache.judgment_cache import JudgmentCache

    cache = JudgmentCache(cache_path)
    try:
        return cache.answer_for(key)
    finally:
        cache.close()


def _remember(cache_path: Path | None, key: str, answer: str) -> None:
    """Keep an answer. Silence is never kept — a failed call must not stick."""
    if cache_path is None or not answer:
        return
    from immich_memories.cache.judgment_cache import JudgmentCache

    cache = JudgmentCache(cache_path)
    try:
        cache.remember(key, answer)
    finally:
        cache.close()


async def _dispatch(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout_seconds: int,
    thinking: bool,
    images: Sequence[bytes],
    image_detail: str,
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
    require_complete: bool = False,
) -> str:
    if llm_config.provider == "ollama":
        return await _query_ollama(
            prompt,
            llm_config,
            temperature,
            max_tokens,
            timeout_seconds,
            images,
            transport_observer,
            require_complete,
        )
    think = thinking and llm_config.reasons and not images
    if llm_config.provider == "anthropic":
        return await _query_anthropic(
            prompt,
            llm_config,
            temperature,
            max_tokens,
            timeout_seconds,
            think,
            images,
            transport_observer,
            require_complete,
        )
    return await _query_openai(
        prompt,
        llm_config,
        temperature,
        max_tokens,
        timeout_seconds,
        think,
        images,
        image_detail,
        transport_observer,
        require_complete,
    )


async def _query_ollama(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout: int,
    images: Sequence[bytes] = (),
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
    require_complete: bool = False,
) -> str:
    base_url = config.base_url.rstrip("/")
    payload: dict = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    # Ollama takes bare base64 in its own field, not a data: URI in a message.
    if images:
        payload["images"] = [base64.b64encode(image).decode("utf-8") for image in images]
    # Ollama keeps its per-request knobs (num_ctx, num_predict) under `options`,
    # so extras aimed at that key merge into it instead of replacing temperature.
    for name, value in config.extra_params.items():
        if name == "options":
            payload["options"].update(value)
        else:
            payload[name] = value
    payload["options"]["num_predict"] = max_tokens
    async with httpx.AsyncClient(timeout=build_llm_timeout(float(timeout))) as client:
        try:
            resp = await client.post(f"{base_url}/api/generate", json=payload)
        except httpx.HTTPError:
            _observe(transport_observer, 1, "connection_error", None)
            raise
        _ensure_success(resp, transport_observer)
        try:
            body = _response_body(resp)
        except (TypeError, ValueError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        llm_metrics.record_reply(
            prompt_tokens=body.get("prompt_eval_count", 0) or 0,
            completion_tokens=body.get("eval_count", 0) or 0,
        )
        if require_complete and body.get("done_reason") in {"length", "max_tokens", "truncated"}:
            _observe(transport_observer, 1, "incomplete", resp.status_code)
            llm_metrics.record_truncation()
            raise LLMIncompleteResponse(body.get("response"))
        try:
            raw_text = body["response"]
            if not isinstance(raw_text, str):
                raise TypeError("Ollama response content is not text")
        except (KeyError, TypeError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        _observe(transport_observer, 1, "response", resp.status_code)
        return raw_text


async def _query_anthropic(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking: bool = False,
    images: Sequence[bytes] = (),
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
    require_complete: bool = False,
) -> str:
    """Native /v1/messages dialect: Claude, or z.ai's Anthropic endpoint."""
    base_url = config.base_url.rstrip("/")
    headers = anthropic_headers(config)
    payload = anthropic_payload(prompt, config, temperature, max_tokens, images)
    timeout = apply_anthropic_reasoning(payload, config, thinking, max_tokens, timeout)
    shape_for_provider(payload, config)
    async with httpx.AsyncClient(
        timeout=build_llm_timeout(float(timeout)), headers=headers
    ) as client:
        try:
            resp = await client.post(f"{base_url}/v1/messages", json=payload)
        except httpx.HTTPError:
            _observe(transport_observer, 1, "connection_error", None)
            raise
        _ensure_success(resp, transport_observer)
        body, usage = _anthropic_usage(resp, transport_observer)
        cached_input = usage.get("cache_read_input_tokens", 0) or 0
        cache_creation_input = usage.get("cache_creation_input_tokens", 0) or 0
        llm_metrics.record_reply(
            # Anthropic-style usage reports uncached, cache-read, and
            # cache-write input separately. Normalize prompt_tokens to the
            # same total-input meaning OpenAI reports while retaining the
            # discounted cache-read subset.
            prompt_tokens=(usage.get("input_tokens", 0) or 0) + cached_input + cache_creation_input,
            cached_prompt_tokens=cached_input,
            completion_tokens=usage.get("output_tokens", 0) or 0,
        )
        raw_text = _anthropic_answer(body, resp, transport_observer)
        truncated = body.get("stop_reason") == "max_tokens"
        if truncated and require_complete:
            _observe(transport_observer, 1, "incomplete", resp.status_code)
            llm_metrics.record_truncation()
            raise LLMIncompleteResponse(raw_text or "")
        if thinking and truncated:
            _observe(transport_observer, 1, "thinking_fallback", resp.status_code)
            llm_metrics.record_truncation()
            logger.warning("Thinking hit the token budget; retrying without thinking")
            return await _query_anthropic(
                prompt,
                config,
                temperature,
                max_tokens,
                timeout,
                thinking=False,
                images=images,
                transport_observer=transport_observer,
                require_complete=require_complete,
            )
        if raw_text is None:
            _record_invalid_response(transport_observer, resp.status_code)
            raise ValueError(reasoning_only_detail(body))
        _observe(transport_observer, 1, "response", resp.status_code)
        return raw_text


async def _query_openai(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking: bool = False,
    images: Sequence[bytes] = (),
    image_detail: str = "low",
    transport_observer: Callable[[LLMTransportAttempt], None] | None = None,
    require_complete: bool = False,
) -> str:
    base_url = config.base_url.rstrip("/")
    headers = openai_headers(config)
    payload = openai_payload(prompt, config, temperature, max_tokens, images, image_detail)
    if thinking:
        timeout = apply_thinking_budget(payload, config, max_tokens, timeout)
    elif config.no_thinking_params:
        # Not asking to think is not the same as asking not to. On a server
        # whose template reasons by default, every bulk call reasoned anyway
        # at the caller's small budget and came back truncated mid-thought.
        # Gated on the switch itself, never on llm.thinking: a user who turns
        # reasoning off still has a server that reasons unless it is told.
        payload.update(config.no_thinking_params)
    shape_for_provider(payload, config)
    adaptations = PARAM_ADAPTATIONS.setdefault((base_url, config.model), set())
    apply_adaptations(payload, adaptations)
    # Retry up to 3x — some models (Qwen/mlx-vlm) return null content
    # Per-phase, not a scalar: a stuck server should fail while connecting
    # rather than hold the whole generation budget on one read.
    async with httpx.AsyncClient(
        timeout=build_llm_timeout(float(timeout)), headers=headers
    ) as client:
        for attempt in range(3):
            resp = await _post_adapted(
                client, f"{base_url}/chat/completions", payload, adaptations, transport_observer
            )
            _ensure_success(resp, transport_observer)
            content, retry_without_thinking = _interpret_openai_response(
                resp,
                thinking,
                require_complete,
                transport_observer,
                attempt + 1,
            )
            if retry_without_thinking:
                llm_metrics.record_truncation()
                # Truncation mid-think leaves the unfinished reasoning in the
                # content channel — unparseable. A fast answer beats no answer.
                logger.warning("Thinking hit the token budget; retrying without thinking")
                return await _query_openai(
                    prompt,
                    config,
                    temperature,
                    max_tokens,
                    timeout,
                    thinking=False,
                    images=images,
                    image_detail=image_detail,
                    transport_observer=transport_observer,
                    require_complete=require_complete,
                )
            if content is not None:
                _observe(transport_observer, attempt + 1, "response", resp.status_code)
                return content
            _observe(transport_observer, attempt + 1, "null_content", resp.status_code)
            logger.debug("LLM null content (attempt %d/3)", attempt + 1)
    msg = "LLM returned null content after 3 retries"
    raise ValueError(msg)


def _openai_completion(
    choice: dict,
    thinking: bool,
    require_complete: bool,
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
    status_code: int,
) -> tuple[str | None, bool]:
    truncated = choice.get("finish_reason") == "length"
    if truncated and require_complete:
        _observe(observer, attempt, "incomplete", status_code)
        llm_metrics.record_truncation()
        message = choice.get("message")
        raise LLMIncompleteResponse(message.get("content") if isinstance(message, dict) else None)
    if truncated and thinking:
        _observe(observer, attempt, "thinking_fallback", status_code)
        return None, True
    message = choice["message"]
    if not isinstance(message, dict):
        raise TypeError("OpenAI response message is not an object")
    return openai_answer_content(message), False


def _anthropic_usage(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> tuple[dict, dict]:
    """Decode the native Anthropic body far enough to retain usage metrics."""
    try:
        body = _response_body(response)
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("Anthropic usage is not an object")
    except (TypeError, ValueError):
        _record_invalid_response(observer, response.status_code)
        raise
    return body, usage


def _anthropic_answer(
    body: dict, response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> str | None:
    """The first text block, or None when the reply carried reasoning only.

    A reasoning model puts one or more `thinking` blocks in front of its
    answer, and on z.ai's route it does so whether or not it was asked to, so
    a reply with no text block at all is a normal shape here rather than a
    malformed body.
    """
    try:
        content = body["content"]
        if not isinstance(content, list):
            raise TypeError("Anthropic response content is not a list")
        texts = [block["text"] for block in content if block.get("type") == "text"]
    except (KeyError, TypeError, AttributeError):
        _record_invalid_response(observer, response.status_code)
        raise
    return texts[0] if texts else None


def _interpret_openai_response(
    response: httpx.Response,
    thinking: bool,
    require_complete: bool,
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
) -> tuple[str | None, bool]:
    """Parse one OpenAI-style reply and preserve its completed-post outcome."""
    try:
        body = _response_body(response)
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "unknown"))[:80]
            message = str(error.get("message", "provider returned an error"))[:300]
            raise ValueError(f"LLM provider error {code}: {message}")
        if "choices" not in body:
            raise ValueError(no_choices_detail(body))
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        if not isinstance(choice, dict) or not isinstance(usage, dict):
            raise TypeError("OpenAI response has an invalid body shape")
    except (KeyError, TypeError, ValueError, IndexError):
        _record_invalid_response(observer, response.status_code)
        raise
    llm_metrics.record_reply(
        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
        cached_prompt_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        or 0,
        completion_tokens=usage.get("completion_tokens", 0) or 0,
    )
    try:
        content, retry_without_thinking = _openai_completion(
            choice, thinking, require_complete, observer, attempt, response.status_code
        )
    except (KeyError, TypeError, AttributeError):
        _record_invalid_response(observer, response.status_code)
        raise
    return content, retry_without_thinking


def _observe(
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
    outcome: str,
    status_code: int | None,
    adaptation: str | None = None,
) -> None:
    if observer is not None:
        observer(LLMTransportAttempt(attempt, outcome, status_code, adaptation))


def _response_body(response: httpx.Response) -> dict:
    """Decode one provider response as the object every dialect requires."""
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("LLM response body is not an object")
    return body


def _record_invalid_response(
    observer: Callable[[LLMTransportAttempt], None] | None, status_code: int
) -> None:
    """Trace the one completed POST whose content could not be parsed."""
    _observe(observer, 1, "invalid_response", status_code)


# Enough of a refused call to act on, short enough to sit on a warning line.
PROVIDER_MESSAGE_CHARS = 300


def _provider_error_detail(response: httpx.Response) -> str:
    """The provider's own code and message, bounded, or "" when it named neither.

    httpx's HTTPStatusError text is the status line and a link to MDN. The
    reason a call was refused exists only in the body, so without this the
    operator reads "400 Bad Request" and has nothing to go on.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, str):
        # Ollama's refusals are a bare sentence under `error`, with no code.
        return f"message {error[:PROVIDER_MESSAGE_CHARS]!r}"
    named = error if isinstance(error, dict) else body
    code = named.get("code")
    message = named.get("message") or named.get("msg")
    if code is None and not message:
        return ""
    return f"code {code}, message {str(message)[:PROVIDER_MESSAGE_CHARS]!r}"


def _ensure_success(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> None:
    """Raise on a 4xx or 5xx, carrying the provider's own code and message."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _observe(observer, 1, "http_error", response.status_code)
        detail = _provider_error_detail(response)
        if not detail:
            raise
        summary = f"{str(exc).splitlines()[0]} - provider said {detail}"
        raise httpx.HTTPStatusError(summary, request=exc.request, response=response) from exc
