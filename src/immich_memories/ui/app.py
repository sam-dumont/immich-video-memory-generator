"""Main NiceGUI application with Immich-inspired theme."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import socket
import sys
from pathlib import Path

# Configure logging before importing our modules
from immich_memories.logging_config import configure_logging

configure_logging()

import httpx
from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from immich_memories import __version__
from immich_memories.config import get_config, init_config_dir
from immich_memories.ui.auth import (
    clear_session,
    is_auth_enabled,
    is_bypass_path,
    is_trusted_proxy,
    set_session,
)
from immich_memories.ui.state import ensure_config, get_app_state
from immich_memories.ui.theme import apply_theme, render_theme_toggle

logger = logging.getLogger(__name__)


def _get_storage_secret() -> str:
    """Get storage secret from env var, file, or generate one.

    Priority: IMMICH_MEMORIES_STORAGE_SECRET env var > file > auto-generate.
    Docker/K8s users can mount a secret via the env var.
    """
    env_secret = os.environ.get("IMMICH_MEMORIES_STORAGE_SECRET")
    if env_secret:
        return env_secret

    secret_path = Path.home() / ".immich-memories" / ".storage_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_path.write_text(secret)
    secret_path.chmod(0o600)
    return secret


_STEPS = [
    ("Configuration", "settings", "/"),
    ("Clip Review", "video_library", "/step2"),
    ("Options", "tune", "/step3"),
    ("Export", "download", "/step4"),
]

_EXTRA_NAV = [
    ("Config", "description", "/settings/config"),
    ("Cache", "cached", "/settings/cache"),
]


# ============================================================================
# Shared UI Components
# ============================================================================


def _render_demo_toggle(state) -> None:
    """Render demo/privacy mode toggle in sidebar."""
    # Re-apply body class on page load if demo mode was already active
    if state.demo_mode:
        ui.run_javascript("document.body.classList.add('demo-mode')")

    def toggle_demo(e):
        state.demo_mode = e.value
        if e.value:
            ui.run_javascript("document.body.classList.add('demo-mode')")
        else:
            ui.run_javascript("document.body.classList.remove('demo-mode')")

    with ui.row().classes("items-center gap-2 mb-2"):
        ui.switch(value=state.demo_mode, on_change=toggle_demo).props("dense")
        ui.label("Demo mode").classes("text-xs").style("color: var(--im-text-secondary)")


def _render_auth_controls() -> None:
    """Render username badge and sign-out button when auth is enabled."""
    config = get_config()
    if not is_auth_enabled(config.auth):
        return
    username = app.storage.user.get("username", "")
    if username:
        with ui.row().classes("items-center gap-2 mt-2"):
            ui.icon("person").classes("text-sm").style("color: var(--im-text-secondary)")
            ui.label(username).classes("text-xs").style("color: var(--im-text-secondary)")
    ui.button(
        "Sign out",
        icon="logout",
        on_click=lambda: ui.navigate.to("/logout"),
    ).props("flat dense no-caps size=sm").classes("w-full").style("color: var(--im-text-secondary)")


def render_step_indicator(current_step: int) -> None:  # noqa: ARG001
    """Step indicator — removed in favor of sidebar navigation.

    The sidebar already highlights the active step. Keeping this function
    as a no-op so callers don't need to change.
    """


def render_sidebar(current_step: int) -> None:
    """Render Immich-style sidebar navigation."""
    state = get_app_state()
    # WHY: ensure_config lazily loads config into per-session state on first page load.
    # Doing it here means every page gets it automatically.
    ensure_config(state)

    with ui.left_drawer(value=True).classes("p-0"):
        # Branding
        with ui.row().classes("items-center gap-3 px-5 py-4"):
            ui.icon("movie").classes("text-2xl").style("color: var(--im-primary)")
            ui.label("Immich Memories").classes("text-lg font-bold").style("color: var(--im-text)")

        # Nav items
        with ui.column().classes("gap-0 px-3 mt-2"):
            for i, (name, icon, path) in enumerate(_STEPS):
                step_num = i + 1
                is_active = step_num == current_step

                def make_nav(s: int, p: str):
                    def handler():
                        state.step = s
                        ui.navigate.to(p)

                    return handler

                classes = "im-nav-item"
                if is_active:
                    classes += " im-nav-active"

                with (
                    ui.element("div").classes(classes).on("click", make_nav(step_num, path)),
                    ui.row().classes("items-center gap-3 w-full"),
                ):
                    ui.icon(icon).classes("text-xl")
                    ui.label(name).classes("text-sm")

        # Extra nav (settings)
        ui.element("div").classes("mx-4 my-3").style(
            "height: 1px; background: var(--im-border-light)"
        )
        with ui.column().classes("gap-0 px-3"):
            for name, icon, path in _EXTRA_NAV:

                def make_extra_nav(p: str):
                    def handler():
                        ui.navigate.to(p)

                    return handler

                with (
                    ui.element("div").classes("im-nav-item").on("click", make_extra_nav(path)),
                    ui.row().classes("items-center gap-3 w-full"),
                ):
                    ui.icon(icon).classes("text-xl")
                    ui.label(name).classes("text-sm")

        # Spacer + toggles at bottom
        ui.element("div").classes("flex-grow")
        with ui.column().classes("px-5 pb-4 mt-auto"):
            ui.element("div").classes("mb-3").style(
                "height: 1px; background: var(--im-border-light)"
            )
            config = get_config()
            if config.server.enable_demo_mode:
                _render_demo_toggle(state)
            render_theme_toggle()
            _render_auth_controls()


def page_header(title: str, step: int) -> None:
    """Render a consistent page header with step indicator."""
    ui.page_title(f"Immich Memories - {title}")
    render_step_indicator(step)
    ui.label(title).classes("text-xl font-semibold mb-4").style("color: var(--im-text)")


# ============================================================================
# Page Routes
# ============================================================================


@ui.page("/")
def index_page() -> None:
    """Step 1: Configuration page."""
    from immich_memories.ui.pages.step1_config import render_step1

    apply_theme()
    render_sidebar(1)
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Configuration", 1)
        render_step1()


@ui.page("/step2")
def step2_page() -> None:
    """Step 2: Clip Review page."""
    from immich_memories.ui.pages.step2_review import render_step2

    apply_theme()
    render_sidebar(2)
    with ui.column().classes("w-full max-w-6xl mx-auto p-6"):
        page_header("Clip Review", 2)
        render_step2()


@ui.page("/step3")
def step3_page() -> None:
    """Step 3: Generation Options page."""
    from immich_memories.ui.pages.step3_options import render_step3

    apply_theme()
    render_sidebar(3)
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Generation Options", 3)
        render_step3()


@ui.page("/step4")
def step4_page() -> None:
    """Step 4: Preview & Export page."""
    from immich_memories.ui.pages.step4_export import render_step4

    apply_theme()
    render_sidebar(4)
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Preview & Export", 4)
        render_step4()


@ui.page("/settings/config")
def config_page() -> None:
    """Configuration viewer/editor page."""
    from immich_memories.ui.pages.settings_config import render_config_page

    apply_theme()
    render_sidebar(0)
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        ui.page_title("Immich Memories - Configuration")
        ui.label("Configuration").classes("text-2xl font-bold mb-4").style("color: var(--im-text)")
        render_config_page()


@ui.page("/settings/cache")
def cache_page() -> None:
    """Cache management settings page."""
    from immich_memories.ui.pages.step1_cache import render_cache_management

    apply_theme()
    render_sidebar(0)
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        ui.page_title("Immich Memories - Cache")
        ui.label("Cache Management").classes("text-2xl font-bold mb-4").style(
            "color: var(--im-text)"
        )
        render_cache_management()


# ============================================================================
# Health Endpoint
# ============================================================================


async def _check_immich_reachable(config) -> bool:
    """Ping Immich server, return True if reachable."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
        )
        return resp.status_code == 200


