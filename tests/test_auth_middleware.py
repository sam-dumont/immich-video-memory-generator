"""Tests for auth helpers — bypass paths, session management, is_auth_enabled.

The actual middleware runs via @app.middleware('http') in NiceGUI and uses
app.storage.user, which cannot be tested with Starlette TestClient alone.
Full middleware flow is tested via integration tests (oidc-provider-mock)
and E2E tests (Playwright, Phase 10).
"""

from __future__ import annotations

from datetime import datetime

import pytest

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
