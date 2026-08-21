"""Main NiceGUI application with Immich-inspired theme."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from immich_memories.config_loader import Config
    from immich_memories.config_models_auth import AuthConfig

import httpx
from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from immich_memories import __version__
from immich_memories.automation.in_process_scheduler import automation_scheduler
from immich_memories.config import get_config, init_config_dir
from immich_memories.security import (
    configured_secret_values,
    sanitize_error_message,
    write_secret_file,
)
from immich_memories.ui.auth import (
    clear_session,
    is_auth_enabled,
    is_bypass_path,
    is_health_probe_path,
    is_rate_limited,
    is_trusted_proxy,
    set_session,
)
from immich_memories.ui.reverse_proxy import reverse_proxy_run_kwargs
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
    secret = secrets.token_hex(32)
    write_secret_file(secret_path, secret)
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


def _is_step_complete(state, step: int) -> bool:
    """Check whether a wizard step has been completed based on AppState."""
    if step == 1:
        return state.config is not None and state.date_range is not None
    if step == 2:
        return len(state.selected_clip_ids) > 0
    if step == 3:
        return bool(state.generation_options)
    return False


def _render_step_nav(state, current_step: int) -> None:  # pragma: no cover
    """Render the 4-step wizard nav items with completion indicators."""
    with ui.column().classes("gap-0 px-3 mt-2"):
        for i, (name, icon, path) in enumerate(_STEPS):
            step_num = i + 1
            is_active = step_num == current_step
            is_complete = _is_step_complete(state, step_num)

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
                if is_complete and not is_active:
                    ui.element("div").classes("flex-grow")
                    ui.icon("check_circle").classes("text-xs").style(
                        "color: var(--im-success); font-size: 16px"
                    )


def _render_extra_nav() -> None:  # pragma: no cover
    """Render settings/cache nav items below the step nav."""
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


def render_sidebar(current_step: int):  # pragma: no cover
    """Render Immich-style sidebar navigation. Returns drawer for toggle."""
    state = get_app_state()
    # WHY: ensure_config lazily loads config into per-session state on first page load.
    # Doing it here means every page gets it automatically.
    ensure_config(state)

    with ui.left_drawer(value=True).classes("p-0") as drawer:
        # Branding
        with ui.row().classes("items-center gap-3 px-5 py-3"):
            ui.icon("movie").classes("text-2xl").style("color: var(--im-primary)")
            ui.label("Immich Memories").classes("text-lg font-bold").style("color: var(--im-text)")

        _render_step_nav(state, current_step)

        # Extra nav (settings)
        ui.element("div").classes("mx-4 my-3").style(
            "height: 1px; background: var(--im-border-light)"
        )
        _render_extra_nav()

        # Spacer + toggles at bottom
        ui.element("div").classes("flex-grow")
        with ui.column().classes("px-5 pb-3 mt-auto"):
            ui.element("div").classes("mb-3").style(
                "height: 1px; background: var(--im-border-light)"
            )
            config = get_config()
            if config.server.enable_demo_mode:
                _render_demo_toggle(state)
            render_theme_toggle()
            _render_auth_controls()

    return drawer


def page_header(title: str, step: int, drawer=None) -> None:
    """Render a consistent page header with step indicator."""
    ui.page_title(f"Immich Memories - {title}")
    render_step_indicator(step)
    with ui.row().classes("w-full items-center gap-2 mb-2"):
        if drawer is not None:
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round").style(
                "color: var(--im-text-muted)"
            )
        ui.label(title).classes("text-xl font-semibold").style("color: var(--im-text)")


# ============================================================================
# Page Routes
# ============================================================================


@ui.page("/")
def index_page() -> None:
    """Step 1: Configuration page."""
    from immich_memories.ui.pages.step1_config import render_step1

    apply_theme()
    d = render_sidebar(1)
    with ui.column().classes("w-full px-8 py-5"):
        page_header("Configuration", 1, drawer=d)
        render_step1()


@ui.page("/step2")
def step2_page() -> None:
    """Step 2: Clip Review page."""
    from immich_memories.ui.pages.step2_review import render_step2

    apply_theme()
    d = render_sidebar(2)
    with ui.column().classes("w-full px-8 py-5"):
        page_header("Clip Review", 2, drawer=d)
        render_step2()


@ui.page("/step3")
def step3_page() -> None:
    """Step 3: Generation Options page."""
    from immich_memories.ui.pages.step3_options import render_step3

    apply_theme()
    d = render_sidebar(3)
    with ui.column().classes("w-full px-8 py-5"):
        page_header("Generation Options", 3, drawer=d)
        render_step3()


@ui.page("/step4")
def step4_page() -> None:
    """Step 4: Preview & Export page."""
    from immich_memories.ui.pages.step4_export import render_step4

    apply_theme()
    d = render_sidebar(4)
    with ui.column().classes("w-full px-8 py-5"):
        page_header("Preview & Export", 4, drawer=d)
        render_step4()


@ui.page("/settings/config")
def config_page() -> None:
    """Configuration viewer/editor page."""
    from immich_memories.ui.pages.settings_config import render_config_page

    apply_theme()
    d = render_sidebar(0)
    with ui.column().classes("w-full px-8 py-5"):
        ui.page_title("Immich Memories - Configuration")
        with ui.row().classes("w-full items-center gap-2 mb-2"):
            ui.button(icon="menu", on_click=d.toggle).props("flat dense round").style(
                "color: var(--im-text-muted)"
            )
            ui.label("Configuration").classes("text-2xl font-bold").style("color: var(--im-text)")
        render_config_page()


@ui.page("/settings/cache")
def cache_page() -> None:
    """Cache management settings page."""
    from immich_memories.ui.pages.step1_cache import render_cache_management

    apply_theme()
    d = render_sidebar(0)
    with ui.column().classes("w-full px-8 py-5"):
        ui.page_title("Immich Memories - Cache")
        with ui.row().classes("w-full items-center gap-2 mb-2"):
            ui.button(icon="menu", on_click=d.toggle).props("flat dense round").style(
                "color: var(--im-text-muted)"
            )
            ui.label("Cache Management").classes("text-2xl font-bold").style(
                "color: var(--im-text)"
            )
        render_cache_management()


# ============================================================================
# Health Endpoint
# ============================================================================


@dataclass(frozen=True)
class _ImmichDependency:
    """Sanitized result of the dependency probe used by health responses."""

    status: Literal[
        "ready",
        "missing_configuration",
        "unreachable",
        "authentication_failed",
        "unsupported_version",
    ]
    reachable: bool
    resolved_api_version: str | None = None


async def _check_immich_dependency(config) -> _ImmichDependency:
    """Resolve the configured API policy and authenticate within a bounded time."""
    from immich_memories.api.compatibility import UnsupportedImmichVersion
    from immich_memories.api.immich import ImmichAPIError, ImmichAuthError, ImmichClient

    try:
        async with ImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
            timeout=2.0,
        ) as client:

            async def resolve_and_authenticate() -> str:
                resolved = await client.get_api_version()
                await client.get_current_user()
                return resolved.value

            resolved_api_version = await asyncio.wait_for(resolve_and_authenticate(), timeout=5.0)
        return _ImmichDependency(
            status="ready",
            reachable=True,
            resolved_api_version=resolved_api_version,
        )
    except UnsupportedImmichVersion:
        return _ImmichDependency(status="unsupported_version", reachable=True)
    except ImmichAuthError:
        return _ImmichDependency(status="authentication_failed", reachable=True)
    except (ImmichAPIError, TimeoutError, httpx.HTTPError, OSError, ValueError):
        return _ImmichDependency(status="unreachable", reachable=False)


def _get_last_successful_run(config) -> str | None:
    """Return ISO timestamp of last completed run, or None."""
    from immich_memories.tracking.run_database import RunDatabase

    db = RunDatabase(db_path=config.cache.database_path)
    runs = db.list_runs(limit=1, status="completed")
    if runs and runs[0].completed_at:
        return runs[0].completed_at.isoformat()
    return None


def _get_automation_status(config) -> dict[str, Any]:
    """Return the durable, read-only smart automation status contract."""
    from immich_memories.automation.runner import AutoRunner

    return AutoRunner(config).status().to_dict()


def _automation_field(automation: dict[str, Any] | None, name: str) -> Any:
    """Project one optional automation field without branching in readiness logic."""
    return automation.get(name) if automation is not None else None


def _sanitize_health_text(value: object, secrets_to_redact: tuple[str, ...]) -> str:
    """Apply structural and config-aware sanitization before exposing health text."""
    safe = sanitize_error_message(str(value))
    for secret in secrets_to_redact:
        safe = safe.replace(secret, "***")
    return safe


def _redact_health_value(value: Any, secrets_to_redact: tuple[str, ...]) -> Any:
    """Recursively sanitize operational state with the shared text redactor."""
    if isinstance(value, str):
        return _sanitize_health_text(value, secrets_to_redact)
    if isinstance(value, dict):
        return {key: _redact_health_value(item, secrets_to_redact) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_health_value(item, secrets_to_redact) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_health_value(item, secrets_to_redact) for item in value)
    return value


def _health_detail_allowed(config: Config) -> bool:
    """Return whether the current request may see automation/run detail."""
    if not is_auth_enabled(config.auth):
        return True
    try:
        return bool(app.storage.user.get("authenticated"))
    except RuntimeError:
        # No request/session context (e.g. called outside an HTTP request).
        return False


def _operational_detail(
    config: Config, secrets_to_redact: tuple[str, ...]
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (automation status, last successful run) for the health payload.

    WHY: /health is public so probes work, but automation detail carries person
    names (memory keys) and host paths. With auth on, only a logged-in session
    gets the detail; probes and other LAN devices get status + version.
    """
    if not _health_detail_allowed(config):
        return None, None

    automation: dict[str, Any] | None = None
    try:
        automation = _get_automation_status(config)
    except Exception as exc:
        logger.warning(
            "Could not read automation status for readiness (%s)",
            type(exc).__name__,
        )

    last_successful_run: str | None = None
    try:
        last_successful_run = _get_last_successful_run(config)
    except Exception as exc:
        logger.warning(
            "Could not read run status for readiness (%s)",
            type(exc).__name__,
        )

    if automation is not None:
        automation = _redact_health_value(automation, secrets_to_redact)
    return automation, last_successful_run


