"""Tests for auth helpers — bypass paths, session management, is_auth_enabled.

The actual middleware runs via @app.middleware('http') in NiceGUI and uses
app.storage.user, which cannot be tested with Starlette TestClient alone.
Full middleware flow is tested via integration tests (oidc-provider-mock)
and E2E tests (Playwright, Phase 10).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth import (
    clear_session,
    is_auth_enabled,
    is_bypass_path,
    set_session,
)


class TestBypassPaths:
    """is_bypass_path identifies public paths correctly."""

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/health/live",
            "/health/ready",
            "/login",
            "/logout",
            "/auth/callback",
            "/auth/authorize",
            "/_nicegui/auto/test",
            "/_nicegui/static/foo.js",
        ],
    )
    def test_bypass_paths_return_true(self, path: str):
        assert is_bypass_path(path) is True

    @pytest.mark.parametrize(
        "path",
        ["/", "/step2", "/protected", "/settings/config", "/api/something"],
    )
    def test_protected_paths_return_false(self, path: str):
        assert is_bypass_path(path) is False


class TestProductionMiddleware:
    """Production ordering keeps operational probes independent from config."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready"])
    async def test_health_bypasses_before_configuration_load(self, path: str):
        from immich_memories.ui.app import _auth_middleware

        request = MagicMock()
        request.url.path = path
        response = MagicMock(name="health_response")
        call_next = AsyncMock(return_value=response)
        get_config = MagicMock(side_effect=AssertionError("health loaded configuration"))

        with patch("immich_memories.ui.app.get_config", get_config):
            actual = await _auth_middleware(request, call_next)

        assert actual is response
        get_config.assert_not_called()
        call_next.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_health_prefix_is_not_a_configuration_bypass(self):
        from immich_memories.ui.app import _auth_middleware

        request = MagicMock()
        request.url.path = "/health/live/extra"
        response = MagicMock(name="protected_response")
        call_next = AsyncMock(return_value=response)
        get_config = MagicMock(return_value=Config())

        with patch("immich_memories.ui.app.get_config", get_config):
            actual = await _auth_middleware(request, call_next)

        assert actual is response
        get_config.assert_called_once_with()
        call_next.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_login_still_loads_config_and_applies_rate_limiting(self):
        from immich_memories.ui.app import _auth_middleware

        request = MagicMock()
        request.url.path = "/login"
        blocked = MagicMock(name="rate_limited_response")
        call_next = AsyncMock()
        get_config = MagicMock(
            return_value=Config(
                auth={
                    "enabled": True,
                    "provider": "basic",
                    "username": "operator",
                    "password": "test-password",
                }
            )
        )

        with (
            patch("immich_memories.ui.app.get_config", get_config),
            patch("immich_memories.ui.app._check_login_rate_limit", return_value=blocked),
        ):
            actual = await _auth_middleware(request, call_next)

        assert actual is blocked
        get_config.assert_called_once_with()
        call_next.assert_not_awaited()


class TestSessionHelpers:
    """set_session and clear_session manage session dict correctly."""

    def test_set_session_fields(self):
        session: dict = {}
        set_session(session, username="admin", provider="basic", email="a@b.com")
        assert session["authenticated"] is True
        assert session["username"] == "admin"
        assert session["auth_provider"] == "basic"
        assert session["email"] == "a@b.com"
        assert "authenticated_at" in session

    def test_set_session_without_email(self):
        session: dict = {}
        set_session(session, username="admin", provider="oidc")
        assert session["authenticated"] is True
        assert session["email"] == ""

    def test_set_session_timestamp_is_utc_iso(self):
        session: dict = {}
        set_session(session, username="admin", provider="basic")
        ts = datetime.fromisoformat(session["authenticated_at"])
        assert ts.tzinfo is not None  # UTC-aware

    def test_clear_session_removes_fields(self):
        session: dict = {
            "authenticated": True,
            "username": "admin",
            "email": "a@b.com",
            "auth_provider": "basic",
            "authenticated_at": "2026-01-01T00:00:00+00:00",
        }
        clear_session(session)
        assert "authenticated" not in session
        assert "username" not in session

    def test_clear_session_preserves_other_keys(self):
        session: dict = {"other_key": "value", "authenticated": True}
        clear_session(session)
        assert session == {"other_key": "value"}


class TestIsAuthEnabled:
    def test_enabled(self):
        cfg = AuthConfig(enabled=True, provider="basic", username="a", password="b")  # noqa: S106
        assert is_auth_enabled(cfg) is True

    def test_disabled(self):
        cfg = AuthConfig(enabled=False)
        assert is_auth_enabled(cfg) is False
