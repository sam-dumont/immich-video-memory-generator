"""Main NiceGUI application."""

from __future__ import annotations

import logging
import os
import signal
import sys

# Configure logging to show in console before importing our modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

from nicegui import app, ui

# ---------------------------------------------------------------------------
# Ctrl+C handling: ThreadPoolExecutor threads are non-daemon by default,
# which prevents clean shutdown. First Ctrl+C tries graceful exit; second
# forces it via os._exit().
# ---------------------------------------------------------------------------
_sigint_count = 0


def _sigint_handler(_signum: int, frame: object) -> None:
    global _sigint_count
    _sigint_count += 1
    if _sigint_count == 1:
        print("\nShutting down (press Ctrl+C again to force)...")
        # Try graceful shutdown via SystemExit
        raise SystemExit(0)
    else:
        print("\nForce shutdown.")
        os._exit(1)


signal.signal(signal.SIGINT, _sigint_handler)

from immich_memories.config import get_config, init_config_dir
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


# ============================================================================
# Helper Functions
# ============================================================================


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
# App Startup
# ============================================================================


def initialize_app() -> None:
    """Initialize the application on startup."""
    # Initialize config directory
    init_config_dir()

    # Load existing config into state
    state = get_app_state()
    config = get_config(reload=True)
    state.immich_url = config.immich.url
    state.immich_api_key = config.immich.api_key

    logger.info("Application initialized")


# Initialize on startup
app.on_startup(initialize_app)


def main(port: int = 8080, host: str = "127.0.0.1", reload: bool = True) -> None:
    """Run the NiceGUI application."""
    ui.run(
        title="Immich Memories",
        favicon="🎬",
        port=port,
        host=host,
        reload=reload,
        uvicorn_reload_includes="*.py",
        uvicorn_reload_excludes=".*, *.log, *.db, *.db-journal",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