# /health and /health/ready are unauthenticated so probes work without
# credentials, and each build costs a network round trip to Immich plus ~12
# SQLite connections. Serving repeats from a short-lived snapshot keeps a flood
# of requests from turning into a flood of work, while staying well inside the
# 15s readiness period so a scheduled probe still reports current truth.
_HEALTH_SNAPSHOT_TTL_SECONDS = 10.0
_health_snapshot_cache: tuple[float, dict[str, Any]] | None = None


async def _build_health_snapshot() -> dict[str, Any]:
    """Build the shared detailed health payload, reusing a recent one if fresh."""
    global _health_snapshot_cache

    cached = _health_snapshot_cache
    if cached is not None and time.monotonic() - cached[0] < _HEALTH_SNAPSHOT_TTL_SECONDS:
        return cached[1]

    snapshot = await _compute_health_snapshot()
    _health_snapshot_cache = (time.monotonic(), snapshot)
    return snapshot


async def _compute_health_snapshot() -> dict[str, Any]:
    """Build the shared detailed health payload for readiness and compatibility."""
    try:
        config = get_config()
    except Exception:
        return {
            "status": "degraded",
            "configuration": "unavailable",
            "immich_reachable": False,
            "immich": {
                "status": "missing_configuration",
                "reachable": False,
                "api_version_policy": None,
                "resolved_api_version": None,
            },
            "automation": None,
            "last_automation_attempt": None,
            "last_successful_auto_run": None,
            "pending_delivery_count": None,
            "oldest_pending_delivery": None,
            "notification_health": None,
            "last_successful_run": None,
            "in_process_scheduler": None,
            "version": __version__,
        }

    secrets_to_redact = configured_secret_values(config)
    configured = bool(config.immich.url and config.immich.api_key)
    if configured:
        try:
            immich = await _check_immich_dependency(config)
        except Exception:
            immich = _ImmichDependency(status="unreachable", reachable=False)
    else:
        immich = _ImmichDependency(status="missing_configuration", reachable=False)

    # Synchronous SQLite: on the event loop each open waits on the write lock
    # while a pipeline run holds it, which stalls every other session.
    automation, last_successful_run = await asyncio.to_thread(
        _operational_detail, config, secrets_to_redact
    )

    ready = configured and immich.status == "ready"
    api_version_policy = getattr(config.immich.api_version, "value", config.immich.api_version)
    return {
        "status": "ready" if ready else "degraded",
        "configuration": "configured" if configured else "missing",
        "immich_reachable": immich.reachable,
        "immich": {
            "status": immich.status,
            "reachable": immich.reachable,
            "api_version_policy": api_version_policy,
            "resolved_api_version": immich.resolved_api_version,
        },
        "automation": automation,
        "last_automation_attempt": automation.get("last_attempt") if automation else None,
        "last_successful_auto_run": (
            automation.get("last_completed_auto_run") if automation else None
        ),
        "pending_delivery_count": (
            automation.get("pending_delivery_count") if automation else None
        ),
        "oldest_pending_delivery": (
            automation.get("oldest_pending_delivery") if automation else None
        ),
        "notification_health": _automation_field(automation, "notification_health"),
        "last_successful_run": last_successful_run,
        "in_process_scheduler": (
            automation_scheduler.snapshot().to_dict() if _health_detail_allowed(config) else None
        ),
        "version": __version__,
    }


