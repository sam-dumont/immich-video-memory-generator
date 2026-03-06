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


def check_ollama(config: Config | None = None) -> CheckResult:
    """Check Ollama server availability.

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        CheckResult with status and details.
    """
    if config is None:
        config = get_config()

    ollama_url = config.llm.ollama_url

    if not ollama_url:
        return CheckResult(
            name="Ollama",
            status=CheckStatus.SKIPPED,
            message="Not configured",
            details="Ollama URL not set in config",
        )

    try:
        # Normalize URL
        base_url = ollama_url.rstrip("/")

        # Check if Ollama is reachable
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = data.get("models", [])
            model_names = [m.get("name", "") for m in models]

            # Check if configured model is available
            configured_model = config.llm.ollama_model

            if configured_model and configured_model not in model_names:
                # Check with and without tag
                base_name = configured_model.split(":")[0]
                if not any(m.startswith(base_name) for m in model_names):
                    return CheckResult(
                        name="Ollama",
                        status=CheckStatus.WARNING,
                        message=f"Connected but missing model: {configured_model}",
                        details=f"Available: {', '.join(model_names[:5])}{'...' if len(model_names) > 5 else ''}",
                    )

            return CheckResult(
                name="Ollama",
                status=CheckStatus.OK,
                message=f"Connected ({len(models)} models available)",
                details=f"Server: {ollama_url}, Model: {configured_model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="Ollama",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details=f"Server not reachable at {ollama_url}",
        )
    except Exception as e:
        return CheckResult(
            name="Ollama",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=str(e),
        )


def check_openai(config: Config | None = None) -> CheckResult:
    """Check OpenAI API key validity.

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        CheckResult with status and details.
    """
    if config is None:
        config = get_config()

    api_key = config.llm.openai_api_key

    if not api_key:
        return CheckResult(
            name="OpenAI",
            status=CheckStatus.SKIPPED,
            message="Not configured",
            details="API key not set (optional, Ollama preferred)",
        )

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code == 401:
                return CheckResult(
                    name="OpenAI",
                    status=CheckStatus.ERROR,
                    message="Invalid API key",
                    details="The provided API key is not valid",
                )

            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])

            # Check if configured model is available
            configured_model = config.llm.openai_model
            model_ids = [m.get("id", "") for m in models]

            if configured_model not in model_ids:
                return CheckResult(
                    name="OpenAI",
                    status=CheckStatus.WARNING,
                    message="Connected but configured model not found",
                    details=f"Looking for: {configured_model}",
                )

            return CheckResult(
                name="OpenAI",
                status=CheckStatus.OK,
                message="API key valid",
                details=f"Model: {configured_model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="OpenAI",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details="OpenAI API not reachable",
        )
    except Exception as e:
        return CheckResult(
            name="OpenAI",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=str(e),
        )


def check_pixabay(config: Config | None = None) -> CheckResult:
    """Check Pixabay API key validity.

    Args:
        config: Configuration to use (defaults to global config).

    Returns:
        CheckResult with status and details.
    """
    if config is None:
        config = get_config()

    api_key = config.audio.pixabay_api_key

    if not api_key:
        return CheckResult(
            name="Pixabay",
            status=CheckStatus.SKIPPED,
            message="Not configured",
            details="API key not set (needed for auto music selection)",
        )

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://pixabay.com/api/",
                params={"key": api_key, "q": "test"},
            )

            if response.status_code == 400:
                data = response.json()
                if "API key" in str(data):
                    return CheckResult(
                        name="Pixabay",
                        status=CheckStatus.ERROR,
                        message="Invalid API key",
                        details="The provided API key is not valid",
                    )

            response.raise_for_status()
            data = response.json()

            if "totalHits" in data:
                return CheckResult(
                    name="Pixabay",
                    status=CheckStatus.OK,
                    message="API key valid",
                    details="Music search available",
                )

            return CheckResult(
                name="Pixabay",
                status=CheckStatus.WARNING,
                message="Unexpected response",
                details=str(data)[:100],
            )

    except httpx.ConnectError:
        return CheckResult(
            name="Pixabay",
            status=CheckStatus.WARNING,
            message="Cannot connect",
            details="Pixabay API not reachable",
        )
    except Exception as e:
        return CheckResult(
            name="Pixabay",
            status=CheckStatus.WARNING,
            message="Connection error",
            details=str(e),
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

    # Optional provider checks
    results.add(check_ollama(config))
    results.add(check_openai(config))
    results.add(check_pixabay(config))

    # Hardware check
    results.add(check_hardware())

    return results
