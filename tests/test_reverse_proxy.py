"""Reverse-proxy settings: Secure session cookie and trusted X-Forwarded-* headers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from immich_memories.config_loader import Config
from immich_memories.ui.auth import record_failed_login, reset_rate_limiter
from immich_memories.ui.reverse_proxy import reverse_proxy_run_kwargs

_BASIC_AUTH = {"enabled": True, "provider": "basic", "username": "op", "password": "pw"}
_PROXY, _VISITOR, _STRANGER = "10.0.0.2", "203.0.113.5", "192.0.2.9"


def _session_cookie(run_kwargs: dict) -> str:
    """Set-Cookie header Starlette emits with the SessionMiddleware kwargs we hand NiceGUI."""

    async def touch_session(request: Request) -> PlainTextResponse:
        request.session["seen"] = True
        return PlainTextResponse("ok")

    middleware_kwargs = run_kwargs.get("session_middleware_kwargs") or {}
    app = Starlette(
        routes=[Route("/", touch_session)],
        middleware=[Middleware(SessionMiddleware, secret_key="test-secret", **middleware_kwargs)],  # noqa: S106
    )
    return TestClient(app).get("/").headers["set-cookie"].lower()


class TestSecureCookies:
    def test_secure_cookies_marks_the_session_cookie_secure(self):
        config = Config(server={"secure_cookies": True})

        cookie = _session_cookie(reverse_proxy_run_kwargs(config, environ={}))

        assert "; secure" in cookie

    def test_default_keeps_the_cookie_plain_for_http_localhost_setups(self):
        cookie = _session_cookie(reverse_proxy_run_kwargs(Config(), environ={}))

        assert "secure" not in cookie


def _request_after_proxy(run_kwargs: dict, peer: str, headers: dict[str, str]) -> Request:
    """The Request the app sees once uvicorn applied our ``forwarded_allow_ips``."""
    seen: list[Request] = []

    async def capture(scope, receive, send):
        seen.append(Request(scope))
        await PlainTextResponse("ok")(scope, receive, send)

    proxied = ProxyHeadersMiddleware(capture, trusted_hosts=run_kwargs["forwarded_allow_ips"])
    TestClient(proxied, client=(peer, 40000)).get("/login", headers=headers)
    return seen[0]


@pytest.fixture
def _login_storage():
    # WHY: the login helpers read/write NiceGUI's app.storage.user, which only
    # exists inside a running NiceGUI request.
    reset_rate_limiter()
    with patch("immich_memories.ui.app.app") as mock_app:
        mock_app.storage.user = {}
        yield mock_app.storage.user
    reset_rate_limiter()


class TestForwardedClientIp:
    def test_rate_limiter_keys_on_the_forwarded_visitor_behind_a_trusted_proxy(
        self, _login_storage
    ):
        from immich_memories.ui.app import _check_login_rate_limit

        config = Config(auth={**_BASIC_AUTH, "trusted_proxies": [_PROXY]})
        run_kwargs = reverse_proxy_run_kwargs(config, environ={})
        for _ in range(5):
            record_failed_login(_VISITOR)

        visitor = _request_after_proxy(run_kwargs, _PROXY, {"X-Forwarded-For": _VISITOR})
        neighbour = _request_after_proxy(run_kwargs, _PROXY, {"X-Forwarded-For": "203.0.113.6"})

        assert _check_login_rate_limit(visitor) is not None
        assert _check_login_rate_limit(neighbour) is None

    def test_forwarded_header_from_an_untrusted_peer_is_ignored(self, _login_storage):
        from immich_memories.ui.app import _check_login_rate_limit

        config = Config(auth={**_BASIC_AUTH, "trusted_proxies": [_PROXY]})
        run_kwargs = reverse_proxy_run_kwargs(config, environ={})
        for _ in range(5):
            record_failed_login(_STRANGER)

        spoofed = _request_after_proxy(run_kwargs, _STRANGER, {"X-Forwarded-For": _VISITOR})

        assert _check_login_rate_limit(spoofed) is not None


class TestForwardedAllowIpsPrecedence:
    def test_an_explicit_env_var_wins_over_config(self):
        config = Config(auth={**_BASIC_AUTH, "trusted_proxies": [_PROXY]})

        run_kwargs = reverse_proxy_run_kwargs(config, environ={"FORWARDED_ALLOW_IPS": "*"})

        # uvicorn reads FORWARDED_ALLOW_IPS itself; passing the kwarg would silently override it.
        assert "forwarded_allow_ips" not in run_kwargs


class TestHeaderProvider:
    def test_header_auth_still_sees_the_proxy_when_it_forwards_the_visitor_ip(self, _login_storage):
        from immich_memories.ui.app import _try_header_auth

        config = Config(auth={"enabled": True, "provider": "header", "trusted_proxies": [_PROXY]})
        run_kwargs = reverse_proxy_run_kwargs(config, environ={})

        request = _request_after_proxy(
            run_kwargs, _PROXY, {"X-Forwarded-For": _VISITOR, "Remote-User": "alice"}
        )
        _try_header_auth(request, config.auth)

        assert _login_storage.get("authenticated") is True


class _FakeOAuth:
    """authlib stand-in that records the redirect_uri the app hands to the IdP."""

    def __init__(self) -> None:
        self.oidc = self
        self.redirect_uri = ""

    async def authorize_redirect(self, request: Request, redirect_uri: str) -> RedirectResponse:
        self.redirect_uri = redirect_uri
        return RedirectResponse("https://idp.example.com/authorize")


class TestOidcRedirectUri:
    def _redirect_uri_for(self, peer: str) -> str:
        from immich_memories.ui.app import app

        config = Config(
            auth={
                "enabled": True,
                "provider": "oidc",
                "issuer_url": "https://idp.example.com",
                "client_id": "memories",
                "trusted_proxies": [_PROXY],
            }
        )
        run_kwargs = reverse_proxy_run_kwargs(config, environ={})
        served = ProxyHeadersMiddleware(app, trusted_hosts=run_kwargs["forwarded_allow_ips"])
        oauth = _FakeOAuth()
        with (
            # WHY: the app reads its config from disk; the test decides who the proxy is.
            patch("immich_memories.ui.app.get_config", return_value=config),
            # WHY: authlib would fetch the IdP's discovery document over the network.
            patch("immich_memories.ui.auth_oidc.create_oidc_client", return_value=oauth),
        ):
            TestClient(served, client=(peer, 40000), follow_redirects=False).get(
                "/auth/authorize",
                headers={"Host": "memories.example.com", "X-Forwarded-Proto": "https"},
            )
        return oauth.redirect_uri

    def test_redirect_uri_is_https_when_the_trusted_proxy_forwards_the_scheme(self):
        assert self._redirect_uri_for(_PROXY) == "https://memories.example.com/auth/callback"

    def test_redirect_uri_ignores_the_scheme_claimed_by_an_untrusted_peer(self):
        assert self._redirect_uri_for(_STRANGER) == "http://memories.example.com/auth/callback"
