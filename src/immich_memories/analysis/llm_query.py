"""Generic LLM query utility.

Sends a prompt — with pictures alongside it, where the caller has them — to
the configured LLM provider (Ollama or OpenAI-compatible) and returns the raw
response string. Caller handles JSON parsing and validation.

Provider routing lives here and nowhere else. A second vision call that
POSTed OpenAI-style regardless of the configured provider 404ed on every
Ollama server it met, and the caller read the failure as "not special".
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.config_models_llm import LLMConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)


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

# Measured on the live endpoint: at 500 max_tokens a thinking call truncates
# mid-think and the reasoning leaks into the content channel; ~600-2300 chars
# of reasoning plus the answer fits comfortably under 4000. Latency ran
# 30-134s where non-thinking answered in 4-7s, so the read budget rises too.
THINKING_MIN_MAX_TOKENS = 4000
THINKING_MIN_TIMEOUT_SECONDS = 180

# Measured on real OpenAI: gpt-5-family models reject `max_tokens` (they want
# `max_completion_tokens`) and any temperature but the default. The 400 body
# names the offending parameter, so the call adapts once and remembers per
# (server, model) for the rest of the process.
_PARAM_ADAPTATIONS: dict[tuple[str, str], set[str]] = {}


def _adaptation_for(message: str) -> str | None:
    if "chat_template_kwargs" in message:
        return "no_chat_template_kwargs"
    if "max_tokens" in message and "max_completion_tokens" in message:
        return "max_completion_tokens"
    if "temperature" in message and ("not support" in message or "Unsupported" in message):
        return "default_temperature"
    return None


def _apply_adaptations(payload: dict, adaptations: set[str]) -> None:
    if "no_chat_template_kwargs" in adaptations:
        payload.pop("chat_template_kwargs", None)
    if "max_completion_tokens" in adaptations and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if "default_temperature" in adaptations:
        payload.pop("temperature", None)


# The Anthropic dialect asks for an explicit reasoning budget; it must sit
# below max_tokens, which thinking floors to THINKING_MIN_MAX_TOKENS.
ANTHROPIC_THINKING_BUDGET_TOKENS = 2048

# Named providers = the generic adapter plus the provider's URL and reasoning
# dialect, applied only where the user left the field at its default.
_PROVIDER_PRESETS: dict[str, dict] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "thinking_params": {"reasoning_effort": "medium"},
        "no_thinking_params": {},
    },
    "zai": {
        "base_url": "https://api.z.ai/api/paas/v4",
        "thinking_params": {"thinking": {"type": "enabled"}},
        "no_thinking_params": {"thinking": {"type": "disabled"}},
        "send_image_detail": False,
    },
}


def _resolved(config: LLMConfig) -> LLMConfig:
    preset = _PROVIDER_PRESETS.get(config.provider)
    if preset is None:
        return config
    fields = type(config).model_fields
    updates: dict = {"provider": "openai-compatible"}
    for name, value in preset.items():
        default = fields[name].get_default(call_default_factory=True)
        if getattr(config, name) == default:
            updates[name] = value
    return config.model_copy(update=updates)


def _shape_for_provider(payload: dict, config: LLMConfig) -> None:
    """Apply the configured provider dialect before any auto-negotiation."""
    if config.max_tokens_param != "max_tokens" and "max_tokens" in payload:
        payload[config.max_tokens_param] = payload.pop("max_tokens")
    for name in config.drop_params:
        payload.pop(name, None)
    payload.update(config.extra_params)


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
    while True:
        try:
            resp = await client.post(url, json=payload)
        except httpx.HTTPError:
            _observe(transport_observer, 1, "connection_error", None)
            raise
        if resp.status_code != 400:
            return resp
        try:
            error = _response_body(resp).get("error", {})
            if not isinstance(error, dict):
                raise TypeError("LLM error body is not an object")
            adaptation = _adaptation_for(str(error.get("message", "")))
        except (TypeError, ValueError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        if adaptation is None or adaptation in applied_adaptations:
            return resp
        _observe(transport_observer, 1, "dialect_adaptation", resp.status_code, adaptation)
        adaptations.add(adaptation)
        applied_adaptations.add(adaptation)
        _apply_adaptations(payload, adaptations)
        logger.info("LLM server dialect: adapting request (%s)", adaptation)


def build_llm_timeout(read_timeout: float) -> httpx.Timeout:
    """Per-phase timeout: long read budget, short connect/write/pool."""
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=read_timeout,
        write=WRITE_TIMEOUT_SECONDS,
        pool=POOL_TIMEOUT_SECONDS,
    )


# Every model call this project makes is a judgement it wants back the same way
# twice: a description that feeds a decision, or a decision itself. Sampling was
# measured turning one real pack's answer over four repeats into four different
# answers, one of which named all 105 tiles. Greedy decoding also makes the
# judgement cache honest -- a banked answer is what re-asking would return.
DEFAULT_TEMPERATURE = 0.0


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
    llm_config = _resolved(llm_config)
    # A prompt hash cannot see the pictures, so an image-bearing call with a
    # fixed prompt template — "one line per picture, in order" — would key
    # identically for two entirely different days and serve one the other's
    # answer. Vision is cached per asset upstream, where the key is the asset
    # id, so there is nothing for this layer to add and everything to get
    # wrong.
    remembered = _remembered(cache_path, llm_config, prompt, thinking) if not images else None
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
    effective_thinking = bool(thinking and llm_config.thinking and not images)
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
    if not images:
        _remember(cache_path, llm_config, prompt, thinking, answer)
    return answer


def _cache_key(llm_config: LLMConfig, prompt: str, thinking: bool) -> str:
    from immich_memories.cache.judgment_cache import judgment_key

    effective_thinking = bool(thinking and getattr(llm_config, "thinking", False))
    thinking_identity = (
        json.dumps(
            getattr(llm_config, "thinking_params", {}),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        if effective_thinking
        else ""
    )
    return judgment_key(
        model=getattr(llm_config, "model", None),
        prompt=prompt,
        thinking=effective_thinking,
        thinking_identity=thinking_identity,
    )


def _remembered(
    cache_path: Path | None, llm_config: LLMConfig, prompt: str, thinking: bool
) -> str | None:
    """What this exact question was answered with before, if it was."""
    if cache_path is None:
        return None
    from immich_memories.cache.judgment_cache import JudgmentCache

    return JudgmentCache(cache_path).answer_for(_cache_key(llm_config, prompt, thinking))


def _remember(
    cache_path: Path | None, llm_config: LLMConfig, prompt: str, thinking: bool, answer: str
) -> None:
    """Keep an answer. Silence is never kept — a failed call must not stick."""
    if cache_path is None or not answer:
        return
    from immich_memories.cache.judgment_cache import JudgmentCache

    JudgmentCache(cache_path).remember(_cache_key(llm_config, prompt, thinking), answer)


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
    think = thinking and llm_config.thinking and not images
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
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            _observe(transport_observer, 1, "http_error", resp.status_code)
            raise
        try:
            body = _response_body(resp)
        except (TypeError, ValueError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        if require_complete and body.get("done_reason") in {"length", "max_tokens", "truncated"}:
            _observe(transport_observer, 1, "incomplete", resp.status_code)
            raise ValueError("LLM returned incomplete content")
        llm_metrics.record_reply(
            prompt_tokens=body.get("prompt_eval_count", 0) or 0,
            completion_tokens=body.get("eval_count", 0) or 0,
        )
        try:
            raw_text = body["response"]
            if not isinstance(raw_text, str):
                raise TypeError("Ollama response content is not text")
        except (KeyError, TypeError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        _observe(transport_observer, 1, "response", resp.status_code)
        return raw_text


def _anthropic_content(prompt: str, images: Sequence[bytes]) -> str | list[dict]:
    """The message body: a bare string without pictures, blocks with them."""
    if not images:
        return prompt
    return [
        *(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(image).decode("utf-8"),
                },
            }
            for image in images
        ),
        {"type": "text", "text": prompt},
    ]


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
    headers = {"anthropic-version": "2023-06-01"}
    if config.api_key:
        headers["x-api-key"] = config.api_key
    payload: dict = {
        "model": config.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": _anthropic_content(prompt, images)}],
        "temperature": temperature,
    }
    if thinking:
        # The dialect wants an explicit budget, and the default temperature.
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        payload["thinking"] = {"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET_TOKENS}
        payload.pop("temperature")
        timeout = max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    elif "thinking" in config.no_thinking_params:
        # Some Anthropic-compatible gateways reason by default. Only copy the
        # native field: LLMConfig's generic default contains Qwen's
        # chat_template_kwargs, which Anthropic itself does not understand.
        payload["thinking"] = config.no_thinking_params["thinking"]
    async with httpx.AsyncClient(
        timeout=build_llm_timeout(float(timeout)), headers=headers
    ) as client:
        try:
            resp = await client.post(f"{base_url}/v1/messages", json=payload)
        except httpx.HTTPError:
            _observe(transport_observer, 1, "connection_error", None)
            raise
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            _observe(transport_observer, 1, "http_error", resp.status_code)
            raise
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
        if body.get("stop_reason") == "max_tokens" and require_complete:
            _observe(transport_observer, 1, "incomplete", resp.status_code)
            raise ValueError("LLM returned incomplete content")
        if thinking and body.get("stop_reason") == "max_tokens":
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
        _observe(transport_observer, 1, "response", resp.status_code)
        return raw_text


def _openai_content(
    prompt: str, images: Sequence[bytes], image_detail: str | None = "low"
) -> str | list[dict]:
    """The message body: a bare string without pictures, parts with them."""
    if not images:
        return prompt
    return [
        {"type": "text", "text": prompt},
        *(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("utf-8"),
                    **({"detail": image_detail} if image_detail is not None else {}),
                },
            }
            for image in images
        ),
    ]


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
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": _openai_content(
                    prompt,
                    images,
                    image_detail if config.send_image_detail else None,
                ),
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking:
        payload.update(config.thinking_params)
        thinking_budget = payload.get("thinking_budget")
        if isinstance(thinking_budget, int) and not isinstance(thinking_budget, bool):
            payload["max_tokens"] = max(
                max_tokens + max(0, thinking_budget), THINKING_MIN_MAX_TOKENS
            )
        else:
            payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        timeout = max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    elif config.no_thinking_params:
        # Not asking to think is not the same as asking not to. On a server
        # whose template reasons by default, every bulk call reasoned anyway
        # at the caller's small budget and came back truncated mid-thought.
        # Gated on the switch itself, never on llm.thinking: a user who turns
        # reasoning off still has a server that reasons unless it is told.
        payload.update(config.no_thinking_params)
    _shape_for_provider(payload, config)
    adaptations = _PARAM_ADAPTATIONS.setdefault((base_url, config.model), set())
    _apply_adaptations(payload, adaptations)
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
        raise ValueError("LLM returned incomplete content")
    if truncated and thinking:
        _observe(observer, attempt, "thinking_fallback", status_code)
        return None, True
    message = choice["message"]
    if not isinstance(message, dict):
        raise TypeError("OpenAI response message is not an object")
    return _openai_answer_content(message), False


def _openai_answer_content(message: dict) -> str | None:
    """Return only the final answer, never the model's private reasoning."""
    if "content" not in message:
        raise KeyError("OpenAI response message has no content field")
    content = message["content"]
    reasoning = message.get("reasoning_content")
    if content is not None and not isinstance(content, str):
        raise TypeError("OpenAI response content is not text")
    if reasoning is not None and not isinstance(reasoning, str):
        raise TypeError("OpenAI response reasoning_content is not text")
    if content and "</think>" in content:
        # Older compatible servers sometimes inline the reasoning channel.
        # The final close marker is the boundary; only what follows is an answer.
        content = content.rsplit("</think>", 1)[1].lstrip()
    return content


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
) -> str:
    """Validate the Anthropic content after its parseable usage was counted."""
    try:
        content = body["content"]
        if not isinstance(content, list):
            raise TypeError("Anthropic response content is not a list")
        return "".join(block.get("text", "") for block in content if block.get("type") == "text")
    except (KeyError, TypeError, AttributeError):
        _record_invalid_response(observer, response.status_code)
        raise


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


def _ensure_success(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        _observe(observer, 1, "http_error", response.status_code)
        raise
