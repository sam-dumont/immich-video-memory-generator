"""Origin validation for the OIDC callback, against a configured public URL.

The previous check compared `urlparse(str(request.url)).netloc` to the `Host`
header. Starlette builds `request.url` *from* that header, so the comparison was
always true -- and its tests passed only because they used a MagicMock whose url
and headers were set independently, a shape the real object cannot take.

These tests build real Starlette Requests from an ASGI scope, so a forged Host
header produces the URL it would really produce and the check can actually fail.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from immich_memories.ui.auth_oidc import oidc_redirect_uri, validate_callback_origin

_PUBLIC = "https://memories.example.com"


def _callback_request(host: str, scheme: str = "https") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/callback",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "scheme": scheme,
            "server": ("memories.example.com", 443),
        }
    )


class TestValidateCallbackOrigin:
    def test_it_accepts_the_configured_public_origin(self):
        assert validate_callback_origin(_callback_request("memories.example.com"), _PUBLIC)

    def test_it_rejects_a_forged_host_header(self):
        """The case the old check could never see."""
        assert not validate_callback_origin(_callback_request("evil.example"), _PUBLIC)

    @pytest.mark.parametrize(
        ("host", "scheme"),
        [
            ("memories.example.com:8443", "https"),
            ("memories.example.com", "http"),
            ("memories.example.com.evil.example", "https"),
        ],
    )
    def test_it_rejects_a_different_port_scheme_or_suffix(self, host, scheme):
        assert not validate_callback_origin(_callback_request(host, scheme), _PUBLIC)

    def test_an_implicit_port_matches_the_explicit_one(self):
        request = _callback_request("memories.example.com:443")
        assert validate_callback_origin(request, _PUBLIC)

    def test_no_host_header_falls_back_to_the_server_binding(self):
        """Absent a Host header Starlette uses `scope["server"]`, which the
        caller cannot forge -- so this is a pass, not a failure."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/auth/callback",
                "query_string": b"",
                "headers": [],
                "scheme": "https",
                "server": ("memories.example.com", 443),
            }
        )
        assert validate_callback_origin(request, _PUBLIC)

    def test_without_a_public_url_it_does_not_pretend_to_check(self):
        """Unconfigured is today's behaviour: the IdP's registered redirect_uri
        is the control, and claiming otherwise is what the old check did."""
        assert validate_callback_origin(_callback_request("anything.example"), "")


class TestOidcRedirectUri:
    def test_it_keeps_the_derived_uri_when_no_public_url_is_set(self):
        derived = "http://10.0.0.4:8080/auth/callback"
        assert oidc_redirect_uri(derived, "") == derived

    def test_it_rebuilds_the_uri_on_the_public_origin(self):
        """Behind a proxy the derived host is internal, and the IdP rejects a
        redirect_uri that is not the registered one."""
        derived = "http://10.0.0.4:8080/auth/callback"
        assert oidc_redirect_uri(derived, _PUBLIC) == f"{_PUBLIC}/auth/callback"

    def test_a_public_url_with_a_path_prefix_is_preserved(self):
        derived = "http://10.0.0.4:8080/auth/callback"
        assert (
            oidc_redirect_uri(derived, "https://example.com/memories")
            == "https://example.com/memories/auth/callback"
        )


class TestPublicUrlConfig:
    def test_a_trailing_slash_is_normalised_away(self):
        from immich_memories.config_models_auth import AuthConfig

        assert AuthConfig(public_url="https://host/").public_url == "https://host"

    @pytest.mark.parametrize("value", ["memories.example.com", "ftp://host", "/memories"])
    def test_it_refuses_anything_that_is_not_an_absolute_http_url(self, value):
        """A relative or schemeless value would silently disable the check."""
        from pydantic import ValidationError

        from immich_memories.config_models_auth import AuthConfig

        with pytest.raises(ValidationError, match="absolute http"):
            AuthConfig(public_url=value)
