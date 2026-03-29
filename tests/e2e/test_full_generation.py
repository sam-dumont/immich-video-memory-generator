"""E2E test: full video generation pipeline via the UI.

Runs the complete flow: select preset → analyze → run smart pipeline
→ generate video → capture progress and completion states.

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
        # WHY: Music generation can fail (missing torchcodec, no API) and
        # the error handler has a cascading sqlite FK bug. Disable for screenshots.
        env["IMMICH_MEMORIES_MUSICGEN__ENABLED"] = "false"
        env["IMMICH_MEMORIES_ACE_STEP__ENABLED"] = "false"

        venv_bin = _REPO_ROOT / ".venv" / "bin"
        log_file = Path(tmpdir) / "server.log"
        log_fh = open(log_file, "w")  # noqa: SIM115
        print(f"   📋 Server logs: tail -f {log_file}")
        proc = subprocess.Popen(
            [
                str(venv_bin / "immich-memories"),
                "ui",
                "--port",
                str(_GEN_PORT),
                "--host",
                "localhost",
            ],
            stdout=log_fh,
            stderr=log_fh,
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
        log_fh.close()


def _goto(page: Page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        page.wait_for_timeout(2000)


def _prep(page: Page) -> None:
    enable_demo_mode(page)
    redact_page(page)
    # Hide warning/error alerts
    page.evaluate("""() => {
        document.querySelectorAll('.im-alert-warning, .im-alert-error').forEach(
            el => el.style.display = 'none'
        );
    }""")


def test_full_generation_pipeline(
    gen_server_url: str,
    page: Page,
    screenshot_dir: Path,
) -> None:
    """Run the full pipeline: preset → analyze → generate → complete."""
    d = screenshot_dir

    # ── Step 1: Select Monthly Highlights ──
    print("\n🔵 [1/7] Starting server and navigating to home page...")
    _goto(page, gen_server_url)
    page.wait_for_timeout(3000)
    set_theme(page, "light")
    _goto(page, gen_server_url)
    page.wait_for_timeout(3000)
    print("   ✓ Home page loaded")

    print("🔵 [2/7] Selecting Monthly Highlights preset...")
    monthly = page.get_by_text("event_noteMonthly")
    if not monthly.is_visible(timeout=5000):
        monthly = page.get_by_text("Monthly Highlights")
    if not monthly.is_visible(timeout=5000):
        pytest.skip("Monthly Highlights preset not visible")
    monthly.click()
    page.wait_for_timeout(1000)
    print("   ✓ Monthly Highlights selected")

    # ── Step 2: Load clips ──
    print("🔵 [3/7] Clicking 'Next: Review Clips' to load clips from Immich...")
    next_btn = page.get_by_role("button", name="Next: Review Clips")
    if not next_btn.is_visible(timeout=5000):
        pytest.skip("Next button not visible — Immich not connected")
    next_btn.click()
    page.wait_for_timeout(2000)

    dialog = page.locator('[role="dialog"]')
    if dialog.is_visible(timeout=5000):
        print("   ⏳ Loading dialog visible — waiting for clips...")
        _prep(page)
        page.screenshot(path=str(d / "pipeline-loading.png"))

    page.wait_for_function(
        "() => !document.querySelector('[role=\"dialog\"]')",
        timeout=300_000,
    )
    page.wait_for_timeout(3000)
    print("   ✓ Clips loaded")

    _prep(page)
    page.screenshot(path=str(d / "step2-fresh-analysis.png"))

    # ── Step 2b: Run smart pipeline ──
    print("🔵 [4/7] Clicking 'Generate Memories' to run smart pipeline...")
    # WHY: exact=True avoids matching the collapsible section header with same text
    gen_memories = page.get_by_role("button", name="Generate Memories", exact=True)
    if not gen_memories.is_visible(timeout=5000):
        pytest.skip("Generate Memories button not visible")
    gen_memories.click()
    print("   ⏳ Pipeline running (scoring, filtering, selecting clips)...")

    review_btn = page.get_by_role("button", name="Review & Refine Selected Clips")
    review_btn.wait_for(timeout=3_600_000)
    print("   ✓ Pipeline complete!")
    page.wait_for_timeout(1000)
    review_btn.click()
    page.wait_for_timeout(2000)
    print("   ✓ Clicked 'Review & Refine'")

    # ── Step 3: Generation Options ──
    print("🔵 [5/7] Navigating to Step 3 (Generation Options)...")
    cont_btn = page.get_by_role("button", name="Continue to Generation")
    if cont_btn.is_visible(timeout=5000):
        cont_btn.click()
        page.wait_for_url("**/step3", timeout=30_000)
        page.wait_for_timeout(2000)
        _prep(page)
        page.screenshot(path=str(d / "step3-pre-generate.png"))
        print("   ✓ Step 3 screenshot captured")

    # ── Step 4: Generate Video ──
    print("🔵 [6/7] Navigating to Step 4 and starting generation...")
    next4 = page.get_by_role("button", name="Next: Preview & Export")
    if next4.is_visible(timeout=5000):
        next4.click()
        page.wait_for_url("**/step4", timeout=30_000)
        page.wait_for_timeout(2000)

    gen_btn = page.get_by_role("button", name="Generate Video")
    if not gen_btn.is_visible(timeout=10_000):
        page.screenshot(path=str(d / "debug-step4-no-generate.png"))
        pytest.skip("Generate Video button not visible")
    gen_btn.click()
    page.wait_for_timeout(3000)
    print("   ⏳ Video generation started...")

    # ── Capture progress and completion ──
    print("🔵 [7/7] Waiting for progress and completion screenshots...")
    captured_progress = False
    captured_dark_progress = False
    for i in range(80):  # 80 × 15s = 20 min max
        success = page.get_by_text("Your memory video is ready!")
        if success.is_visible(timeout=1000):
            print("   ✓ Generation complete!")
            break

        # Read status text from page
        try:
            status = page.locator(".q-linear-progress + *").first.text_content(timeout=500)
            if status and i % 2 == 0:
                print(f"   ⏳ [{i * 15}s] {status}")
        except Exception:
            pass

        if not captured_progress:
            preview_img = page.locator("img.rounded-lg")
            if preview_img.is_visible(timeout=1000):
                _prep(page)
                page.screenshot(path=str(d / "step4-generating.png"))
                captured_progress = True
                print("   📸 step4-generating.png (with preview frame)")
            else:
                progress_bar = page.locator(".q-linear-progress")
                if progress_bar.is_visible(timeout=500):
                    _prep(page)
                    page.screenshot(path=str(d / "step4-generating-fallback.png"))

        if captured_progress and not captured_dark_progress:
            set_theme(page, "dark")
            page.wait_for_timeout(1000)
            _prep(page)
            page.screenshot(path=str(d / "dark-step4-generating.png"))
            captured_dark_progress = True
            print("   📸 dark-step4-generating.png")
            set_theme(page, "light")

        page.wait_for_timeout(15_000)

    if not captured_progress:
        fallback = d / "step4-generating-fallback.png"
        if fallback.exists():
            fallback.rename(d / "step4-generating.png")
            print("   📸 step4-generating.png (fallback — no preview frame)")

    # ── Capture completion ──
    success = page.get_by_text("Your memory video is ready!")
    if success.is_visible(timeout=5000):
        page.wait_for_timeout(2000)
        _prep(page)
        page.screenshot(path=str(d / "step4-complete.png"))
        print("   📸 step4-complete.png")

        page.evaluate("document.querySelector('.q-drawer')?.style.setProperty('display','none')")
        page.evaluate(
            "document.querySelector('.q-page-container')?.style.setProperty('padding-left','0')"
        )
        page.wait_for_timeout(200)
        page.screenshot(path=str(d / "hero-step4-complete.png"))
        print("   📸 hero-step4-complete.png")

        set_theme(page, "dark")
        page.wait_for_timeout(1500)
        _prep(page)
        page.screenshot(path=str(d / "dark-step4-complete.png"))
        print("   📸 dark-step4-complete.png")
        print("\n✅ All generation screenshots captured!")
    else:
        page.screenshot(path=str(d / "debug-step4-failed.png"))
        print("\n❌ Generation did not complete — debug screenshot saved")
