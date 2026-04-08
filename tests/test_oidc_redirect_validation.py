"""Tests for OIDC callback origin validation."""

from __future__ import annotations

from unittest.mock import MagicMock

from immich_memories.ui.auth_oidc import validate_callback_origin


def _make_request(url: str, host_header: str) -> MagicMock:
    """Build a mock Starlette Request with the given URL and Host header."""
    request = MagicMock()
    request.url = MagicMock()
    request.url.__str__ = MagicMock(return_value=url)
    request.headers = {"host": host_header}
    return request


class TestValidateCallbackOrigin:
    """Reject OIDC callbacks that don't match the expected host."""

    def test_matching_host(self):
        req = _make_request("https://app.example.com/auth/callback", "app.example.com")
        assert validate_callback_origin(req) is True

    def test_matching_host_with_port(self):
        req = _make_request("https://app.example.com:8080/auth/callback", "app.example.com:8080")
        assert validate_callback_origin(req) is True

    def test_mismatched_host(self):
        req = _make_request("https://evil.example.com/auth/callback", "app.example.com")
        assert validate_callback_origin(req) is False

    def test_missing_host_header(self):
        req = _make_request("https://app.example.com/auth/callback", "")
        assert validate_callback_origin(req) is False

    def test_empty_url(self):
        req = _make_request("", "app.example.com")
        assert validate_callback_origin(req) is False

    def test_localhost_with_port(self):
        req = _make_request("http://localhost:8080/auth/callback", "localhost:8080")
        assert validate_callback_origin(req) is True

    def test_port_mismatch(self):
        req = _make_request("https://app.example.com:9090/auth/callback", "app.example.com:8080")
        assert validate_callback_origin(req) is False
