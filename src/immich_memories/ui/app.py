"""Main NiceGUI application with Immich-inspired theme."""

from __future__ import annotations

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
from starlette.responses import JSONResponse

from immich_memories import __version__
from immich_memories.config import get_config, init_config_dir
from immich_memories.ui.state import get_app_state
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


def render_step_indicator(current_step: int) -> None:  # noqa: ARG001
    """Step indicator — removed in favor of sidebar navigation.

    The sidebar already highlights the active step. Keeping this function
    as a no-op so callers don't need to change.
    """


def render_sidebar(current_step: int) -> None:
    """Render Immich-style sidebar navigation."""
    state = get_app_state()

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
            _render_demo_toggle(state)
            render_theme_toggle()


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
# App Startup / Shutdown
# ============================================================================


def initialize_app() -> None:
    """Initialize the application on startup."""
    init_config_dir()
    state = get_app_state()
    config = get_config(reload=True)
    state.config = config
    state.immich_url = config.immich.url
    state.immich_api_key = config.immich.api_key
    state.include_live_photos = config.analysis.include_live_photos
    logger.info("Application initialized")


app.on_startup(initialize_app)


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
