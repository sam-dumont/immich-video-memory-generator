"""Shared identities for resolved text-model settings and exact text requests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from immich_memories.config_models_llm import LLMConfig

COMPLETE_RETRY_POLICY = "complete-with-one-double-budget-retry-v1"


def text_model_identity(resolved: LLMConfig, *, thinking: bool) -> str:
    """Identify non-secret answer settings after provider defaults are resolved."""
    effective_thinking = bool(thinking and resolved.reasons)
    material = {
        "provider": resolved.provider,
        "endpoint": resolved.base_url.rstrip("/"),
        "model": resolved.model,
        "thinking": effective_thinking,
        "reasoning_params": (
            resolved.thinking_params if effective_thinking else resolved.no_thinking_params
        ),
        "max_tokens_param": resolved.max_tokens_param,
        "drop_params": sorted(resolved.drop_params),
        "extra_params": resolved.extra_params,
        "structured_output": resolved.structured_output,
        "repetition_penalty": resolved.repetition_penalty,
    }
    digest = _digest(material)
    return f"{resolved.model or 'unnamed-model'}@text-{digest[:20]}"


def text_judgment_key(
    resolved: LLMConfig,
    prompt: str,
    *,
    thinking: bool,
    max_tokens: int,
    temperature: float,
    require_complete: bool,
    policy: str = "single-text-request-v2",
    response_format: Mapping[str, Any] | None = None,
) -> str:
    """An incomplete answer cannot satisfy a request requiring completion.

    A gateway request with bounded recovery is distinct from either individual
    transport attempt. Operational settings (credentials, timeout) are excluded.
    """
    return _digest(
        {
            "policy": policy,
            "model": text_model_identity(resolved, thinking=thinking),
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "require_complete": require_complete,
            **({"response_format": response_format} if response_format else {}),
        }
    )


def _digest(material: dict) -> str:
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
