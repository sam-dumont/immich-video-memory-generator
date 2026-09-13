"""Tests for provider preflight diagnostics."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from unittest.mock import MagicMock, patch

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.analysis.editorial_description_contract import API_MODEL
from immich_memories.api.immich import ImmichAPIError
from immich_memories.config_loader import Config
from immich_memories.preflight import (
    CheckResult,
    CheckStatus,
    check_caption_endpoint,
    check_detector_export,
    check_encoder,
    check_immich,
    check_llm,
    check_notifications,
    check_title_rendering,
    run_preflight_checks,
)


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


def test_notification_preflight_warns_on_sanitized_failure_cooldown(tmp_path) -> None:
    from immich_memories.automation.notification_state import (
        NotificationFailureCategory,
        NotificationStateStore,
    )

    credential_url = "https://notify.test/provider-secret"
    config = Config(
        cache={"database": str(tmp_path / "preflight.db")},
        notifications={"enabled": True, "urls": [credential_url], "cooldown_hours": 24},
    )
    NotificationStateStore(config.cache.database_path).record_failure(
        NotificationFailureCategory.QUOTA
    )

    result = check_notifications(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Delivery paused after quota failure"
    assert "24h" in (result.details or "")
    assert credential_url not in f"{result.message} {result.details}"


def test_notification_preflight_is_optional_when_disabled() -> None:
    result = check_notifications(Config())

    assert result.status is CheckStatus.SKIPPED
    assert result.message == "Notifications disabled"


def test_title_rendering_preflight_reports_the_pil_fallback_without_a_kernel_library() -> None:
    # WHY: replaces the installed-package probe with what a no-kernel install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        result = check_title_rendering(Config())

    assert result.status is CheckStatus.WARNING
    assert "PIL fallback" in result.message
    assert "immich-memories" in (result.details or "")


def test_title_rendering_preflight_names_the_library_it_would_load() -> None:
    """One of the two being present is enough, and the message says which one."""
    result = check_title_rendering(Config())

    assert result.status is CheckStatus.OK
    assert "quadrants" in result.message


def test_preflight_run_lists_every_absent_optional_feature() -> None:
    """The degraded-install summary is the whole point: one line per lost feature."""
    # WHY: replaces the installed-package probe with what a bare pip install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        checks = run_preflight_checks(Config())

    degraded = {c.name for c in checks if c.status is CheckStatus.WARNING}
    assert {"Title rendering"} <= degraded


class _CaptionEndpoint:
    """A local stand-in for the caption server's `/models` inventory."""

    def __init__(self, model_ids: list[str]) -> None:
        body = json.dumps({"data": [{"id": name} for name in model_ids]}).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def test_encoder_check_names_the_fetch_command_when_the_export_is_absent(tmp_path) -> None:
    config = Config(triage={"encoder": str(tmp_path / "dinov2-small.onnx")})

    result = check_encoder(config)

    assert result.status is CheckStatus.ERROR
    assert "models fetch" in (result.details or "")


def test_encoder_check_rejects_an_export_that_is_not_the_pinned_one(tmp_path) -> None:
    path = tmp_path / "dinov2-small.onnx"
    path.write_bytes(b"some other onnx export")
    config = Config(triage={"encoder": str(path)})

    result = check_encoder(config)

    assert result.status is CheckStatus.ERROR
    assert "pinned" in result.message.lower()


def test_detector_check_names_the_fetch_command_when_the_export_is_absent(tmp_path) -> None:
    config = Config(editorial={"preparation": {"marqo_onnx": str(tmp_path / "marqo.onnx")}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.ERROR
    assert "models fetch" in (result.details or "")


def test_detector_check_rejects_an_export_that_is_not_the_pinned_one(tmp_path) -> None:
    path = tmp_path / "marqo.onnx"
    path.write_bytes(b"some other onnx export")
    config = Config(editorial={"preparation": {"marqo_onnx": str(path)}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.ERROR
    assert "pinned" in result.message.lower()


def test_detector_check_accepts_the_pinned_export(tmp_path, monkeypatch) -> None:
    path = tmp_path / "marqo.onnx"
    path.write_bytes(b"the pinned onnx export")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # WHY: the real 22.5 MB export cannot live in the repo, so this file's own
    # digest stands in for the pin; the check itself is the production one.
    monkeypatch.setattr(detectors, "MARQO_ONNX_SHA256", digest)
    config = Config(editorial={"preparation": {"marqo_onnx": str(path)}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.OK
    assert result.details == str(path)


def test_caption_check_passes_when_the_endpoint_advertises_the_alias() -> None:
    endpoint = _CaptionEndpoint([API_MODEL])
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.OK
    assert API_MODEL in result.message


def test_caption_check_fails_when_the_endpoint_serves_another_model() -> None:
    endpoint = _CaptionEndpoint(["some-other-vlm"])
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.ERROR
    assert API_MODEL in (result.details or result.message)
