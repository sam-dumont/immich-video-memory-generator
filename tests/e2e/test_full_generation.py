"""E2E test: full video generation pipeline via the UI.

Runs the complete flow: select preset → analyze (no cache, fresh LLM
scoring) → generate video with real music → capture progress states.

This is an EXPENSIVE test (~5-10 min). Run separately:
    make e2e-full

Uses a fresh temp cache directory so the LLM analysis pipeline runs
from scratch (no cached scores).
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import _REPO_ROOT, enable_demo_mode, set_theme
from tests.e2e.redaction import redact_page

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_GEN_PORT = 8096
_GEN_URL = f"http://localhost:{_GEN_PORT}"


@pytest.fixture(scope="module")
def gen_server_url():
    """Start a server with a fresh cache (no analysis data)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("NICEGUI_") and k != "PYTEST_CURRENT_TEST"
        }
        env["IMMICH_MEMORIES_AUTH__ENABLED"] = "false"
        env["IMMICH_MEMORIES_CACHE__DATABASE"] = f"{tmpdir}/fresh-cache.db"
        env["IMMICH_MEMORIES_CACHE__DIRECTORY"] = f"{tmpdir}/cache"
        env["IMMICH_MEMORIES_DEFAULTS__TARGET_DURATION"] = "1"
        env["IMMICH_MEMORIES_OUTPUT__RESOLUTION"] = "720p"

        venv_bin = _REPO_ROOT / ".venv" / "bin"
        proc = subprocess.Popen(
            [
                str(venv_bin / "immich-memories"),
                "ui",
                "--port",
                str(_GEN_PORT),
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
                pytest.skip(f"Gen server exited: {stderr[-500:]}")
            try:
                r = httpx.get(_GEN_URL, timeout=2.0, follow_redirects=True)
                if r.status_code < 500:
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(0.5)
        else:
            proc.terminate()
            pytest.skip("Gen server did not start")

        yield _GEN_URL

        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _goto(page: Page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        page.wait_for_timeout(2000)


def _prep(page: Page) -> None:
    enable_demo_mode(page)
    redact_page(page)


def test_full_generation_pipeline(
    gen_server_url: str,
    page: Page,
    screenshot_dir: Path,
) -> None:
    """Run the full pipeline: preset → analyze → generate → complete."""
    d = screenshot_dir

    # ── Step 1: Select Monthly Highlights for a recent month ──
    _goto(page, gen_server_url)
    page.wait_for_timeout(3000)
    set_theme(page, "light")

    _goto(page, gen_server_url)
    page.wait_for_timeout(3000)

    monthly = page.get_by_text("Monthly Highlights")
    if not monthly.is_visible(timeout=5000):
        pytest.skip("Monthly Highlights preset not visible")
    monthly.scroll_into_view_if_needed()
    monthly.click()
    page.wait_for_timeout(1000)

    # Pick first available person if person combo appears
    person_combo = page.get_by_role("combobox", name="Person")
    if person_combo.is_visible(timeout=3000):
        person_combo.click()
        page.wait_for_timeout(500)
        options = page.get_by_role("option")
        if options.count() > 1:
            options.nth(1).click()
        page.wait_for_timeout(300)

    _prep(page)
    _goto(page, gen_server_url)  # Force page refresh to pick up selections
    page.wait_for_timeout(2000)

    # Click Next to start analysis
    next_btn = page.get_by_role("button", name="Next: Review Clips")
    if not next_btn.is_visible(timeout=5000):
        pytest.skip("Next button not visible — Immich not connected")
    next_btn.click()

    # ── Step 2: Wait for analysis pipeline ──
    # The loading dialog shows progress: "Loading videos...", "Fetching...", "Analyzing..."
    page.wait_for_timeout(2000)

    # Capture the loading dialog if visible
    dialog = page.locator('[role="dialog"]')
    if dialog.is_visible(timeout=5000):
        _prep(page)
        page.screenshot(path=str(d / "pipeline-loading.png"))

    # Wait for analysis to complete (up to 5 minutes)
    page.wait_for_function(
        "() => !document.querySelector('[role=\"dialog\"]')",
        timeout=300_000,
    )
    page.wait_for_timeout(3000)

    # Capture step2 with fresh analysis results (LLM badges visible)
    _prep(page)
    page.screenshot(path=str(d / "step2-fresh-analysis.png"))

    # ── Navigate to Step 3 via sidebar click ──
    # WHY: NiceGUI button clicks via JS don't trigger websocket events.
    # Use sidebar navigation which uses standard anchor/click behavior.
    step3_nav = page.locator("text=Options").first
    if step3_nav.is_visible(timeout=5000):
        step3_nav.click(force=True)
        page.wait_for_timeout(3000)
        _prep(page)
        page.screenshot(path=str(d / "step3-pre-generate.png"))

    # ── Navigate to Step 4 via sidebar ──
    step4_nav = page.locator("text=Export").first
    if step4_nav.is_visible(timeout=5000):
        step4_nav.click(force=True)
        page.wait_for_timeout(3000)

    # Click Generate
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(500)
    gen_btn = page.get_by_role("button", name="Generate Video")
    if not gen_btn.is_visible(timeout=10_000):
        page.screenshot(path=str(d / "debug-step4-no-generate.png"))
        pytest.skip("Generate Video button not visible")
    gen_btn.click(force=True)
    page.wait_for_timeout(3000)

    # Capture progress: check every 15s, keep one mid-progress shot
    captured_progress = False
    for _ in range(80):  # 80 × 15s = 20 min max
        success = page.get_by_text("Your memory video is ready!")
        if success.is_visible(timeout=1000):
            break

        if not captured_progress:
            progress_bar = page.locator(".q-linear-progress")
            if progress_bar.is_visible(timeout=1000):
                _prep(page)
                page.screenshot(path=str(d / "step4-generating.png"))
                captured_progress = True

        page.wait_for_timeout(15_000)

    # ── Capture completion state ──
    page.wait_for_timeout(2000)
    _prep(page)
    page.screenshot(path=str(d / "step4-complete.png"))

    page.evaluate("document.querySelector('.q-drawer')?.style.setProperty('display','none')")
    page.evaluate(
        "document.querySelector('.q-page-container')?.style.setProperty('padding-left','0')"
    )
    page.wait_for_timeout(200)
    page.screenshot(path=str(d / "hero-step4-complete.png"))
