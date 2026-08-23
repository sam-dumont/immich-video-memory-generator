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

from immich_memories.config_models import LLMConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# A stuck server should fail while connecting, not hold the full generation
# budget. httpx timeouts are per-I/O-operation rather than per-request totals,
# so a short connect budget cannot starve a legitimately slow local model.
CONNECT_TIMEOUT_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 30.0
POOL_TIMEOUT_SECONDS = 10.0


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
    images: Sequence[bytes] = (),
) -> str:
    """Send a prompt, optionally with JPEG images, and return the response."""
    if llm_config.provider == "ollama":
        return await _query_ollama(prompt, llm_config, temperature, timeout_seconds, images)
    return await _query_openai(prompt, llm_config, temperature, max_tokens, timeout_seconds, images)


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
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]


def _openai_content(prompt: str, images: Sequence[bytes]) -> str | list[dict]:
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
                    # Low detail: these are thumbnails, and the question is what
                    # the day was, not what is written on a sign in it.
                    "detail": "low",
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
    images: Sequence[bytes] = (),
) -> str:
    base_url = config.base_url.rstrip("/")
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": _openai_content(prompt, images)}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Retry up to 3x — some models (Qwen/mlx-vlm) return null content
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for attempt in range(3):
            resp = await client.post(f"{base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if content is not None:
                return content
            logger.debug("LLM null content (attempt %d/3)", attempt + 1)
    msg = "LLM returned null content after 3 retries"
    raise ValueError(msg)
