"""Capture UI screenshots in both light and dark mode.

Each run produces matching light/dark pairs for every screenshot.
Screenshots are saved to docs-site/static/img/screenshots/ and are
referenced by the ThemedScreenshot Docusaurus component.

Usage:
    make screenshots          # light + dark, saves to docs-site/
    make e2e                  # full E2E suite (includes screenshots)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import enable_demo_mode, set_theme
from tests.e2e.redaction import redact_page, redact_person_names

pytestmark = pytest.mark.e2e

_THEMES = ["light", "dark"]

# Maximum time to wait for clip loading (Immich fetch can be slow)
_CLIP_LOAD_TIMEOUT = 300_000  # 5 minutes


def _name(base: str, theme: str) -> str:
    """Return screenshot filename: 'dark-{base}' for dark, '{base}' for light."""
    return f"dark-{base}" if theme == "dark" else base


def _save(page: Page, screenshot_dir: Path, name: str) -> None:
    path = screenshot_dir / f"{name}.png"
    page.screenshot(path=str(path))


def _wait_for_ready(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


def _before_screenshot(page: Page) -> None:
    enable_demo_mode(page)
    redact_page(page)


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_all_screenshots(
    page: Page,
    app_url: str,
    screenshot_dir: Path,
    theme: str,
) -> None:
    """Navigate the full wizard and capture screenshots at each step.

    One function per theme to preserve navigation state across steps.
    This mirrors the structure of take-screenshots.ts.
    """
    # -- Set theme + enable demo mode for privacy --
    page.goto(app_url)
    _wait_for_ready(page)
    set_theme(page, theme)

    # ── Step 1: Configuration ──
    page.goto(app_url)
    _wait_for_ready(page)
    _before_screenshot(page)
    _save(page, screenshot_dir, _name("step1-config-connected", theme))

    # Preset cards (scroll down to show them)
    year_preset = page.get_by_text("Year in Review")
    if year_preset.is_visible():
        year_preset.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step1-preset-cards", theme))

        # Click to show selected state
        year_preset.click()
        page.wait_for_timeout(500)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step1-preset-selected", theme))

    # Person dropdown — only visible with person-specific presets
    person_preset = page.get_by_text("Person Spotlight")
    if person_preset.is_visible(timeout=3000):
        person_preset.scroll_into_view_if_needed()
        person_preset.click()
        page.wait_for_timeout(1000)

        person_combo = page.get_by_role("combobox", name="Person")
        if person_combo.is_visible(timeout=10_000):
            person_combo.click()
            page.wait_for_timeout(500)
            enable_demo_mode(page)
            redact_person_names(page)
            _save(page, screenshot_dir, _name("step1-person-dropdown", theme))

            options = page.get_by_role("option")
            if options.count() > 1:
                options.nth(1).click()
            else:
                page.get_by_role("option", name="All people").click()
            page.wait_for_timeout(300)

        # Switch back to Year in Review for the rest of the flow
        year_btn = page.get_by_text("Year in Review")
        if year_btn.is_visible():
            year_btn.click()
            page.wait_for_timeout(500)

    # Cache management panel
    cache_button = page.get_by_text("Cache Management")
    if cache_button.is_visible(timeout=10_000):
        cache_button.scroll_into_view_if_needed()
        cache_button.click()
        page.wait_for_timeout(500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(300)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step1-cache-panel", theme))

    # ── Step 2: Clip Review ──
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)

    next_btn = page.get_by_role("button", name="Next: Review Clips")
    if not next_btn.is_visible():
        return  # Can't proceed without Immich connection
    next_btn.click()
    page.wait_for_url("**/step2", timeout=30_000)

    # Wait for loading dialog to disappear
    try:
        page.wait_for_selector('[role="dialog"]', timeout=5_000)
    except Exception:
        pass  # Dialog may have already gone or never appeared
    page.wait_for_function(
        "() => !document.querySelector('[role=\"dialog\"]')",
        timeout=_CLIP_LOAD_TIMEOUT,
    )
    _wait_for_ready(page)

    # Wait for clip content
    try:
        page.wait_for_selector('button:has-text("clips")', timeout=30_000)
    except Exception:
        pass  # May not have clips, screenshot anyway
    _before_screenshot(page)
    _save(page, screenshot_dir, _name("step2-clip-review", theme))

    # Expand first month
    month_button = page.locator("button").filter(has_text=r"\(\d+ clips?\)").first
    if month_button.is_visible():
        month_button.scroll_into_view_if_needed()
        month_button.click()
        page.wait_for_timeout(1000)
        month_button.scroll_into_view_if_needed()
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step2-clip-grid", theme))

    # Refine Moments
    refine_btn = page.get_by_role("button", name="Next: Refine Moments")
    if refine_btn.is_visible():
        refine_btn.scroll_into_view_if_needed()
        refine_btn.click()
        _wait_for_ready(page)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step2-refine-moments", theme))

    # ── Step 3: Generation Options ──
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    continue_btn = page.get_by_role("button", name="Continue to Generation")
    try:
        continue_btn.click(timeout=10_000)
        page.wait_for_url("**/step3", timeout=30_000)
        _wait_for_ready(page)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step3-options", theme))
    except Exception:
        pass  # Button may not exist depending on clip state

    # ── Step 4: Preview & Export ──
    next4_btn = page.get_by_role("button", name="Next: Preview & Export")
    try:
        next4_btn.click(timeout=10_000)
        page.wait_for_url("**/step4", timeout=30_000)
        _wait_for_ready(page)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("step4-preview-export", theme))
    except Exception:
        pass

    # ── Settings: Config Viewer ──
    page.goto(f"{app_url}/settings/config")
    _wait_for_ready(page)
    _before_screenshot(page)
    _save(page, screenshot_dir, _name("settings-config", theme))

    # ── Settings: Cache Management ──
    page.goto(f"{app_url}/settings/cache")
    _wait_for_ready(page)
    page.wait_for_timeout(2000)
    _before_screenshot(page)
    _save(page, screenshot_dir, _name("settings-cache", theme))

    # ── Feature: Trip Preset Selected ──
    page.goto(app_url)
    _wait_for_ready(page)
    trip_preset = page.get_by_text("Trip", exact=True)
    if trip_preset.is_visible(timeout=5000):
        trip_preset.scroll_into_view_if_needed()
        trip_preset.click()
        page.wait_for_timeout(500)
        _before_screenshot(page)
        _save(page, screenshot_dir, _name("trip-preset", theme))

    # ── Feature: Privacy/Demo Mode Toggle ──
    demo_switch = page.get_by_text("Demo mode")
    if demo_switch.is_visible(timeout=3000):
        demo_switch.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        _save(page, screenshot_dir, _name("privacy-toggle", theme))


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_login_page(
    page: Page,
    app_url: str,
    screenshot_dir: Path,
    theme: str,
) -> None:
    """Capture the login page (basic auth variant).

    The login page is at /login even when auth is disabled — it just
    redirects. We navigate directly and capture if it renders.
    """
    page.goto(f"{app_url}/login")
    _wait_for_ready(page)

    # Only capture if a login form actually rendered (auth may be disabled)
    sign_in_btn = page.get_by_role("button", name="Sign in")
    sso_btn = page.get_by_text("Sign in with SSO")
    if not sign_in_btn.is_visible(timeout=2000) and not sso_btn.is_visible(timeout=1000):
        return

    set_theme(page, theme)
    page.goto(f"{app_url}/login")
    _wait_for_ready(page)
    _save(page, screenshot_dir, _name("login", theme))
