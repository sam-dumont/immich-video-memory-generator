"""Generic text-only LLM query utility.

Sends a text prompt to the configured LLM provider (Ollama or OpenAI-compatible)
and returns the raw response string. Caller handles JSON parsing and validation.
"""

from __future__ import annotations

import logging

import httpx

from immich_memories.config_models import LLMConfig

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
) -> str:
    """Send a text-only prompt to the configured LLM and return the response.

    thinking=True asks a reasoning model to reason before answering — only
    honored when the config says the server supports it (llm.thinking), and
    never on the Ollama path. Reserve it for judgement calls: measured cost is
    5-10x latency and 10-20x completion tokens.
    """
    if llm_config.provider == "ollama":
        return await _query_ollama(prompt, llm_config, temperature, timeout_seconds)
    think = thinking and llm_config.thinking
    return await _query_openai(prompt, llm_config, temperature, max_tokens, timeout_seconds, think)


async def _query_ollama(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    timeout: int,
) -> str:
    base_url = config.base_url.rstrip("/")
    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]


async def _query_openai(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking: bool = False,
) -> str:
    base_url = config.base_url.rstrip("/")
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking:
        payload.update(config.thinking_params)
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        timeout = max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    adaptations = _PARAM_ADAPTATIONS.setdefault((base_url, config.model), set())
    _apply_adaptations(payload, adaptations)
    # Retry up to 3x — some models (Qwen/mlx-vlm) return null content
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for attempt in range(3):
            resp = await _post_adapted(client, f"{base_url}/chat/completions", payload, adaptations)
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            if thinking and choice.get("finish_reason") == "length":
                # Truncation mid-think leaves the unfinished reasoning in the
                # content channel — unparseable. A fast answer beats no answer.
                logger.warning("Thinking hit the token budget; retrying without thinking")
                return await _query_openai(
                    prompt, config, temperature, max_tokens, timeout, thinking=False
                )
            content = choice["message"]["content"]
            if content is not None:
                return content
            logger.debug("LLM null content (attempt %d/3)", attempt + 1)
    msg = "LLM returned null content after 3 retries"
    raise ValueError(msg)
