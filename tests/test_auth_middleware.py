"""Tests for auth middleware — bypass paths, session TTL, header stripping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth import (
    clear_session,
    create_auth_middleware,
    is_auth_enabled,
    set_session,
)


def _echo_handler(request: Request) -> JSONResponse:
    """Echo back session and headers for test inspection."""
    return JSONResponse(
        {
            "session": dict(request.session),
            "headers": dict(request.headers),
            "path": request.url.path,
        }
    )


def _make_app(auth_config: AuthConfig) -> Starlette:
    """Create a minimal Starlette app with session + auth middleware for testing."""
    routes = [
        Route("/", _echo_handler),
        Route("/health", _echo_handler),
        Route("/login", _echo_handler),
        Route("/logout", _echo_handler),
        Route("/auth/callback", _echo_handler),
        Route("/auth/authorize", _echo_handler),
        Route("/_nicegui/auto/test", _echo_handler),
        Route("/protected", _echo_handler),
    ]
    app = Starlette(routes=routes)
    # Order matters: SessionMiddleware must wrap AuthMiddleware
    if auth_config.enabled:
        app.add_middleware(create_auth_middleware(auth_config))
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # noqa: S106
    return app


def _basic_auth_config(**overrides) -> AuthConfig:
    defaults = {
        "enabled": True,
        "provider": "basic",
        "username": "admin",
        "password": "secret",  # noqa: S106
        "session_ttl_hours": 24,
    }
    defaults.update(overrides)
    return AuthConfig(**defaults)


class TestMiddlewareDisabled:
    """When auth is disabled, all requests pass through."""

    def test_requests_pass_through(self):
        cfg = AuthConfig(enabled=False)
        app = _make_app(cfg)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_protected_passes_through(self):
        cfg = AuthConfig(enabled=False)
        app = _make_app(cfg)
        client = TestClient(app)
        resp = client.get("/protected")
        assert resp.status_code == 200


class TestMiddlewareBypassPaths:
    """Bypass paths are accessible without authentication."""

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/login",
            "/auth/callback",
            "/auth/authorize",
            "/_nicegui/auto/test",
        ],
    )
    def test_bypass_paths_allowed(self, path: str):
        cfg = _basic_auth_config()
        app = _make_app(cfg)
        client = TestClient(app, follow_redirects=False)
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} should bypass auth"


class TestMiddlewareRedirect:
    """Unauthenticated requests are redirected to /login."""

    def test_unauthenticated_gets_307(self):
        cfg = _basic_auth_config()
        app = _make_app(cfg)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_unauthenticated_protected_gets_307(self):
        cfg = _basic_auth_config()
        app = _make_app(cfg)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/protected")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]


class TestMiddlewareSessionTTL:
    """Expired sessions are cleared and redirected."""

    def test_expired_session_redirects(self):
        cfg = _basic_auth_config(session_ttl_hours=1)

        # Use /health (bypass path) to set session, avoiding auth redirect
        def _set_expired_session(request: Request) -> PlainTextResponse:
            expired_time = datetime.now(UTC) - timedelta(hours=2)
            set_session(
                request.session,
                username="admin",
                provider="basic",
            )
            request.session["authenticated_at"] = expired_time.isoformat()
            return PlainTextResponse("session set")

        app = _make_app(cfg)
        # Replace the /health route with our session-setting handler
        app.routes[1] = Route("/health", _set_expired_session)
        client = TestClient(app, follow_redirects=False)

        # Set the expired session via bypass path
        client.get("/health")

        # Now try to access a protected path — should be redirected
        resp = client.get("/")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_valid_session_passes(self):
        cfg = _basic_auth_config(session_ttl_hours=24)

        def _set_valid_session(request: Request) -> PlainTextResponse:
            set_session(
                request.session,
                username="admin",
                provider="basic",
            )
            return PlainTextResponse("session set")

        app = _make_app(cfg)
        app.routes[1] = Route("/health", _set_valid_session)
        client = TestClient(app, follow_redirects=False)

        # Set valid session via bypass path
        client.get("/health")
        resp = client.get("/")
        assert resp.status_code == 200


class TestMiddlewareHeaderStripping:
    """Forged auth headers from untrusted IPs are stripped."""

    def test_untrusted_ip_headers_stripped_and_redirected(self):
        cfg = AuthConfig(
            enabled=True,
            provider="header",
            trusted_proxies=["192.168.1.0/24"],
            user_header="Remote-User",
            email_header="Remote-Email",
        )
        app = _make_app(cfg)
        client = TestClient(app, follow_redirects=False)

        # testclient comes from 127.0.0.1 — not in trusted_proxies
        resp = client.get(
            "/protected",
            headers={"Remote-User": "forged-user", "Remote-Email": "forged@evil.com"},
        )
        # Should be redirected (not authenticated)
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]


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
        assert "email" not in session
        assert "auth_provider" not in session
        assert "authenticated_at" not in session

    def test_clear_session_ignores_missing_keys(self):
        session: dict = {"other_key": "value"}
        clear_session(session)
        assert session == {"other_key": "value"}


class TestIsAuthEnabled:
    """is_auth_enabled helper returns correct boolean."""

    def test_enabled(self):
        cfg = _basic_auth_config()
        assert is_auth_enabled(cfg) is True

    def test_disabled(self):
        cfg = AuthConfig(enabled=False)
        assert is_auth_enabled(cfg) is False
