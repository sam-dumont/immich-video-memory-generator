"""Main NiceGUI application with Immich-inspired theme."""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys

# Configure logging before importing our modules
from immich_memories.logging_config import configure_logging

configure_logging()

from nicegui import app, ui

from immich_memories.config import get_config, init_config_dir
from immich_memories.ui.state import get_app_state
from immich_memories.ui.theme import apply_theme, render_theme_toggle

logger = logging.getLogger(__name__)

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

        # Spacer + theme toggle at bottom
        ui.element("div").classes("flex-grow")
        with ui.column().classes("px-5 pb-4 mt-auto"):
            ui.element("div").classes("mb-3").style(
                "height: 1px; background: var(--im-border-light)"
            )
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
# App Startup / Shutdown
# ============================================================================


def initialize_app() -> None:
    """Initialize the application on startup."""
    init_config_dir()
    state = get_app_state()
    config = get_config(reload=True)
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


def _kill_port_holders(port: int) -> bool:
    """Find and kill processes holding the given port. Returns True if any were killed."""
    import subprocess

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if not pids:
            return False

        my_pid = str(os.getpid())
        pids_to_kill = [p for p in pids if p != my_pid]
        if not pids_to_kill:
            return False

        logger.warning(
            f"Killing {len(pids_to_kill)} zombie process(es) on port {port}: {pids_to_kill}"
        )
        for pid in pids_to_kill:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        # Give them a moment, then force-kill survivors
        import time

        time.sleep(0.5)
        for pid in pids_to_kill:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        time.sleep(0.3)
        return True
    except Exception:
        return False


def _is_port_free(host: str, port: int) -> bool:
    """Check if a port is available for binding (IPv4 and IPv6)."""
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
    # Kill zombie processes from previous runs before binding
    if not _is_port_free(host, port):
        logger.warning(f"Port {port} is in use — attempting to clean up zombie processes")
        if _kill_port_holders(port):
            if not _is_port_free(host, port):
                logger.error(
                    f"Port {port} still in use after cleanup. Use: lsof -ti :{port} | xargs kill -9"
                )
                sys.exit(1)
        else:
            logger.error(f"Port {port} is in use. Use: lsof -ti :{port} | xargs kill -9")
            sys.exit(1)

    kwargs: dict = {
        "title": "Immich Memories",
        "favicon": "🎬",
        "port": port,
        "host": host,
        "reload": reload,
        "storage_secret": "immich-memories-ui",
    }
    if reload:
        kwargs["uvicorn_reload_includes"] = "*.py"
        kwargs["uvicorn_reload_excludes"] = ".*, *.log, *.db, *.db-journal"
    ui.run(**kwargs)


if __name__ in {"__main__", "__mp_main__"}:
    main()