def _get_last_successful_run() -> str | None:
    """Return ISO timestamp of last completed run, or None."""
    from immich_memories.tracking.run_database import RunDatabase

    db = RunDatabase()
    runs = db.list_runs(limit=1, status="completed")
    if runs and runs[0].completed_at:
        return runs[0].completed_at.isoformat()
    return None


async def _health_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return JSON health status with Immich connectivity and last run info."""
    config = get_config()

    immich_reachable = False
    with contextlib.suppress(Exception):
        immich_reachable = await _check_immich_reachable(config)

    last_successful_run: str | None = None
    with contextlib.suppress(Exception):
        last_successful_run = _get_last_successful_run()

    status = "ok" if immich_reachable else "degraded"

    return JSONResponse(
        {
            "status": status,
            "immich_reachable": immich_reachable,
            "last_successful_run": last_successful_run,
            "version": __version__,
        }
    )


app.add_api_route("/health", _health_handler, methods=["GET"])


# ============================================================================
# Auth: Middleware + Routes
# ============================================================================


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Provider-agnostic auth check using NiceGUI's app.storage.user.

    WHY @app.middleware('http') not BaseHTTPMiddleware:
    BaseHTTPMiddleware breaks NiceGUI websockets. NiceGUI's middleware
    decorator only runs on HTTP requests and has access to app.storage.user.
    """
    config = get_config()
    if not is_auth_enabled(config.auth):
        return await call_next(request)

    if is_bypass_path(request.url.path):
        return await call_next(request)

    # Header auth: auto-session from trusted proxy
    if config.auth.provider == "header":
        client_ip = request.client.host if request.client else ""
        if is_trusted_proxy(client_ip, config.auth.trusted_proxies):
            user = request.headers.get(config.auth.user_header, "")
            if user and not app.storage.user.get("authenticated"):
                email = request.headers.get(config.auth.email_header, "")
                set_session(app.storage.user, username=user, provider="header", email=email)

    if not app.storage.user.get("authenticated"):
        return RedirectResponse("/login", status_code=307)

    # Session TTL check
    from datetime import UTC, datetime, timedelta

    authenticated_at_str = app.storage.user.get("authenticated_at")
    if authenticated_at_str:
        authenticated_at = datetime.fromisoformat(authenticated_at_str)
        if datetime.now(UTC) > authenticated_at + timedelta(hours=config.auth.session_ttl_hours):
            clear_session(app.storage.user)
            return RedirectResponse("/login", status_code=307)

    return await call_next(request)


