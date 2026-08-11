"""Tests for provider preflight diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.api.immich import ImmichAPIError
from immich_memories.config_loader import Config
from immich_memories.preflight import CheckResult, CheckStatus, check_immich


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
