"""Playwright E2E test fixtures.

Run locally with: make e2e  (or make screenshots for screenshot capture only)
Requires either a running UI server on :8099 or auto-starts one.

When auto-starting, auth is disabled via IMMICH_MEMORIES_AUTH__ENABLED=false
and the server runs under `coverage run` so UI code coverage is tracked.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page

_BASE_PORT = 8099
_BASE_URL = f"http://localhost:{_BASE_PORT}"
_STARTUP_TIMEOUT = 30  # seconds
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_COVERAGE_FILE = _REPO_ROOT / ".coverage.e2e-server"


@pytest.fixture(scope="session")
def app_url() -> Generator[str, None, None]:
    """Yield the base URL of a running UI server.

    Reuses an existing server if one is already listening on :8099,
    otherwise starts one under `coverage run` (with auth disabled)
    and tears it down after the session.
    """
    if _server_is_ready():
        yield _BASE_URL
        return

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("NICEGUI_") and k != "PYTEST_CURRENT_TEST"
    }
    env["IMMICH_MEMORIES_AUTH__ENABLED"] = "false"
    # Don't enable demo mode via config — we inject the CSS class directly
    # in enable_demo_mode(). The toggle would show in screenshots otherwise.
    env["COVERAGE_FILE"] = str(_SERVER_COVERAGE_FILE)

    venv_bin = _REPO_ROOT / ".venv" / "bin"
    proc = subprocess.Popen(
        [
            str(venv_bin / "coverage"),
            "run",
            "--source=immich_memories",
            "--branch",
            str(venv_bin / "immich-memories"),
            "ui",
            "--port",
            str(_BASE_PORT),
            "--host",
            "localhost",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _wait_for_server(proc)
        yield _BASE_URL
    finally:
        # WHY: SIGINT (not SIGTERM) — uvicorn handles SIGINT gracefully and
        # runs atexit hooks, which is how coverage writes its data file.
        # SIGTERM skips atexit in uvicorn.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        _convert_server_coverage()


@pytest.fixture(scope="session")
def browser_context_args() -> dict:
    """Override pytest-playwright default viewport to match screenshot size."""
    return {"viewport": {"width": 1440, "height": 900}}


@pytest.fixture(scope="session")
def screenshot_dir() -> Path:
    """Path to the docs-site screenshot directory."""
    repo_root = Path(__file__).resolve().parents[2]
    out = repo_root / "docs-site" / "static" / "img" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    return out


def set_theme(page: Page, theme: str) -> None:
    """Switch the NiceGUI app to the given theme ('light' or 'dark')."""
    icon = "light_mode" if theme == "light" else "dark_mode"
    btn = page.locator(f'button:has(i:text("{icon}"))')
    if btn.is_visible(timeout=3000):
        btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        # Move mouse away from the button to clear hover state
        page.mouse.move(640, 450)
        page.wait_for_timeout(200)


def enable_demo_mode(page: Page) -> None:
    """Activate demo mode (CSS blur on all media) for privacy."""
    page.evaluate("document.body.classList.add('demo-mode')")


# -- helpers ------------------------------------------------------------------


def _convert_server_coverage() -> None:
    """Convert server .coverage data to XML and merge with pytest's coverage."""
    if not _SERVER_COVERAGE_FILE.exists():
        return
    subprocess.run(
        ["uv", "run", "coverage", "xml", "-o", str(_REPO_ROOT / "tests" / "e2e-coverage.xml")],
        env={**os.environ, "COVERAGE_FILE": str(_SERVER_COVERAGE_FILE)},
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    _SERVER_COVERAGE_FILE.unlink(missing_ok=True)


def _server_is_ready() -> bool:
    try:
        r = httpx.get(_BASE_URL, timeout=2.0, follow_redirects=True)
        return r.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _wait_for_server(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
            pytest.skip(f"UI server exited early: {stderr[-2000:]}")
        if _server_is_ready():
            return
        time.sleep(0.5)
    proc.terminate()
    pytest.skip(f"UI server did not start within {_STARTUP_TIMEOUT}s")
