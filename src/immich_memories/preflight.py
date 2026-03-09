"""Preflight checks for validating provider connections."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import httpx

from immich_memories.config import Config, get_config

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


@dataclass
class PreflightResults:
    """Results of all preflight checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        """Check if all checks passed (no errors)."""
        return all(c.status != CheckStatus.ERROR for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return any(c.status == CheckStatus.WARNING for c in self.checks)

    @property
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return any(c.status == CheckStatus.ERROR for c in self.checks)

    def add(self, result: CheckResult) -> None:
        """Add a check result."""
        self.checks.append(result)


def check_immich(config: Config | None = None) -> CheckResult:
    """Check Immich server connection and API key validity.

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        CheckResult with status and details.
    """
    if config is None:
        config = get_config()

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

    try:
        from immich_memories.api.immich import SyncImmichClient

        with SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
        ) as client:
            user = client.get_current_user()
            return CheckResult(
                name="Immich",
                status=CheckStatus.OK,
                message=f"Connected as {user.name or user.email}",
                details=f"Server: {config.immich.url}",
            )
    except Exception as e:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details=str(e),
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
                details=f"Server: {base_url}, Model: {model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details=f"Server not reachable at {base_url}",
        )
    except Exception as e:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=str(e),
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

            if response.status_code == 401:
                return CheckResult(
                    name="LLM",
                    status=CheckStatus.ERROR,
                    message="Authentication failed",
                    details=f"API key rejected by {base_url}",
                )

            response.raise_for_status()
            return CheckResult(
                name="LLM",
                status=CheckStatus.OK,
                message="Connected (openai-compatible)",
                details=f"Server: {base_url}, Model: {model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details=f"Server not reachable at {base_url}",
        )
    except Exception as e:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=str(e),
        )


def check_llm(config: Config | None = None) -> CheckResult:
    """Check LLM provider availability.

    Dispatches to the appropriate check based on config.llm.provider:
    - "ollama": GET /api/tags
    - "openai-compatible": POST /chat/completions with minimal payload

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        CheckResult with status and details.
    """
    if config is None:
        config = get_config()

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

    except Exception as e:
        return CheckResult(
            name="Hardware",
            status=CheckStatus.WARNING,
            message="Detection failed",
            details=str(e),
        )


def run_preflight_checks(config: Config | None = None) -> PreflightResults:
    """Run all preflight checks.

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        PreflightResults containing all check results.
    """
    if config is None:
        config = get_config()

    results = PreflightResults()

    # Required checks
    results.add(check_immich(config))

    # LLM provider check
    results.add(check_llm(config))
    # Hardware check
    results.add(check_hardware())

    return results
