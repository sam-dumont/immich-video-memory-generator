"""Tests for OIDC client wrapper."""

from __future__ import annotations

import sys

import pytest

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth_oidc import (
    _import_authlib,
    create_oidc_client,
    extract_user_from_token,
    get_end_session_url,
    reset_oidc_client,
)


def _make_oidc_config(**overrides) -> AuthConfig:
    defaults = {
        "enabled": True,
        "provider": "oidc",
        "issuer_url": "https://auth.example.com",
        "client_id": "myapp",
        "client_secret": "secret",
        "scope": "openid email profile",
    }
    defaults.update(overrides)
    return AuthConfig(**defaults)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the OIDC singleton before each test."""
    reset_oidc_client()
    yield
    reset_oidc_client()


class TestCreateOIDCClient:
    def test_creates_client(self):
        config = _make_oidc_config()
        oauth = create_oidc_client(config)
        assert oauth is not None

    def test_client_has_oidc_attribute(self):
        config = _make_oidc_config()
        oauth = create_oidc_client(config)
        assert hasattr(oauth, "oidc")

    def test_pkce_enabled(self):
        config = _make_oidc_config()
        oauth = create_oidc_client(config)
        client = oauth.oidc
        # authlib stores code_challenge_method in server_metadata
        assert client.server_metadata.get("code_challenge_method") == "S256"


class TestExtractUserFromToken:
    def test_preferred_username_present(self):
        token = {
            "userinfo": {
                "preferred_username": "alice",
                "name": "Alice Smith",
                "sub": "abc123",
                "email": "alice@example.com",
            }
        }
        username, email = extract_user_from_token(token)
        assert username == "alice"
        assert email == "alice@example.com"

    def test_only_name_present(self):
        token = {
            "userinfo": {
                "name": "Bob Jones",
                "sub": "def456",
                "email": "bob@example.com",
            }
        }
        username, email = extract_user_from_token(token)
        assert username == "Bob Jones"

    def test_only_sub_present(self):
        token = {
            "userinfo": {
                "sub": "ghi789",
            }
        }
        username, _ = extract_user_from_token(token)
        assert username == "ghi789"

    def test_missing_userinfo_key(self):
        token = {"access_token": "xyz"}
        username, email = extract_user_from_token(token)
        assert username == "unknown"
        assert email == ""

    def test_email_absent(self):
        token = {
            "userinfo": {
                "preferred_username": "carol",
            }
        }
        _, email = extract_user_from_token(token)
        assert email == ""

    def test_empty_userinfo(self):
        token = {"userinfo": {}}
        username, email = extract_user_from_token(token)
        assert username == "unknown"
        assert email == ""


class TestImportGuard:
    def test_raises_when_authlib_missing(self, monkeypatch):
        # Temporarily poison authlib in sys.modules
        original = sys.modules.get("authlib.integrations.starlette_client")
        monkeypatch.setitem(sys.modules, "authlib.integrations.starlette_client", None)
        with pytest.raises(ImportError, match="authlib is required"):
            _import_authlib()
        # Restore
        if original is not None:
            monkeypatch.setitem(sys.modules, "authlib.integrations.starlette_client", original)
        else:
            monkeypatch.delitem(sys.modules, "authlib.integrations.starlette_client", raising=False)


class TestSingleton:
    def test_same_instance_returned(self):
        config = _make_oidc_config()
        first = create_oidc_client(config)
        second = create_oidc_client(config)
        assert first is second

    def test_reset_clears_singleton(self):
        config = _make_oidc_config()
        first = create_oidc_client(config)
        reset_oidc_client()
        second = create_oidc_client(config)
        assert first is not second


class TestGetEndSessionUrl:
    def test_returns_none_when_not_initialized(self):
        config = _make_oidc_config()
        result = get_end_session_url(config)
        assert result is None

    def test_returns_none_when_no_metadata(self):
        config = _make_oidc_config()
        create_oidc_client(config)
        # No server metadata loaded — should return None gracefully
        result = get_end_session_url(config)
        assert result is None

    def test_returns_endpoint_from_metadata(self):
        config = _make_oidc_config()
        oauth = create_oidc_client(config)
        # Manually inject server metadata with end_session_endpoint
        oauth.oidc.server_metadata = {"end_session_endpoint": "https://auth.example.com/logout"}
        result = get_end_session_url(config)
        assert result == "https://auth.example.com/logout"