async def _liveness_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Report only that the web process is alive."""
    return JSONResponse({"status": "alive", "version": __version__})


async def _readiness_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Report whether configuration and Immich can support useful work."""
    snapshot = await _build_health_snapshot()
    return JSONResponse(snapshot, status_code=200 if snapshot["status"] == "ready" else 503)


async def _health_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Keep the detailed compatibility payload without readiness status codes."""
    snapshot = await _build_health_snapshot()
    if snapshot["status"] == "ready":
        snapshot["status"] = "ok"
    return JSONResponse(snapshot)


app.add_api_route("/health", _health_handler, methods=["GET"])
app.add_api_route("/health/live", _liveness_handler, methods=["GET"])
app.add_api_route("/health/ready", _readiness_handler, methods=["GET"])


# ============================================================================
# Auth: Middleware + Routes
# ============================================================================


def _check_login_rate_limit(request: Request) -> JSONResponse | None:
    """Return 429 if the client is rate-limited.

    Deliberately writes nothing: NiceGUI persists any non-empty `storage.user`
    to a file keyed by a cookie the caller may not send, and expires only tab
    storage. The login page reads the same IP off `client.ip` instead.
    """
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(client_ip):
        return JSONResponse({"detail": "Too many failed login attempts"}, status_code=429)
    return None


def _try_header_auth(request: Request, auth_config: AuthConfig) -> None:
    """Auto-create session from trusted proxy header."""
    client_ip = request.client.host if request.client else ""
    if not is_trusted_proxy(client_ip, auth_config.trusted_proxies):
        return
    user = request.headers.get(auth_config.user_header, "")
    if user and not app.storage.user.get("authenticated"):
        email = request.headers.get(auth_config.email_header, "")
        set_session(app.storage.user, username=user, provider="header", email=email)


def _check_session_ttl(ttl_hours: int) -> RedirectResponse | None:
    """Return redirect if session has expired."""
    from datetime import UTC, datetime, timedelta

    authenticated_at_str = app.storage.user.get("authenticated_at")
    if not authenticated_at_str:
        return None
    authenticated_at = datetime.fromisoformat(authenticated_at_str)
    if datetime.now(UTC) > authenticated_at + timedelta(hours=ttl_hours):
        clear_session(app.storage.user)
        return RedirectResponse("/login", status_code=307)
    return None


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Provider-agnostic auth check using NiceGUI's app.storage.user.

    WHY @app.middleware('http') not BaseHTTPMiddleware:
    BaseHTTPMiddleware breaks NiceGUI websockets. NiceGUI's middleware
    decorator only runs on HTTP requests and has access to app.storage.user.
    """
    if is_health_probe_path(request.url.path):
        return await call_next(request)

    config = get_config()
    if not is_auth_enabled(config.auth):
        return await call_next(request)

    if is_bypass_path(request.url.path):
        if request.url.path == "/login":
            blocked = _check_login_rate_limit(request)
            if blocked:
                return blocked
        return await call_next(request)

    if config.auth.provider == "header":
        _try_header_auth(request, config.auth)

    if not app.storage.user.get("authenticated"):
        return RedirectResponse("/login", status_code=307)

    expired = _check_session_ttl(config.auth.session_ttl_hours)
    if expired:
        return expired

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
    from immich_memories.ui.auth_oidc import create_oidc_client, oidc_redirect_uri

    oauth = create_oidc_client(config.auth)
    redirect_uri = oidc_redirect_uri(str(request.url_for("_oidc_callback")), config.auth.public_url)
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


