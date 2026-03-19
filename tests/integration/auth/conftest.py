"""Fixtures for OIDC integration tests using oidc-provider-mock."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth import (
    is_bypass_path,
    set_session,
    verify_credentials,
)
from immich_memories.ui.auth_oidc import (
    create_oidc_client,
    extract_user_from_token,
    reset_oidc_client,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import werkzeug.serving

logger = logging.getLogger(__name__)


def _create_test_auth_middleware(auth_config: AuthConfig) -> type[BaseHTTPMiddleware]:
    """Starlette middleware for integration tests (NOT used in production).

    WHY: production uses @app.middleware('http') with app.storage.user (NiceGUI).
    Tests use pure Starlette with request.session. Same logic, different session store.
    """

    class TestAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if is_bypass_path(request.url.path):
                return await call_next(request)
            if not request.session.get("authenticated"):
                return RedirectResponse(url="/login", status_code=307)
            return await call_next(request)

    return TestAuthMiddleware


@pytest.fixture(scope="session")
def oidc_server() -> Iterator[werkzeug.serving.BaseWSGIServer]:
    """Start oidc-provider-mock on a random port in a background thread."""
    # WHY: authlib refuses HTTP callback URIs unless this is set
    os.environ["AUTHLIB_INSECURE_TRANSPORT"] = "1"

    from oidc_provider_mock import run_server_in_thread

    with run_server_in_thread(port=0) as server:
        logger.info("OIDC mock server running on port %d", server.server_port)
        yield server


@pytest.fixture()
def auth_config(oidc_server: werkzeug.serving.BaseWSGIServer) -> AuthConfig:
    """AuthConfig pointing at the mock OIDC server."""
    return AuthConfig(
        enabled=True,
        provider="oidc",
        issuer_url=f"http://localhost:{oidc_server.server_port}",
        client_id="test-client",
        client_secret="test-secret",  # noqa: S106
        scope="openid email profile",
        allow_insecure_issuer=True,
    )


@pytest.fixture()
def basic_auth_config() -> AuthConfig:
    """AuthConfig for basic auth testing."""
    return AuthConfig(
        enabled=True,
        provider="basic",
        username="admin",
        password="secret",  # noqa: S106
    )


@pytest.fixture(autouse=True)
def _reset_oidc_singleton() -> Iterator[None]:
    """Reset the OIDC client singleton between tests."""
    reset_oidc_client()
    yield
    reset_oidc_client()


def _build_oidc_routes(auth_config: AuthConfig) -> list[Route]:
    """Build OIDC auth routes mirroring app.py's handlers."""

    async def oidc_authorize(request: Request) -> RedirectResponse:
        oauth = create_oidc_client(auth_config)
        redirect_uri = str(request.url_for("oidc_callback"))
        return await oauth.oidc.authorize_redirect(request, redirect_uri)

    async def oidc_callback(request: Request) -> RedirectResponse:
        oauth = create_oidc_client(auth_config)
        token = await oauth.oidc.authorize_access_token(request)
        username, email = extract_user_from_token(token)
        set_session(request.session, username=username, provider="oidc", email=email)
        return RedirectResponse("/")

    return [
        Route("/auth/authorize", oidc_authorize, name="oidc_authorize"),
        Route("/auth/callback", oidc_callback, name="oidc_callback"),
    ]


def _build_basic_auth_routes(auth_config: AuthConfig) -> list[Route]:
    """Build basic auth login route for testing."""

    async def login_handler(
        request: Request,
    ) -> RedirectResponse | JSONResponse | PlainTextResponse:
        if request.method == "POST":
            form = await request.form()
            username = str(form.get("username", ""))
            password = str(form.get("password", ""))
            if verify_credentials(username, password, auth_config):
                set_session(request.session, username=username, provider="basic")
                return RedirectResponse("/", status_code=303)
            return JSONResponse({"error": "invalid credentials"}, status_code=401)
        return PlainTextResponse("login page")

    return [
        Route("/login", login_handler, methods=["GET", "POST"], name="login"),
    ]


def _build_common_routes() -> list[Route]:
    """Build common routes shared by all test apps."""

    async def index(request: Request) -> JSONResponse:
        return JSONResponse({"session": dict(request.session), "page": "index"})

    async def health(request: Request) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"status": "ok"})

    async def login_page(request: Request) -> PlainTextResponse:  # noqa: ARG001
        return PlainTextResponse("login page")

    return [
        Route("/", index, name="index"),
        Route("/health", health, name="health"),
        Route("/login", login_page, name="login"),
    ]


def make_test_app(auth_config: AuthConfig) -> Starlette:
    """Create a Starlette test app with session + auth middleware + routes."""
    routes = _build_common_routes()

    if auth_config.provider == "oidc":
        routes.extend(_build_oidc_routes(auth_config))
    elif auth_config.provider == "basic":
        basic_routes = _build_basic_auth_routes(auth_config)
        basic_paths = {r.path for r in basic_routes}
        routes = [r for r in routes if r.path not in basic_paths]
        routes.extend(basic_routes)

    app = Starlette(routes=routes)
    app.add_middleware(_create_test_auth_middleware(auth_config))
    # WHY: SessionMiddleware must wrap AuthMiddleware so request.session is available
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # noqa: S106
    return app


@pytest.fixture()
def starlette_app(auth_config: AuthConfig) -> Starlette:
    """Starlette test app with OIDC routes and auth middleware."""
    return make_test_app(auth_config)


@pytest.fixture()
def client(starlette_app: Starlette) -> TestClient:
    """TestClient that does NOT follow redirects (for inspecting 302s)."""
    return TestClient(starlette_app, follow_redirects=False)
