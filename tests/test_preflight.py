"""Tests for provider preflight diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.api.immich import ImmichAPIError
from immich_memories.config_loader import Config
from immich_memories.preflight import (
    CheckResult,
    CheckStatus,
    check_audio_content,
    check_immich,
    check_llm,
    check_notifications,
    check_speech_boundaries,
    check_title_rendering,
    check_transcription,
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


def test_audio_preflight_warns_when_panns_requested_but_extra_absent() -> None:
    config = Config(audio_content={"enabled": True, "use_panns": True})

    with patch("importlib.util.find_spec", return_value=None):
        result = check_audio_content(config)

    assert result.status is CheckStatus.WARNING
    assert "energy-only fallback" in result.message
    assert "audio-ml" in (result.details or "")


def test_audio_preflight_reports_semantic_panns_ready() -> None:
    config = Config(audio_content={"enabled": True, "use_panns": True})

    with patch("importlib.util.find_spec", return_value=MagicMock()):
        result = check_audio_content(config)

    assert result.status is CheckStatus.OK
    assert result.message == "Semantic PANNs audio classification ready"


def test_audio_preflight_skips_when_disabled() -> None:
    config = Config(audio_content={"enabled": False})

    result = check_audio_content(config)

    assert result.status is CheckStatus.SKIPPED
    assert result.message == "Audio-content analysis disabled"


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


def test_speech_boundaries_preflight_names_the_feature_lost_without_the_extra() -> None:
    """A bare install must see the cost, not just a missing package name."""
    # WHY: replaces the installed-package probe with what a bare pip install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        result = check_speech_boundaries(Config())

    assert result.status is CheckStatus.WARNING
    assert result.message == "Speech boundaries unavailable; cuts may land mid-sentence"
    assert "onnxruntime" in (result.details or "")
    assert "immich-memories[speech]" in (result.details or "")


def test_transcription_preflight_names_the_feature_lost_without_the_extra() -> None:
    config = Config(transcription={"enabled": True, "languages": ["en"]})

    # WHY: replaces the installed-package probe with what a no-transcribe install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        result = check_transcription(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == (
        "Speech transcription unavailable; clips are chosen without what was said"
    )
    assert "immich-memories[transcribe]" in (result.details or "")


def test_title_rendering_preflight_reports_the_pil_fallback_without_taichi() -> None:
    # WHY: replaces the installed-package probe with what a no-gpu install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        result = check_title_rendering(Config())

    assert result.status is CheckStatus.WARNING
    assert "PIL fallback" in result.message
    assert "immich-memories[gpu]" in (result.details or "")


def test_preflight_run_lists_every_absent_optional_feature() -> None:
    """The degraded-install summary is the whole point: one line per lost feature."""
    config = Config(
        audio_content={"enabled": True},
        transcription={"enabled": True, "languages": ["en"]},
    )

    # WHY: replaces the installed-package probe with what a bare pip install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        checks = run_preflight_checks(config)

    degraded = {c.name for c in checks if c.status is CheckStatus.WARNING}
    assert {"Audio content", "Speech boundaries", "Transcription", "Title rendering"} <= degraded
