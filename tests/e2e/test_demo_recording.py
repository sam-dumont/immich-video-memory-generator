"""Record demo video segments via Playwright.

Each test function records one segment as a .webm file.
Run with: make demo-record

Segments:
  1. Step 1 — Config (fast: connect, preset, person)
  2. Step 2 — Navigate to grid (fast: click Next, loading)
  3. Step 2 — Clip grid (highlight: scroll through thumbnails)
  4. Step 3 — Options (fast: toggle settings, scroll)
  5. Step 4 — Generation progress (highlight: progress bar)
  6. Step 4 — Final preview (wow: video playing)

Intro/outro cards are generated from screenshots during assembly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, Playwright

from tests.e2e.conftest import enable_demo_mode
from tests.e2e.redaction import redact_page

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_VIDEO_SIZE = {"width": 1440, "height": 900}
_CLIP_LOAD_TIMEOUT = 300_000  # 5 minutes


def _recording_context(playwright: Playwright, raw_dir: Path) -> BrowserContext:
    """Create a browser context with video recording enabled."""
    browser = playwright.chromium.launch()
    return browser.new_context(
        viewport={"width": _VIDEO_SIZE["width"], "height": _VIDEO_SIZE["height"]},
        record_video_dir=str(raw_dir),
        record_video_size=_VIDEO_SIZE,
    )


def _smooth_scroll(page: Page, total_delta: int, step: int = 80, delay: int = 60) -> None:
    """Simulate natural smooth scrolling."""
    scrolled = 0
    while scrolled < total_delta:
        page.mouse.wheel(0, step)
        page.wait_for_timeout(delay)
        scrolled += step


def _save_segment(context: BrowserContext, page: Page, raw_dir: Path, name: str) -> None:
    """Close page to finalize recording, rename to segment name."""
    page.close()
    video_path = page.video.path()
    context.close()
    if video_path and Path(video_path).exists():
        dest = raw_dir / f"{name}.webm"
        shutil.move(str(video_path), str(dest))


def _setup_step1(page: Page, app_url: str) -> None:
    """Navigate to app and select preset + person."""
    page.goto(app_url, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1000)
    enable_demo_mode(page)
    redact_page(page)

    preset = page.locator("text=Person Spotlight").first
    if preset.is_visible(timeout=5000):
        preset.click()
        page.wait_for_timeout(800)

    person_dropdown = page.locator('[aria-label="Person"]').first
    if person_dropdown.is_visible(timeout=3000):
        person_dropdown.click()
        page.wait_for_timeout(500)
        first_option = page.locator('[role="option"]').first
        if first_option.is_visible(timeout=3000):
            first_option.click()
            page.wait_for_timeout(500)

    redact_page(page)


def _wait_for_stable(page: Page) -> None:
    """Wait for NiceGUI to finish rendering before DOM manipulation."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def _navigate_to_step2(page: Page) -> None:
    """Click Next to load clips."""
    next_btn = page.locator("button:has-text('Next')").first
    if next_btn.is_visible(timeout=3000):
        next_btn.click()
    page.locator(".q-card, .clip-card, [class*='clip']").first.wait_for(timeout=_CLIP_LOAD_TIMEOUT)
    page.wait_for_timeout(1500)
    _wait_for_stable(page)
    redact_page(page)


def _navigate_to_step3(page: Page) -> None:
    """Click Next to generation options."""
    next_btn = page.locator("button:has-text('Next')").first
    if next_btn.is_visible(timeout=3000):
        next_btn.click()
    page.wait_for_timeout(2000)
    _wait_for_stable(page)
    redact_page(page)


class TestDemoRecording:
    def test_segment1_config(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 1: Connect to Immich, select preset, pick person."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        page.wait_for_timeout(1500)

        _save_segment(ctx, page, demo_raw_dir, "segment1-config")

    def test_segment2_navigate(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 2: Click Next to load clips, show loading spinner."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        next_btn = page.locator("button:has-text('Next')").first
        if next_btn.is_visible(timeout=3000):
            next_btn.click()
        page.wait_for_timeout(3000)
        redact_page(page)

        _save_segment(ctx, page, demo_raw_dir, "segment2-navigate")

    def test_segment3_clip_grid(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 2 highlight: Scroll through clip grid thumbnails."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        _navigate_to_step2(page)

        # Smooth scroll through the grid — the highlight moment
        page.wait_for_timeout(1000)
        _smooth_scroll(page, 600, step=40, delay=80)
        page.wait_for_timeout(1500)
        _smooth_scroll(page, 400, step=40, delay=80)
        page.wait_for_timeout(1000)

        _save_segment(ctx, page, demo_raw_dir, "segment3-grid")

    def test_segment4_options(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 3: Toggle generation options, scroll to show all."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        _navigate_to_step2(page)
        _navigate_to_step3(page)

        # Scroll through options
        _smooth_scroll(page, 400, step=60, delay=50)
        page.wait_for_timeout(1000)

        _save_segment(ctx, page, demo_raw_dir, "segment4-options")

    def test_segment5_progress(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 4 highlight: Generation progress bar filling."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        _navigate_to_step2(page)
        _navigate_to_step3(page)

        # Click Generate
        generate_btn = page.locator("button:has-text('Generate')").first
        if generate_btn.is_visible(timeout=5000):
            generate_btn.click()

        # Record progress bar for ~20 seconds
        page.wait_for_timeout(20_000)
        redact_page(page)

        _save_segment(ctx, page, demo_raw_dir, "segment5-progress")

    def test_segment6_preview(
        self, playwright: Playwright, app_url: str, demo_raw_dir: Path
    ) -> None:
        """Step 4: Final video preview playing — the wow moment."""
        ctx = _recording_context(playwright, demo_raw_dir)
        page = ctx.new_page()

        _setup_step1(page, app_url)
        _navigate_to_step2(page)
        _navigate_to_step3(page)

        generate_btn = page.locator("button:has-text('Generate')").first
        if generate_btn.is_visible(timeout=5000):
            generate_btn.click()

        # Wait for generation to complete (up to 10 minutes)
        page.locator("text=Video saved").first.wait_for(timeout=600_000)
        page.wait_for_timeout(2000)
        redact_page(page)

        # Scroll to video player and let it play
        _smooth_scroll(page, 300, step=60, delay=60)
        page.wait_for_timeout(8000)

        _save_segment(ctx, page, demo_raw_dir, "segment6-preview")
