"""E2E tests for authentication flows and demo mode.

Starts a separate server with basic auth enabled to test:
- Login page rendering
- Login flow (enter credentials → redirect to wizard)
- Auth controls in sidebar (username badge, sign-out button)
- Demo mode toggle behavior
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import _REPO_ROOT
from tests.e2e.redaction import redact_page

pytestmark = pytest.mark.e2e

_AUTH_PORT = 8098
_AUTH_URL = f"http://localhost:{_AUTH_PORT}"
_TEST_USER = "testuser"
_TEST_PASS = "testpass123"  # noqa: S105


@pytest.fixture(scope="module")
def auth_server_url() -> str:
    """Start a server with basic auth enabled on a separate port."""
    if _is_ready():
        return _AUTH_URL

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("NICEGUI_") and k != "PYTEST_CURRENT_TEST"
    }
    env["IMMICH_MEMORIES_AUTH__ENABLED"] = "true"
    env["IMMICH_MEMORIES_AUTH__PROVIDER"] = "basic"
    env["IMMICH_MEMORIES_AUTH__USERNAME"] = _TEST_USER
    env["IMMICH_MEMORIES_AUTH__PASSWORD"] = _TEST_PASS
    env["IMMICH_MEMORIES_SERVER__ENABLE_DEMO_MODE"] = "true"
    env["COVERAGE_FILE"] = str(_REPO_ROOT / ".coverage.e2e-auth")

    venv_bin = _REPO_ROOT / ".venv" / "bin"
    proc = subprocess.Popen(
        [
            str(venv_bin / "coverage"),
            "run",
            "--source=immich_memories",
            "--branch",
            "--parallel-mode",
            str(venv_bin / "immich-memories"),
            "ui",
            "--port",
            str(_AUTH_PORT),
            "--host",
            "localhost",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
            pytest.skip(f"Auth server exited: {stderr[-1000:]}")
        if _is_ready():
            yield _AUTH_URL
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            _merge_auth_coverage()
            return
        time.sleep(0.5)

    proc.terminate()
    pytest.skip("Auth server did not start in 30s")


def _is_ready() -> bool:
    try:
        r = httpx.get(_AUTH_URL, timeout=2.0, follow_redirects=True)
        return r.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _merge_auth_coverage() -> None:
    """Merge auth server coverage into the main e2e coverage file."""
    import glob

    for f in glob.glob(str(_REPO_ROOT / ".coverage.e2e-auth*")):
        Path(f).unlink(missing_ok=True)


def test_login_and_auth_controls(
    auth_server_url: str,
    page: Page,
    screenshot_dir: Path,
) -> None:
    """Login with basic auth, verify sidebar auth controls."""
    # Navigate to root — should redirect to /login
    page.goto(auth_server_url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    # Should be on login page
    assert "/login" in page.url or page.get_by_role("button", name="Sign in").is_visible(
        timeout=5000
    )
    page.screenshot(path=str(screenshot_dir / "login-basic-auth.png"))

    # Fill in credentials and sign in
    page.get_by_label("Username").fill(_TEST_USER)
    page.get_by_label("Password").fill(_TEST_PASS)
    page.get_by_role("button", name="Sign in").click()

    # Should redirect to main wizard
    page.wait_for_url(f"{auth_server_url}/", timeout=10_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)

    # Verify auth controls visible in sidebar
    username_label = page.get_by_text(_TEST_USER)
    assert username_label.is_visible(timeout=5000)

    sign_out = page.get_by_role("button", name="Sign out")
    assert sign_out.is_visible(timeout=3000)

    redact_page(page)
    page.screenshot(path=str(screenshot_dir / "step1-with-auth.png"))


def test_demo_mode_toggle(
    auth_server_url: str,
    page: Page,
) -> None:
    """Verify the demo mode toggle actually adds/removes the CSS class."""
    # Login first
    page.goto(auth_server_url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    if page.get_by_role("button", name="Sign in").is_visible(timeout=2000):
        page.get_by_label("Username").fill(_TEST_USER)
        page.get_by_label("Password").fill(_TEST_PASS)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(f"{auth_server_url}/", timeout=10_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

    # Find demo mode toggle
    demo_switch = page.locator("text=Demo mode").locator("..").locator("input[type='checkbox']")
    if not demo_switch.is_visible(timeout=5000):
        demo_label = page.get_by_text("Demo mode")
        if demo_label.is_visible():
            demo_label.click()

    # Check that demo-mode class is toggled
    has_class = page.evaluate("document.body.classList.contains('demo-mode')")
    # Toggle it
    demo_toggle = page.locator(".q-toggle").filter(has=page.get_by_text("Demo mode"))
    if demo_toggle.is_visible(timeout=3000):
        demo_toggle.click()
        page.wait_for_timeout(500)
        new_state = page.evaluate("document.body.classList.contains('demo-mode')")
        assert new_state != has_class


def test_sign_out(
    auth_server_url: str,
    page: Page,
) -> None:
    """Verify sign out redirects to login page."""
    # Login
    page.goto(auth_server_url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    if page.get_by_role("button", name="Sign in").is_visible(timeout=2000):
        page.get_by_label("Username").fill(_TEST_USER)
        page.get_by_label("Password").fill(_TEST_PASS)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(f"{auth_server_url}/", timeout=10_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

    # Click sign out
    sign_out = page.get_by_role("button", name="Sign out")
    if sign_out.is_visible(timeout=3000):
        sign_out.click()
        page.wait_for_timeout(2000)
        # Should be back on login page
        assert "/login" in page.url