_NOT_AUTHORISED_PAGE = """<!doctype html>
<meta charset="utf-8"><title>Not authorised</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee;
         display: grid; place-items: center; height: 100vh; margin: 0; }}
  div {{ max-width: 32rem; padding: 2rem; text-align: center; }}
  code {{ background: #222; padding: .15em .4em; border-radius: .25em; }}
</style>
<div>
  <h1>Not authorised</h1>
  <p>You signed in as <code>{who}</code>, but that account is not on this
     server's allow-list.</p>
  <p>Ask the administrator to add you to <code>auth.allowed_emails</code> or
     <code>auth.allowed_domains</code>.</p>
</div>
"""


async def _oidc_callback(request: Request) -> Response:
    """Handle OIDC callback — exchange code for tokens and create session."""
    config = get_config()
    from immich_memories.ui.auth_oidc import (
        create_oidc_client,
        extract_user_from_token,
        is_user_allowed,
        validate_callback_origin,
    )

    if not validate_callback_origin(request, config.auth.public_url):
        logger.warning("OIDC callback origin mismatch: %s", request.url)
        return JSONResponse({"detail": "Invalid callback origin"}, status_code=400)

    oauth = create_oidc_client(config.auth)
    # WHY: authlib uses request.session for OIDC state/PKCE internally.
    # Our auth session goes in app.storage.user (NiceGUI's store).
    token = await oauth.oidc.authorize_access_token(request)
    username, email = extract_user_from_token(token)
    if not is_user_allowed(email, config.auth):
        # WHY a page and not a redirect to /login: the IdP session is still
        # valid, so /login would send them straight back here and loop.
        logger.warning("OIDC login refused for %s: not in the allow-list", email or username)
        return HTMLResponse(_NOT_AUTHORISED_PAGE.format(who=html.escape(email or username)), 403)
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


