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
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_text_identity import text_judgment_key
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


# z.ai's reasoning switch is a level, not a boolean: disabled, low, high, max.
# Measured 2026-09-14 on /api/paas/v4, glm-5.3-flash answers a request carrying
# {"type": "disabled"} with HTTP 400 code 1210, "This model always engages in
# thinking and cannot be disabled; please use low, high, or max", which names
# the levels it will take instead.
THINKING_REQUIRED_CODE = "1210"
LOWEST_THINKING_LEVEL = "low"
_LOWEST_LEVEL_ADAPTATION = "lowest_thinking_level"


def _adaptation_for(error: dict) -> str | None:
    if str(error.get("code", "")) == THINKING_REQUIRED_CODE:
        return _LOWEST_LEVEL_ADAPTATION
    message = str(error.get("message", ""))
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
    if _LOWEST_LEVEL_ADAPTATION in adaptations:
        # The refusal is only ever to "off": a level the model will reason at
        # is left exactly as the caller asked for it.
        thinking = payload.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            payload["thinking"] = {"type": LOWEST_THINKING_LEVEL}


def _announce_adaptation(adaptation: str, before: object, after: object) -> None:
    if adaptation == _LOWEST_LEVEL_ADAPTATION:
        logger.warning(
            "LLM provider refused thinking %s (code %s); retrying once with %s",
            before,
            THINKING_REQUIRED_CODE,
            after,
        )
    else:
        logger.info("LLM server dialect: adapting request (%s)", adaptation)


# The Anthropic dialect asks for an explicit reasoning budget; it must sit
# below max_tokens, which thinking floors to THINKING_MIN_MAX_TOKENS.
ANTHROPIC_THINKING_BUDGET_TOKENS = 2048

# Measured 2026-09-14 against z.ai's /api/anthropic route with glm-5.3-flash.
# Where /api/paas/v4 refuses `{"type": "disabled"}` outright with code 1210,
# this route accepts every level, answers HTTP 200, and then reasons anyway
# when it wants to: a caption-shaped ask at the 140-token cap the callers use
# spent all 140 tokens inside the thinking block and returned no text block at
# all. The reasoning those small asks produced ran 300 to 930 characters, so a
# thousand tokens of headroom carries it and the caller's cap keeps meaning
# what it says about the answer.
ANTHROPIC_REASONING_HEADROOM_TOKENS = 1024

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
        "send_image_detail": False,
    },
}

# The whole GLM-5 line reasons unconditionally, so the cheapest level it
# accepts is what "do not reason" has to mean there; older lines take
# "disabled".
_ALWAYS_REASONING_MODELS = re.compile(r"^glm-5(\.\d+)?(-|$)")


def _zai_off_level(model: str) -> str:
    """The thinking level that stands in for "off" on one z.ai model."""
    return (
        LOWEST_THINKING_LEVEL
        if _ALWAYS_REASONING_MODELS.match(model.strip().lower())
        else "disabled"
    )


def _preset_for(config: LLMConfig) -> dict | None:
    """The named provider's preset, with the model-dependent parts filled in."""
    preset = _PROVIDER_PRESETS.get(config.provider)
    if preset is None or config.provider != "zai":
        return preset
    return {**preset, "no_thinking_params": {"thinking": {"type": _zai_off_level(config.model)}}}


def _preset_dialect(base_url: str) -> str:
    """z.ai serves both dialects on one host, so the base URL's path picks the adapter.

    `.../api/anthropic` wants `/v1/messages`; `.../api/paas/v4` wants
    `/chat/completions`. Posting the OpenAI path to the Anthropic base gets a
    200 carrying `{"code":500,"msg":"404 NOT_FOUND"}`.
    """
    path = urlsplit(base_url).path.rstrip("/")
    return "anthropic" if path.endswith("/anthropic") else "openai-compatible"