@ui.page("/login")
def login_page_route() -> None:
    """Login page."""
    from immich_memories.ui.pages.login import render_login_page

    config = get_config()
    if config.auth.enabled and config.auth.provider == "oidc" and config.auth.auto_launch:
        ui.navigate.to("/auth/authorize")
        return
    render_login_page(config.auth)


async def _logout_handler(request: Request) -> RedirectResponse:  # noqa: ARG001
    """Clear session and redirect to login."""
    from immich_memories.ui.state import remove_session

    config = get_config()
    session_id = app.storage.user.get("session_id")
    auth_provider = app.storage.user.get("auth_provider")
    if session_id:
        remove_session(session_id)
    clear_session(app.storage.user)

    # WHY: OIDC providers may have an end_session_endpoint for full sign-out.
    if auth_provider == config.auth.provider == "oidc":
        from immich_memories.ui.auth_oidc import get_end_session_url

        end_session = get_end_session_url(config.auth)
        if end_session:
            return RedirectResponse(end_session)

    return RedirectResponse("/login", status_code=307)


app.add_api_route("/logout", _logout_handler, methods=["GET"])


async def _oidc_authorize(request: Request) -> RedirectResponse:
    """Redirect to OIDC provider's authorization endpoint."""
    config = get_config()
    if config.auth.provider != "oidc":
        return RedirectResponse("/login")
    from immich_memories.ui.auth_oidc import create_oidc_client

    oauth = create_oidc_client(config.auth)
    redirect_uri = str(request.url_for("_oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, redirect_uri)  # type: ignore[return-value]


async def _oidc_callback(request: Request) -> RedirectResponse:
    """Handle OIDC callback — exchange code for tokens and create session."""
    config = get_config()
    from immich_memories.ui.auth_oidc import create_oidc_client, extract_user_from_token

    oauth = create_oidc_client(config.auth)
    # WHY: authlib uses request.session for OIDC state/PKCE internally.
    # Our auth session goes in app.storage.user (NiceGUI's store).
    token = await oauth.oidc.authorize_access_token(request)
    username, email = extract_user_from_token(token)
    set_session(app.storage.user, username=username, provider="oidc", email=email)
    return RedirectResponse("/")


app.add_api_route("/auth/authorize", _oidc_authorize, methods=["GET"])
app.add_api_route("/auth/callback", _oidc_callback, methods=["GET"])


# ============================================================================
# App Startup / Shutdown
# ============================================================================


def initialize_app() -> None:
    """Initialize shared resources on startup."""
    init_config_dir()
    config = get_config(reload=True)
    logger.info(
        "Application initialized (auth=%s)",
        "enabled" if config.auth.enabled else "disabled",
    )


app.on_startup(initialize_app)


async def _session_cleanup_loop() -> None:
    """Periodically clean up stale sessions."""
    from immich_memories.ui.state import cleanup_stale_sessions

    while True:
        await asyncio.sleep(900)  # 15 minutes
        cleanup_stale_sessions()


def _start_cleanup_task() -> None:
    asyncio.ensure_future(_session_cleanup_loop())


app.on_startup(_start_cleanup_task)


def _shutdown_app() -> None:
    """Clean up on application shutdown."""
    logger.info("Application shutting down")


app.on_shutdown(_shutdown_app)


# ============================================================================
# Port check + run
# ============================================================================


def _is_port_free(host: str, port: int) -> bool:
    """Check if a port is available for binding."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def main(port: int = 8080, host: str = "0.0.0.0", reload: bool = False) -> None:  # noqa: S104
    """Run the NiceGUI application."""
    if not _is_port_free(host, port):
        logger.error(
            f"Port {port} is already in use. "
            f"Stop the existing process: lsof -ti :{port} | xargs kill"
        )
        sys.exit(1)

    kwargs: dict = {
        "title": "Immich Memories",
        "favicon": "🎬",
        "port": port,
        "host": host,
        "reload": reload,
        "storage_secret": _get_storage_secret(),
    }
    if reload:
        kwargs["uvicorn_reload_includes"] = "*.py"
        kwargs["uvicorn_reload_excludes"] = ".*, *.log, *.db, *.db-journal"
    ui.run(**kwargs)


if __name__ in {"__main__", "__mp_main__"}:
    main()
