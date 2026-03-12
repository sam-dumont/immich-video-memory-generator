"""Main NiceGUI application."""

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

logger = logging.getLogger(__name__)


# ============================================================================
# Shared UI Components
# ============================================================================


def render_step_indicator(current_step: int) -> None:
    """Render the step progress indicator at the top of the page."""
    step_names = [
        "Configuration",
        "Clip Review",
        "Generation Options",
        "Preview & Export",
    ]

    with ui.row().classes("w-full justify-center gap-4 mb-6"):
        for i, name in enumerate(step_names):
            step_num = i + 1
            if step_num < current_step:
                # Completed step
                with ui.row().classes("items-center gap-1"):
                    ui.icon("check_circle", color="green").classes("text-xl")
                    ui.label(name).classes("text-green-600 font-medium")
            elif step_num == current_step:
                # Current step
                with ui.row().classes("items-center gap-1"):
                    ui.icon("radio_button_checked", color="blue").classes("text-xl")
                    ui.label(name).classes("text-blue-600 font-bold")
            else:
                # Future step
                with ui.row().classes("items-center gap-1"):
                    ui.icon("radio_button_unchecked", color="gray").classes("text-xl")
                    ui.label(name).classes("text-gray-400")

            # Add separator except for last step
            if i < len(step_names) - 1:
                ui.label("—").classes("text-gray-300 mx-2")


def render_sidebar() -> None:
    """Render the sidebar navigation."""
    state = get_app_state()

    with ui.left_drawer().classes("bg-gray-100 p-4"):
        ui.label("Immich Memories").classes("text-xl font-bold mb-4")

        ui.separator()

        # Navigation links
        with ui.column().classes("gap-2 mt-4"):

            def nav_to(step: int, path: str) -> None:
                state.step = step
                ui.navigate.to(path)

            ui.button(
                "1. Configuration",
                on_click=lambda: nav_to(1, "/"),
                icon="settings",
            ).props("flat").classes("w-full justify-start")

            ui.button(
                "2. Clip Review",
                on_click=lambda: nav_to(2, "/step2"),
                icon="video_library",
            ).props("flat").classes("w-full justify-start")

            ui.button(
                "3. Options",
                on_click=lambda: nav_to(3, "/step3"),
                icon="tune",
            ).props("flat").classes("w-full justify-start")

            ui.button(
                "4. Export",
                on_click=lambda: nav_to(4, "/step4"),
                icon="download",
            ).props("flat").classes("w-full justify-start")


def page_header(title: str, step: int) -> None:
    """Render a consistent page header with step indicator."""
    ui.page_title(f"Immich Memories - {title}")
    render_step_indicator(step)
    ui.separator()
    ui.label(title).classes("text-2xl font-bold mt-4 mb-4")


# ============================================================================
# Page Routes
# ============================================================================


@ui.page("/")
def index_page() -> None:
    """Step 1: Configuration page."""
    from immich_memories.ui.pages.step1_config import render_step1

    render_sidebar()
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Configuration", 1)
        render_step1()


@ui.page("/step2")
def step2_page() -> None:
    """Step 2: Clip Review page."""
    from immich_memories.ui.pages.step2_review import render_step2

    render_sidebar()
    with ui.column().classes("w-full max-w-6xl mx-auto p-6"):
        page_header("Clip Review", 2)
        render_step2()


@ui.page("/step3")
def step3_page() -> None:
    """Step 3: Generation Options page."""
    from immich_memories.ui.pages.step3_options import render_step3

    render_sidebar()
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Generation Options", 3)
        render_step3()


@ui.page("/step4")
def step4_page() -> None:
    """Step 4: Preview & Export page."""
    from immich_memories.ui.pages.step4_export import render_step4

    render_sidebar()
    with ui.column().classes("w-full max-w-4xl mx-auto p-6"):
        page_header("Preview & Export", 4)
        render_step4()


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
    """Check if a port is available for binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    }
    if reload:
        kwargs["uvicorn_reload_includes"] = "*.py"
        kwargs["uvicorn_reload_excludes"] = ".*, *.log, *.db, *.db-journal"
    ui.run(**kwargs)


if __name__ in {"__main__", "__mp_main__"}:
    main()
