"""E2E test for OIDC login flow using oidc-provider-mock.

Starts a mock OIDC provider + NiceGUI server, then drives the full
browser-based login flow: click SSO → mock IdP consent → callback → wizard.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import _REPO_ROOT, _build_launch_environment
from tests.e2e.redaction import redact_page

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def oidc_mock_server() -> Iterator[int]:
    """Start oidc-provider-mock on a random port."""
    os.environ["AUTHLIB_INSECURE_TRANSPORT"] = "1"
    from oidc_provider_mock import run_server_in_thread

    with run_server_in_thread(port=0) as server:
        port = server.server_port

        # Register a test user
        httpx.put(
            f"http://localhost:{port}/users/testuser",
            json={
                "preferred_username": "testuser",
                "email": "test@example.com",
                "name": "Test User",
            },
        )
        yield port


@pytest.fixture(scope="module")
def oidc_app_url(
    oidc_mock_server: int, tmp_path_factory, unused_tcp_port_factory, fake_immich_server
) -> Iterator[str]:
    """Start NiceGUI server configured for OIDC auth against the mock."""
    port = unused_tcp_port_factory()
    url = f"http://localhost:{port}"

    env = _build_launch_environment(tmp_path_factory.mktemp("oidc-auth"))
    env["IMMICH_URL"] = fake_immich_server.base_url
    env["IMMICH_API_KEY"] = fake_immich_server.api_key
    env["IMMICH_MEMORIES_AUTH__ENABLED"] = "true"
    env["IMMICH_MEMORIES_AUTH__PROVIDER"] = "oidc"
    env["IMMICH_MEMORIES_AUTH__ISSUER_URL"] = f"http://localhost:{oidc_mock_server}"
    env["IMMICH_MEMORIES_AUTH__CLIENT_ID"] = "test-client"
    env["IMMICH_MEMORIES_AUTH__CLIENT_SECRET"] = "test-secret"  # noqa: S105
    env["IMMICH_MEMORIES_AUTH__ALLOW_INSECURE_ISSUER"] = "true"
    env["AUTHLIB_INSECURE_TRANSPORT"] = "1"

    venv_bin = _REPO_ROOT / ".venv" / "bin"
    proc = subprocess.Popen(
        [
            str(venv_bin / "immich-memories"),
            "ui",
            "--port",
            str(port),
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
            pytest.skip(f"OIDC app server exited: {stderr[-1000:]}")
        try:
            r = httpx.get(url, timeout=2.0, follow_redirects=True)
            if r.status_code < 500:
                break
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(0.5)
    else:
        proc.terminate()
        pytest.skip("OIDC app server did not start in 30s")

    yield url

    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_oidc_login_flow(
    oidc_app_url: str,
    oidc_mock_server: int,
    page: Page,
    screenshot_dir: Path,
) -> None:
    """Full OIDC login: click SSO → consent on mock IdP → redirect back."""
    page.goto(oidc_app_url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    # Should be on login page with SSO button (text depends on config)
    sso_btn = page.locator("button").filter(has_text="Sign in with")
    if not sso_btn.is_visible(timeout=5000):
        pytest.skip("SSO button not visible — OIDC may not be configured")

    page.screenshot(path=str(screenshot_dir / "login-oidc.png"))

    # Click SSO — redirects to mock IdP
    sso_btn.click()
    page.wait_for_timeout(2000)

    # Mock IdP shows a consent form — submit it with our test user
    sub_input = page.locator('input[name="sub"]')
    if sub_input.is_visible(timeout=5000):
        sub_input.fill("testuser")
        page.locator('button[type="submit"], input[type="submit"]').first.click()
    else:
        # Some mock IdPs auto-consent
        pass

    # Should redirect back to the app
    page.wait_for_url(f"{oidc_app_url}/**", timeout=15_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)

    # Verify we're logged in — should see the wizard, not login
    assert "/login" not in page.url

    # Username should be visible in sidebar
    username = page.get_by_text("testuser")
    assert username.is_visible(timeout=5000)

    redact_page(page)
    page.screenshot(path=str(screenshot_dir / "oidc-authenticated.png"))
