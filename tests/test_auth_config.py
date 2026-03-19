"""Tests for AuthConfig model."""

from __future__ import annotations

import pytest

from immich_memories.config_loader import Config, _apply_env_overrides
from immich_memories.config_models_auth import AuthConfig


class TestAuthConfigDefaults:
    """AuthConfig has safe defaults."""

    def test_disabled_by_default(self):
        cfg = AuthConfig()
        assert cfg.enabled is False

    def test_provider_default(self):
        cfg = AuthConfig()
        assert cfg.provider == "basic"

    def test_session_ttl_hours_default(self):
        cfg = AuthConfig()
        assert cfg.session_ttl_hours == 24

    def test_header_names_defaults(self):
        cfg = AuthConfig()
        assert cfg.user_header == "Remote-User"
        assert cfg.email_header == "Remote-Email"

    def test_oidc_scope_default(self):
        cfg = AuthConfig()
        assert cfg.scope == "openid email profile"

    def test_auto_launch_default(self):
        cfg = AuthConfig()
        assert cfg.auto_launch is False

    def test_insecure_issuer_default(self):
        cfg = AuthConfig()
        assert cfg.allow_insecure_issuer is False


class TestAuthConfigValidation:
    """AuthConfig validates provider requirements when enabled."""

    def test_header_provider_requires_trusted_proxies(self):
        with pytest.raises(ValueError, match="trusted_proxies"):
            AuthConfig(enabled=True, provider="header")

    def test_header_provider_with_proxies_ok(self):
        cfg = AuthConfig(enabled=True, provider="header", trusted_proxies=["10.0.0.1"])
        assert cfg.provider == "header"

    def test_basic_requires_username(self):
        with pytest.raises(ValueError, match="username"):
            AuthConfig(enabled=True, provider="basic", password="secret")  # noqa: S106

    def test_basic_requires_password(self):
        with pytest.raises(ValueError, match="password"):
            AuthConfig(enabled=True, provider="basic", username="admin")

    def test_basic_with_creds_ok(self):
        cfg = AuthConfig(
            enabled=True,
            provider="basic",
            username="admin",
            password="secret",  # noqa: S106
        )
        assert cfg.provider == "basic"

    def test_oidc_requires_issuer_url(self):
        with pytest.raises(ValueError, match="issuer_url"):
            AuthConfig(enabled=True, provider="oidc", client_id="myapp")

    def test_oidc_requires_client_id(self):
        with pytest.raises(ValueError, match="client_id"):
            AuthConfig(enabled=True, provider="oidc", issuer_url="https://auth.example.com")

    def test_oidc_with_full_config_ok(self):
        cfg = AuthConfig(
            enabled=True,
            provider="oidc",
            issuer_url="https://auth.example.com",
            client_id="myapp",
            client_secret="secret",  # noqa: S106
        )
        assert cfg.provider == "oidc"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValueError):
            AuthConfig(provider="kerberos")  # type: ignore[arg-type]

    def test_disabled_config_skips_validation(self):
        # enabled=False: oidc with no issuer_url should be fine
        cfg = AuthConfig(enabled=False, provider="oidc")
        assert cfg.enabled is False
        assert cfg.provider == "oidc"


class TestAuthConfigEnvExpansion:
    """AuthConfig expands environment variables in secrets."""

    def test_password_env_expansion(self, monkeypatch):
        monkeypatch.setenv("MY_PASSWORD", "hunter2")
        cfg = AuthConfig(
            enabled=True,
            provider="basic",
            username="admin",
            password="${MY_PASSWORD}",  # noqa: S106
        )
        assert cfg.password == "hunter2"  # noqa: S105

    def test_client_secret_env_expansion(self, monkeypatch):
        monkeypatch.setenv("OIDC_SECRET", "supersecret")
        cfg = AuthConfig(
            enabled=True,
            provider="oidc",
            issuer_url="https://auth.example.com",
            client_id="myapp",
            client_secret="${OIDC_SECRET}",  # noqa: S106
        )
        assert cfg.client_secret == "supersecret"  # noqa: S105


class TestAuthEnvVarShortcut:
    """IMMICH_MEMORIES_AUTH_USERNAME + PASSWORD env vars auto-enable basic auth."""

    def test_both_set_enables_basic_auth(self, monkeypatch):
        monkeypatch.setenv("IMMICH_MEMORIES_AUTH_USERNAME", "admin")
        monkeypatch.setenv("IMMICH_MEMORIES_AUTH_PASSWORD", "secret")
        config = Config()
        _apply_env_overrides(config)
        assert config.auth.enabled is True
        assert config.auth.provider == "basic"
        assert config.auth.username == "admin"
        assert config.auth.password == "secret"  # noqa: S105

    def test_only_username_set_stays_disabled(self, monkeypatch):
        monkeypatch.setenv("IMMICH_MEMORIES_AUTH_USERNAME", "admin")
        monkeypatch.delenv("IMMICH_MEMORIES_AUTH_PASSWORD", raising=False)
        config = Config()
        _apply_env_overrides(config)
        assert config.auth.enabled is False
