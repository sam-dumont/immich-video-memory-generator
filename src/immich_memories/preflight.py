"""Preflight checks for validating provider connections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import httpx

from immich_memories.analysis.provider_health import (
    ProviderHealth,
    ProviderState,
    classify_openai_response,
)
from immich_memories.api.compatibility import UnsupportedImmichVersion
from immich_memories.config import Config
from immich_memories.security import sanitize_error_message

logger = logging.getLogger(__name__)


class CheckStatus(Enum):
    """Status of a preflight check."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: CheckStatus
    message: str
    details: str | None = None


def check_immich(config: Config) -> CheckResult:
    """Check Immich server connection and API key validity.

    Args:
        config: Configuration to use.

    Returns:
        CheckResult with status and details.
    """
    if not config.immich.url:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="URL not configured",
            details="Set immich.url in config or IMMICH_MEMORIES_IMMICH__URL env var",
        )

    if not config.immich.api_key:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="API key not configured",
            details="Set immich.api_key in config or IMMICH_MEMORIES_IMMICH__API_KEY env var",
        )

    from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

    try:
        with SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
        ) as client:
            resolved_version = client.get_api_version()
            user = client.get_current_user()
            return CheckResult(
                name="Immich",
                status=CheckStatus.OK,
                message=f"Connected as {user.name or user.email}",
                details=f"Server: {config.immich.url}; API: {resolved_version.value}",
            )
    except UnsupportedImmichVersion as e:
        safe_message = sanitize_error_message(str(e)).replace(config.immich.api_key, "***")
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Unsupported Immich version",
            details=safe_message,
        )
    except ImmichAPIError as e:
        safe_message = sanitize_error_message(str(e)).replace(config.immich.api_key, "***")
        diagnostics = [safe_message]
        if e.status_code is not None:
            diagnostics.append(f"HTTP {e.status_code}")
        if e.correlation_id:
            safe_correlation = sanitize_error_message(e.correlation_id).replace(
                config.immich.api_key, "***"
            )
            diagnostics.append(f"Correlation ID: {safe_correlation}")
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details="; ".join(diagnostics),
        )
    except (httpx.TimeoutException, httpx.HTTPStatusError, OSError) as e:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details=sanitize_error_message(str(e)).replace(config.immich.api_key, "***"),
        )


def _check_ollama(base_url: str, model: str) -> CheckResult:
    """Check Ollama server availability via /api/tags.

    Args:
        base_url: Ollama server URL.
        model: Configured model name.

    Returns:
        CheckResult with status and details.
    """
    try:
        normalized = base_url.rstrip("/")

        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{normalized}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = data.get("models", [])
            model_names = [m.get("name", "") for m in models]

            if model and model not in model_names:
                base_name = model.split(":")[0]
                if not any(m.startswith(base_name) for m in model_names):
                    return CheckResult(
                        name="LLM",
                        status=CheckStatus.WARNING,
                        message=f"Connected but missing model: {model}",
                        details=f"Available: {', '.join(model_names[:5])}{'...' if len(model_names) > 5 else ''}",
                    )

            return CheckResult(
                name="LLM",
                status=CheckStatus.OK,
                message=f"Connected (ollama, {len(models)} models)",
                details=f"Model: {model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details="Check the configured LLM base URL and that Ollama is running",
        )
    except (httpx.TimeoutException, httpx.HTTPStatusError, OSError) as e:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=type(e).__name__,
        )


def _openai_health_failure(health: ProviderHealth, model: str) -> CheckResult | None:
    """Translate provider health into a safe, actionable preflight failure."""
    if health.state is ProviderState.AUTH_FAILED:
        return CheckResult(
            name="LLM",
            status=CheckStatus.ERROR,
            message="Authentication failed",
            details="The configured API key was rejected",
        )
    if health.state is ProviderState.MODEL_MISSING:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message=f"Configured model unavailable: {model}",
            details=f"Model: {model}",
        )
    if health.state is ProviderState.ROUTE_MISSING:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Chat-completions route unavailable",
            details="Check that the configured base URL exposes /chat/completions",
        )
    if health.available:
        return None
    return CheckResult(
        name="LLM",
        status=CheckStatus.WARNING,
        message=health.message,
        details="Check the configured LLM provider",
    )


def _check_openai_compatible(base_url: str, model: str, api_key: str) -> CheckResult:
    """Check OpenAI-compatible server via test completion.

    Args:
        base_url: API base URL (e.g. http://localhost:8080/v1).
        model: Model name.
        api_key: API key (may be empty for local servers).

    Returns:
        CheckResult with status and details.
    """
    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=10.0, headers=headers) as client:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
            )

            try:
                response_body = response.json()
            except ValueError:
                response_body = {}
            health = classify_openai_response(response.status_code, response_body, model)
            if failure := _openai_health_failure(health, model):
                return failure

            return CheckResult(
                name="LLM",
                status=CheckStatus.OK,
                message="Connected (openai-compatible)",
                details=f"Model: {model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details="Check the configured LLM base URL and provider availability",
        )
    except (httpx.TimeoutException, httpx.HTTPStatusError, OSError) as e:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=type(e).__name__,
        )


def check_llm(config: Config) -> CheckResult:
    """Check LLM provider availability.

    Dispatches to the appropriate check based on config.llm.provider:
    - "ollama": GET /api/tags
    - "openai-compatible": POST /chat/completions with minimal payload

    Args:
        config: Configuration to use.

    Returns:
        CheckResult with status and details.
    """
    provider = config.llm.provider
    base_url = config.llm.base_url
    model = config.llm.model

    if not base_url:
        return CheckResult(
            name="LLM",
            status=CheckStatus.SKIPPED,
            message="Not configured",
            details="No base_url set",
        )

    if provider == "ollama":
        return _check_ollama(base_url, model)
    if provider == "openai-compatible":
        return _check_openai_compatible(base_url, model, config.llm.api_key)

    return CheckResult(
        name="LLM",
        status=CheckStatus.ERROR,
        message=f"Unknown provider: {provider}",
    )


def check_hardware() -> CheckResult:
    """Check hardware acceleration availability.

    Returns:
        CheckResult with status and details.
    """
    try:
        from immich_memories.processing.hardware import (
            HWAccelBackend,
            detect_hardware_acceleration,
        )

        caps = detect_hardware_acceleration()

        if caps.backend == HWAccelBackend.NONE:
            return CheckResult(
                name="Hardware",
                status=CheckStatus.WARNING,
                message="No GPU acceleration",
                details="Video encoding will use CPU (slower)",
            )

        features = []
        if caps.supports_h264_encode:
            features.append("H.264 encode")
        if caps.supports_h265_encode:
            features.append("H.265 encode")
        if caps.opencv_cuda:
            features.append("OpenCV CUDA")

        return CheckResult(
            name="Hardware",
            status=CheckStatus.OK,
            message=f"{caps.backend.value.upper()} ({caps.device_name or 'Unknown'})",
            details=", ".join(features) if features else "Basic acceleration",
        )

    except (ImportError, RuntimeError, OSError) as e:
        return CheckResult(
            name="Hardware",
            status=CheckStatus.WARNING,
            message="Detection failed",
            details=str(e),
        )


def run_preflight_checks(config: Config) -> list[CheckResult]:
    """Run all preflight checks.

    Args:
        config: Configuration to use.

    Returns:
        List of check results.
    """
    return [
        check_immich(config),
        check_llm(config),
        check_hardware(),
    ]
