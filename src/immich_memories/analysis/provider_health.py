"""Bounded, credential-safe health state for optional analysis providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ProviderState(StrEnum):
    """Stable provider states safe to expose in logs and preflight output."""

    READY = "ready"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "authentication_failed"
    ROUTE_MISSING = "route_missing"
    MODEL_MISSING = "model_missing"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Sanitized result of a provider capability check."""

    state: ProviderState
    message: str

    @property
    def available(self) -> bool:
        return self.state is ProviderState.READY


class ProviderCircuit:
    """Mutable run-level circuit shared by content-analysis consumers."""

    def __init__(self) -> None:
        self.health = ProviderHealth(ProviderState.READY, "ready")

    @property
    def available(self) -> bool:
        return self.health.available

    def set_health(self, health: ProviderHealth) -> bool:
        """Store health and return True only when the circuit changed state."""
        changed = health != self.health
        self.health = health
        return changed

    def disable(
        self,
        message: str,
        state: ProviderState = ProviderState.DISABLED,
    ) -> bool:
        return self.set_health(ProviderHealth(state, message))


def classify_openai_response(status_code: int, body: Any, model: str) -> ProviderHealth:
    """Classify an OpenAI-compatible response without retaining its body."""
    if 200 <= status_code < 300:
        return ProviderHealth(ProviderState.READY, f"configured model ready: {model}")
    if status_code in {401, 403}:
        return ProviderHealth(ProviderState.AUTH_FAILED, "provider authentication rejected")

    body_text = str(body).lower()
    model_failure = "model" in body_text and any(
        marker in body_text
        for marker in ("not found", "does not exist", "unknown", "unavailable", "not loaded")
    )
    if status_code == 404 and model_failure:
        return ProviderHealth(ProviderState.MODEL_MISSING, f"configured model unavailable: {model}")
    if status_code == 404:
        return ProviderHealth(ProviderState.ROUTE_MISSING, "chat-completions route unavailable")
    if 400 <= status_code < 500:
        return ProviderHealth(
            ProviderState.DISABLED,
            f"provider rejected capability check (HTTP {status_code})",
        )
    return ProviderHealth(
        ProviderState.UNREACHABLE,
        f"provider unavailable (HTTP {status_code})",
    )
