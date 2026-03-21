"""Playwright E2E test fixtures.

Run locally with: make e2e  (or make screenshots for screenshot capture only)
Requires either a running UI server on :8099 or auto-starts one.
"""

from __future__ import annotations

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


@pytest.fixture(scope="session")
def app_url() -> Generator[str, None, None]:
    """Yield the base URL of a running UI server.

    Reuses an existing server if one is already listening on :8099,
    otherwise starts one as a subprocess and tears it down after the session.
    """
    if _server_is_ready():
        yield _BASE_URL
        return

    proc = subprocess.Popen(
        ["uv", "run", "immich-memories", "ui", "--port", str(_BASE_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_server(proc)
        yield _BASE_URL
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser_context_args() -> dict:
    """Override pytest-playwright default viewport to match screenshot size."""
    return {"viewport": {"width": 1280, "height": 900}}


@pytest.fixture(scope="session")
def screenshot_dir() -> Path:
    """Path to the docs-site screenshot directory."""
    repo_root = Path(__file__).resolve().parents[2]
    out = repo_root / "docs-site" / "static" / "img" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    return out


def set_theme(page: Page, theme: str) -> None:
    """Switch the NiceGUI app to the given theme ('light' or 'dark').

    Clicks the theme toggle button in the sidebar and waits for the page
    reload that NiceGUI triggers on theme change.
    """
    icon = "light_mode" if theme == "light" else "dark_mode"
    btn = page.locator(f'button:has(i:text("{icon}"))')
    if btn.is_visible(timeout=3000):
        btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)


# -- helpers ------------------------------------------------------------------


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
            pytest.skip(f"UI server exited early: {stderr[:500]}")
        if _server_is_ready():
            return
        time.sleep(0.5)
    proc.terminate()
    pytest.skip(f"UI server did not start within {_STARTUP_TIMEOUT}s")