def _expire_session_storage() -> None:
    """Drop the session files NiceGUI persists and never expires."""
    from nicegui.storage import Storage

    from immich_memories.ui.session_storage import sweep_expired_user_storage

    sweep_expired_user_storage(Storage.path, ttl_hours=get_config().auth.session_ttl_hours)


async def _session_cleanup_loop() -> None:
    """Periodically clean up stale sessions."""
    from immich_memories.ui.state import cleanup_stale_sessions

    while True:
        await asyncio.sleep(900)  # 15 minutes
        cleanup_stale_sessions()
        _expire_session_storage()


def _start_cleanup_task() -> None:
    asyncio.ensure_future(_session_cleanup_loop())


app.on_startup(_expire_session_storage)
app.on_startup(_start_cleanup_task)
app.on_startup(lambda: asyncio.ensure_future(automation_scheduler.run_forever()))


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
    from immich_memories.logging_config import configure_logging

    configure_logging()
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
    kwargs.update(reverse_proxy_run_kwargs(get_config(), os.environ))
    if reload:
        kwargs["uvicorn_reload_includes"] = "*.py"
        kwargs["uvicorn_reload_excludes"] = ".*, *.log, *.db, *.db-journal"
    ui.run(**kwargs)


if __name__ in {"__main__", "__mp_main__"}:
    main()
