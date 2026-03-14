"""Generic text-only LLM query utility.

Sends a text prompt to the configured LLM provider (Ollama or OpenAI-compatible)
and returns the raw response string. Caller handles JSON parsing and validation.
"""

from __future__ import annotations

import logging

import httpx

from immich_memories.config_models import LLMConfig

logger = logging.getLogger(__name__)


async def query_llm(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float = 0.3,
    max_tokens: int = 500,
    timeout_seconds: int = 30,
) -> str:
    """Send a text-only prompt to the configured LLM and return the response."""
    if llm_config.provider == "ollama":
        return await _query_ollama(prompt, llm_config, temperature, timeout_seconds)
    return await _query_openai(prompt, llm_config, temperature, max_tokens, timeout_seconds)


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
        return resp.json()["response"]  # type: ignore[no-any-return]


async def _query_openai(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    timeout: int,
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
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=payload)
        resp.raise_for_status()
        choices = resp.json()["choices"]
        return choices[0]["message"]["content"]  # type: ignore[no-any-return]
