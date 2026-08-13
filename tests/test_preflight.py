"""Tests for provider preflight diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.api.immich import ImmichAPIError
from immich_memories.config_loader import Config
from immich_memories.preflight import CheckResult, CheckStatus, check_immich, check_llm


def test_immich_api_error_returns_sanitized_diagnostic_result() -> None:
    api_key = "preflight-api-secret"
    config = Config(immich={"url": "https://immich.example.com", "api_key": api_key})
    error = ImmichAPIError(
        f"upstream rejected {api_key}",
        status_code=400,
        correlation_id="corr-123",
        details={"requestHeaders": {"x-api-key": api_key}},
    )
    # WHY: replace the external Immich connection while exercising real preflight mapping.
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_current_user.side_effect = error

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result == CheckResult(
        name="Immich",
        status=CheckStatus.ERROR,
        message="Connection failed",
        details="upstream rejected ***; HTTP 400; Correlation ID: corr-123",
    )
    assert api_key not in result.message
    assert api_key not in (result.details or "")
    assert "requestHeaders" not in (result.details or "")


def test_immich_connection_error_does_not_expose_configured_api_key() -> None:
    api_key = "connection-api-secret"
    config = Config(immich={"url": "https://immich.example.com", "api_key": api_key})
    # WHY: replace the external Immich connection with a credential-bearing transport error.
    client = MagicMock()
    client.__enter__.side_effect = OSError(f"connection rejected {api_key}")

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result.status is CheckStatus.ERROR
    assert result.message == "Connection failed"
    assert result.details == "connection rejected ***"
    assert api_key not in (result.details or "")


def test_llm_preflight_reports_missing_configured_model() -> None:
    """A model-specific 404 is not mislabeled as a generic connection error."""
    config = Config(
        llm={
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model": "removed-vlm",
        },
        content_analysis={"enabled": True},
    )
    response = MagicMock()
    response.status_code = 404
    response.json.return_value = {"error": {"message": "model removed-vlm not found"}}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Configured model unavailable: removed-vlm"
    assert "model removed-vlm not found" not in (result.details or "")
    assert "localhost:9999" not in (result.details or "")


def test_llm_preflight_reports_missing_chat_route() -> None:
    """A route-level 404 remains distinct from a removed model."""
    config = Config(
        llm={
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model": "vlm",
        },
        content_analysis={"enabled": True},
    )
    response = MagicMock()
    response.status_code = 404
    response.json.return_value = {}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Chat-completions route unavailable"
    assert "localhost:9999" not in (result.details or "")
