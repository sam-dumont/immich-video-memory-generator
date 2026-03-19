"""Tests for basic auth credential verification."""

from __future__ import annotations

from unittest.mock import patch

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth import verify_credentials


def _basic_config(username: str = "admin", password: str = "secret") -> AuthConfig:  # noqa: S107
    return AuthConfig(
        enabled=True,
        provider="basic",
        username=username,
        password=password,  # noqa: S106
    )


class TestVerifyCredentials:
    """Constant-time credential verification."""

    def test_correct_credentials(self):
        cfg = _basic_config()
        assert verify_credentials("admin", "secret", cfg) is True

    def test_wrong_password(self):
        cfg = _basic_config()
        assert verify_credentials("admin", "wrong", cfg) is False

    def test_wrong_username(self):
        cfg = _basic_config()
        assert verify_credentials("nobody", "secret", cfg) is False

    def test_both_wrong(self):
        cfg = _basic_config()
        assert verify_credentials("nobody", "wrong", cfg) is False

    def test_empty_username(self):
        cfg = _basic_config()
        assert verify_credentials("", "secret", cfg) is False

    def test_empty_password(self):
        cfg = _basic_config()
        assert verify_credentials("admin", "", cfg) is False

    def test_empty_both(self):
        cfg = _basic_config()
        assert verify_credentials("", "", cfg) is False

    def test_uses_constant_time_comparison(self):
        """secrets.compare_digest is called for BOTH username and password."""
        cfg = _basic_config()
        # WHY: verify constant-time comparison to prevent timing attacks
        with patch("immich_memories.ui.auth.secrets.compare_digest", return_value=True) as mock_cd:
            verify_credentials("admin", "secret", cfg)
            assert mock_cd.call_count == 2
