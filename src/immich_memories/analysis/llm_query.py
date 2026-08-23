"""Generic LLM query utility.

Sends a prompt — with pictures alongside it, where the caller has them — to
the configured LLM provider (Ollama or OpenAI-compatible) and returns the raw
response string. Caller handles JSON parsing and validation.

Provider routing lives here and nowhere else. A second vision call that
POSTed OpenAI-style regardless of the configured provider 404ed on every
Ollama server it met, and the caller read the failure as "not special".
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

import httpx

from immich_memories.config_models_llm import LLMConfig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

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
    if "max_tokens" in message and "max_completion_tokens" in message:
        return "max_completion_tokens"
    if "temperature" in message and ("not support" in message or "Unsupported" in message):
        return "default_temperature"
    return None


def _apply_adaptations(payload: dict, adaptations: set[str]) -> None:
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
    },
    "zai": {
        "base_url": "https://api.z.ai/api/paas/v4",
        "thinking_params": {"thinking": {"type": "enabled"}},
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
    client: httpx.AsyncClient, url: str, payload: dict, adaptations: set[str]
) -> httpx.Response:
    """POST, negotiating parameter dialects on explicit 400s (one per rule)."""
    while True:
        resp = await client.post(url, json=payload)
        if resp.status_code != 400:
            return resp
        adaptation = _adaptation_for(resp.json().get("error", {}).get("message", ""))
        if adaptation is None or adaptation in adaptations:
            return resp
        adaptations.add(adaptation)
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


async def query_llm(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float = 0.3,
    max_tokens: int = 500,
    timeout_seconds: int = 30,
    thinking: bool = False,
    images: Sequence[bytes] = (),
    image_detail: str = "low",
    cache_path: Path | None = None,
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
        return remembered
    answer = await _dispatch(
        prompt,
        llm_config,
        temperature,
        max_tokens,
        timeout_seconds,
        thinking,
        images,
        image_detail,
    )
    if not images:
        _remember(cache_path, llm_config, prompt, thinking, answer)
    return answer


def _cache_key(llm_config: LLMConfig, prompt: str, thinking: bool) -> str:
    from immich_memories.cache.judgment_cache import judgment_key

    return judgment_key(
        model=getattr(llm_config, "model", None),
        prompt=prompt,
        thinking=bool(thinking and getattr(llm_config, "thinking", False)),
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
) -> str:
    if llm_config.provider == "ollama":
        return await _query_ollama(prompt, llm_config, temperature, timeout_seconds, images)
    think = thinking and llm_config.thinking and not images
    if llm_config.provider == "anthropic":
        return await _query_anthropic(
            prompt, llm_config, temperature, max_tokens, timeout_seconds, think, images
        )
    return await _query_openai(
        prompt, llm_config, temperature, max_tokens, timeout_seconds, think, images, image_detail
    )


async def _query_ollama(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    timeout: int,
    images: Sequence[bytes] = (),
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
    async with httpx.AsyncClient(timeout=build_llm_timeout(float(timeout))) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]


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
    async with httpx.AsyncClient(
        timeout=build_llm_timeout(float(timeout)), headers=headers
    ) as client:
        resp = await client.post(f"{base_url}/v1/messages", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if thinking and body.get("stop_reason") == "max_tokens":
            logger.warning("Thinking hit the token budget; retrying without thinking")
            return await _query_anthropic(
                prompt, config, temperature, max_tokens, timeout, thinking=False, images=images
            )
        return "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text")


def _openai_content(
    prompt: str, images: Sequence[bytes], image_detail: str = "low"
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
                    # Thumbnails, and the question is what a day or a photo was,
                    # not what is written on a sign in it. Callers with a
                    # configured preference pass their own.
                    "detail": image_detail,
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
) -> str:
    base_url = config.base_url.rstrip("/")
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": _openai_content(prompt, images, image_detail)}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking:
        payload.update(config.thinking_params)
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        timeout = max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
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
            resp = await _post_adapted(client, f"{base_url}/chat/completions", payload, adaptations)
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            if thinking and choice.get("finish_reason") == "length":
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
                )
            content = choice["message"]["content"]
            if content is not None:
                return content
            logger.debug("LLM null content (attempt %d/3)", attempt + 1)
    msg = "LLM returned null content after 3 retries"
    raise ValueError(msg)
