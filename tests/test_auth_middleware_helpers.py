"""Tests for extracted auth middleware helper functions.

These test the helpers directly (not through NiceGUI middleware),
since NiceGUI middleware requires a running app server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request


@pytest.fixture()
def _mock_storage():
    """Provide a dict-backed mock for app.storage.user."""
    storage = {}
    with patch("immich_memories.ui.app.app") as mock_app:
        mock_app.storage.user = storage
        yield storage


def _make_request(host: str = "10.0.0.1", path: str = "/", headers: dict | None = None) -> Request:
    """Build a minimal Starlette Request for testing."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "server": ("localhost", 8080),
    }
    req = Request(scope)
    req._client = MagicMock()  # WHY: Starlette client needs explicit mock
    req._client.host = host
    # Starlette reads .client from scope, so patch the property
    scope["client"] = (host, 12345)
    return req


class TestCheckLoginRateLimit:
    """Tests for _check_login_rate_limit helper."""

    def test_not_limited_returns_none(self, _mock_storage):
        from immich_memories.ui.app import _check_login_rate_limit

        request = _make_request(host="192.168.1.1")
        with patch("immich_memories.ui.app.is_rate_limited", return_value=False):
            result = _check_login_rate_limit(request)

        assert result is None
        assert _mock_storage["_client_ip"] == "192.168.1.1"

    def test_limited_returns_429(self, _mock_storage):
        from immich_memories.ui.app import _check_login_rate_limit

        request = _make_request(host="10.0.0.5")
        with patch("immich_memories.ui.app.is_rate_limited", return_value=True):
            result = _check_login_rate_limit(request)

        assert result is not None
        assert result.status_code == 429

    def test_no_client_uses_unknown(self, _mock_storage):
        from immich_memories.ui.app import _check_login_rate_limit

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/login",
            "query_string": b"",
            "headers": [],
            "server": ("localhost", 8080),
        }
        request = Request(scope)
        with patch("immich_memories.ui.app.is_rate_limited", return_value=False):
            result = _check_login_rate_limit(request)

        assert result is None
        assert _mock_storage["_client_ip"] == "unknown"


class TestTryHeaderAuth:
    """Tests for _try_header_auth helper."""

    def test_trusted_proxy_creates_session(self, _mock_storage):
        from immich_memories.ui.app import _try_header_auth

        auth_config = MagicMock()
        auth_config.trusted_proxies = ["10.0.0.0/8"]
        auth_config.user_header = "X-User"
        auth_config.email_header = "X-Email"
        request = _make_request(host="10.0.0.1", headers={"X-User": "alice", "X-Email": "a@b.com"})

        with (
            patch("immich_memories.ui.app.is_trusted_proxy", return_value=True),
            patch("immich_memories.ui.app.set_session") as mock_set,
        ):
            _try_header_auth(request, auth_config)

        mock_set.assert_called_once_with(
            _mock_storage, username="alice", provider="header", email="a@b.com"
        )

    def test_untrusted_proxy_does_nothing(self, _mock_storage):
        from immich_memories.ui.app import _try_header_auth

        auth_config = MagicMock()
        auth_config.trusted_proxies = ["10.0.0.0/8"]
        request = _make_request(host="192.168.1.1")

        with (
            patch("immich_memories.ui.app.is_trusted_proxy", return_value=False),
            patch("immich_memories.ui.app.set_session") as mock_set,
        ):
            _try_header_auth(request, auth_config)

        mock_set.assert_not_called()

    def test_already_authenticated_skips_session(self, _mock_storage):
        from immich_memories.ui.app import _try_header_auth

        _mock_storage["authenticated"] = True
        auth_config = MagicMock()
        auth_config.trusted_proxies = ["10.0.0.0/8"]
        auth_config.user_header = "X-User"
        request = _make_request(host="10.0.0.1", headers={"X-User": "alice"})

        with (
            patch("immich_memories.ui.app.is_trusted_proxy", return_value=True),
            patch("immich_memories.ui.app.set_session") as mock_set,
        ):
            _try_header_auth(request, auth_config)

        mock_set.assert_not_called()

    def test_no_user_header_skips_session(self, _mock_storage):
        from immich_memories.ui.app import _try_header_auth

        auth_config = MagicMock()
        auth_config.trusted_proxies = ["10.0.0.0/8"]
        auth_config.user_header = "X-User"
        request = _make_request(host="10.0.0.1")

        with (
            patch("immich_memories.ui.app.is_trusted_proxy", return_value=True),
            patch("immich_memories.ui.app.set_session") as mock_set,
        ):
            _try_header_auth(request, auth_config)

        mock_set.assert_not_called()


class TestCheckSessionTtl:
    """Tests for _check_session_ttl helper."""

    def test_no_authenticated_at_returns_none(self, _mock_storage):
        from immich_memories.ui.app import _check_session_ttl

        result = _check_session_ttl(24)
        assert result is None

    def test_fresh_session_returns_none(self, _mock_storage):
        from immich_memories.ui.app import _check_session_ttl

        _mock_storage["authenticated_at"] = datetime.now(UTC).isoformat()
        result = _check_session_ttl(24)
        assert result is None

    def test_expired_session_returns_redirect(self, _mock_storage):
        from immich_memories.ui.app import _check_session_ttl

        expired_time = datetime.now(UTC) - timedelta(hours=25)
        _mock_storage["authenticated_at"] = expired_time.isoformat()

        with patch("immich_memories.ui.app.clear_session") as mock_clear:
            result = _check_session_ttl(24)

        assert result is not None
        assert result.status_code == 307
        mock_clear.assert_called_once()