def _resolved(config: LLMConfig) -> LLMConfig:
    preset = _preset_for(config)
    if preset is None:
        return config
    fields = type(config).model_fields
    updates: dict = {}
    for name, value in preset.items():
        default = fields[name].get_default(call_default_factory=True)
        if getattr(config, name) == default:
            updates[name] = value
        elif name in ("thinking_params", "no_thinking_params"):
            # The provider's own reasoning switch is not interchangeable with the
            # generic one it was replaced by, so both are sent. A key the user
            # named themselves still wins, which is how a z.ai thinking level
            # other than the preset's is chosen.
            updates[name] = {**value, **getattr(config, name)}
    updates["provider"] = _preset_dialect(updates.get("base_url", config.base_url))
    return config.model_copy(update=updates)


def resolved_llm_config(config: LLMConfig) -> LLMConfig:
    """Return the provider configuration that will actually reach the wire."""
    return _resolved(config)


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
            adaptation = _adaptation_for(error)
        except (TypeError, ValueError):
            _record_invalid_response(transport_observer, resp.status_code)
            raise
        if adaptation is None or adaptation in applied_adaptations:
            return resp
        _observe(transport_observer, 1, "dialect_adaptation", resp.status_code, adaptation)
        adaptations.add(adaptation)
        applied_adaptations.add(adaptation)
        before = payload.get("thinking")
        _apply_adaptations(payload, adaptations)
        _announce_adaptation(adaptation, before, payload.get("thinking"))


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
    check_cancelled()
    llm_config = _resolved(llm_config)
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


def _apply_anthropic_reasoning(
    payload: dict, config: LLMConfig, thinking: bool, max_tokens: int, timeout: int
) -> int:
    """Put the reasoning switch and the tokens it will cost on one payload."""
    if thinking:
        # The dialect wants an explicit budget, and the default temperature.
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        payload["thinking"] = {"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET_TOKENS}
        payload.pop("temperature")
        return max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    if "thinking" not in config.no_thinking_params:
        return timeout
    # Only the native field: LLMConfig's generic default carries Qwen's
    # chat_template_kwargs, which no /v1/messages server understands.
    payload["thinking"] = config.no_thinking_params["thinking"]
    # On this route the switch is a request, not a guarantee, so the answer
    # gets the cap the caller asked for and the reasoning gets its own room.
    payload["max_tokens"] = max_tokens + ANTHROPIC_REASONING_HEADROOM_TOKENS
    return timeout


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
    timeout = _apply_anthropic_reasoning(payload, config, thinking, max_tokens, timeout)
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
            raise ValueError(_reasoning_only(body))
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


def _apply_thinking_budget(payload: dict, config: LLMConfig, max_tokens: int, timeout: int) -> int:
    """Raise the payload token ceiling in place and answer with the raised timeout."""
    payload.update(config.thinking_params)
    thinking_budget = payload.get("thinking_budget")
    if isinstance(thinking_budget, int) and not isinstance(thinking_budget, bool):
        payload["max_tokens"] = max(max_tokens + max(0, thinking_budget), THINKING_MIN_MAX_TOKENS)
    else:
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
    return max(timeout, THINKING_MIN_TIMEOUT_SECONDS)


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
        timeout = _apply_thinking_budget(payload, config, max_tokens, timeout)
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
        llm_metrics.record_truncation()
        message = choice.get("message")
        raise LLMIncompleteResponse(message.get("content") if isinstance(message, dict) else None)
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
        # oMLX occasionally returns a message carrying only its role (an empty answer with
        # finish_reason=stop). Measured 2026-09-02 on the 30B: ~1.6 % of calls. It is the same
        # failure as a truncated answer for the caller, so it is reported the same way.
        raise ValueError("LLM returned empty content")
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


def _reasoning_only(body: dict) -> str:
    """Name what came back instead of an answer, and where it stopped."""
    content = body.get("content")
    kinds = (
        sorted({str(block.get("type")) for block in content}) if isinstance(content, list) else []
    )
    return (
        "LLM provider returned no text block: "
        f"stop_reason {str(body.get('stop_reason'))!r}, blocks {kinds}"
    )


def _no_choices(body: dict) -> str:
    """Some gateways answer 200 with their own error envelope instead of a completion."""
    code = body.get("code")
    message = body.get("msg") or body.get("message")
    if code is None and not message:
        return f"LLM provider returned no choices; body fields: {sorted(body)[:10]}"
    return f"LLM provider returned no choices: code {code}, msg {str(message)[:300]!r}"


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
            raise ValueError(_no_choices(body))
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
