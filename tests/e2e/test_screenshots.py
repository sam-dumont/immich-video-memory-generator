"""Capture UI screenshots in both light and dark mode.

All screenshots are captured in a SINGLE wizard pass per theme to avoid
exhausting NiceGUI's websocket connections. Each run produces matching
light/dark pairs saved to docs-site/static/img/screenshots/.

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
_CLIP_LOAD_TIMEOUT = 300_000  # 5 minutes


def _name(base: str, theme: str) -> str:
    return f"dark-{base}" if theme == "dark" else base


def _save(page: Page, d: Path, name: str) -> None:
    page.screenshot(path=str(d / f"{name}.png"))


def _goto(page: Page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        page.wait_for_timeout(2000)


def _wait(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


def _prep(page: Page) -> None:
    enable_demo_mode(page)
    redact_page(page)


def _hide_sidebar(page: Page) -> None:
    page.evaluate("document.querySelector('.q-drawer')?.style.setProperty('display','none')")
    page.evaluate(
        "document.querySelector('.q-page-container')?.style.setProperty('padding-left','0')"
    )
    page.wait_for_timeout(200)


def _show_sidebar(page: Page) -> None:
    page.evaluate("document.querySelector('.q-drawer')?.style.removeProperty('display')")
    page.evaluate(
        "document.querySelector('.q-page-container')?.style.removeProperty('padding-left')"
    )
    page.wait_for_timeout(200)


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_all(page: Page, app_url: str, screenshot_dir: Path, theme: str) -> None:
    """Single wizard pass capturing all screenshots for one theme."""
    d = screenshot_dir

    # ── Set theme ──
    _goto(page, app_url)
    _wait(page)
    set_theme(page, theme)

    # ════════════════════════════════════════════════════════════════
    # STEP 1: Configuration
    # ════════════════════════════════════════════════════════════════
    _goto(page, app_url)
    _wait(page)
    _prep(page)
    _save(page, d, _name("step1-config-connected", theme))
    _save(page, d, _name("step1-overview", theme))

    # Hero: no sidebar
    _hide_sidebar(page)
    _save(page, d, _name("hero-step1", theme))
    _show_sidebar(page)

    # Preset cards
    year_preset = page.get_by_text("Year in Review")
    if year_preset.is_visible():
        year_preset.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        _prep(page)
        _save(page, d, _name("step1-preset-cards", theme))

        year_preset.click()
        page.wait_for_timeout(500)
        _prep(page)
        _save(page, d, _name("step1-preset-selected", theme))
        _save(page, d, _name("type-year-review", theme))

    # Person dropdown (needs Person Spotlight preset)
    person_preset = page.get_by_text("Person Spotlight")
    if person_preset.is_visible(timeout=3000):
        person_preset.scroll_into_view_if_needed()
        person_preset.click()
        page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("type-person", theme))

        person_combo = page.get_by_role("combobox", name="Person")
        if person_combo.is_visible(timeout=10_000):
            person_combo.click()
            page.wait_for_timeout(500)
            enable_demo_mode(page)
            redact_person_names(page)
            _save(page, d, _name("step1-person-dropdown", theme))

            options = page.get_by_role("option")
            if options.count() > 1:
                options.nth(1).click()
            else:
                page.get_by_role("option", name="All people").click()
            page.wait_for_timeout(300)

    # Monthly Highlights preset
    monthly = page.get_by_text("Monthly Highlights")
    if monthly.is_visible(timeout=3000):
        monthly.scroll_into_view_if_needed()
        monthly.click()
        page.wait_for_timeout(1000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        _prep(page)
        _save(page, d, _name("type-monthly", theme))

    # Trip preset + detection
    trip = page.get_by_text("Trip", exact=True)
    if trip.is_visible(timeout=3000):
        trip.scroll_into_view_if_needed()
        trip.click()
        page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("trip-preset", theme))
        _save(page, d, _name("type-trip", theme))

        try:
            page.get_by_text("Found").wait_for(timeout=30_000)
            page.wait_for_timeout(500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(300)
            _prep(page)
            _save(page, d, _name("trip-detection", theme))
        except Exception:
            pass

    # Switch back to Year in Review for wizard navigation
    year_btn = page.get_by_text("Year in Review")
    if year_btn.is_visible(timeout=3000):
        year_btn.click()
        page.wait_for_timeout(500)

    # ════════════════════════════════════════════════════════════════
    # STEP 2: Clip Review
    # ════════════════════════════════════════════════════════════════
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    next_btn = page.get_by_role("button", name="Next: Review Clips")
    if not next_btn.is_visible(timeout=3000):
        return
    next_btn.click()

    try:
        page.wait_for_url("**/step2", timeout=30_000)
    except Exception:
        return
    try:
        page.wait_for_selector('[role="dialog"]', timeout=5_000)
    except Exception:
        pass
    page.wait_for_function(
        "() => !document.querySelector('[role=\"dialog\"]')",
        timeout=_CLIP_LOAD_TIMEOUT,
    )
    _wait(page)
    try:
        page.wait_for_selector('button:has-text("clips")', timeout=30_000)
    except Exception:
        pass

    _prep(page)
    _save(page, d, _name("step2-clip-review", theme))

    # Hero: no sidebar
    _hide_sidebar(page)
    _save(page, d, _name("hero-step2", theme))
    _show_sidebar(page)

    # Grid view
    grid_btn = page.locator('button:has(i:text("grid_view"))')
    if grid_btn.is_visible(timeout=3000):
        grid_btn.evaluate("el => el.click()")
        page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("step2-grid", theme))

    # List view
    list_btn = page.locator('button:has(i:text("view_list"))')
    if list_btn.is_visible(timeout=3000):
        list_btn.evaluate("el => el.click()")
        page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("step2-list", theme))

    # Expand first month
    month_button = page.locator("button").filter(has_text=r"\(\d+ clips?\)").first
    if month_button.is_visible():
        month_button.scroll_into_view_if_needed()
        month_button.click()
        page.wait_for_timeout(1000)
        month_button.scroll_into_view_if_needed()
        _prep(page)
        _save(page, d, _name("step2-clip-grid", theme))

    # Refine Moments
    refine_btn = page.get_by_role("button", name="Next: Refine Moments")
    if refine_btn.is_visible():
        refine_btn.scroll_into_view_if_needed()
        # WHY: JS click bypasses sidebar overlay pointer interception
        refine_btn.evaluate("el => el.click()")
        _wait(page)
        _prep(page)
        _save(page, d, _name("step2-refine-moments", theme))

    # ════════════════════════════════════════════════════════════════
    # STEP 3: Generation Options
    # ════════════════════════════════════════════════════════════════
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    cont = page.get_by_role("button", name="Continue to Generation")
    try:
        cont.evaluate("el => el.click()")
        page.wait_for_url("**/step3", timeout=30_000)
        _wait(page)
        _prep(page)
        _save(page, d, _name("step3-options", theme))
        _save(page, d, _name("step3-basic", theme))

        # Advanced options expanded
        advanced = page.get_by_text("Advanced options")
        if advanced.is_visible(timeout=3000):
            advanced.click()
            page.wait_for_timeout(500)
            advanced.scroll_into_view_if_needed()
            _prep(page)
            _save(page, d, _name("step3-advanced", theme))

        # LLM title fields
        title_input = page.get_by_label("Title")
        if title_input.is_visible(timeout=3000):
            title_input.scroll_into_view_if_needed()
            _prep(page)
            _save(page, d, _name("llm-title-step3", theme))

        regen_btn = page.get_by_role("button", name="Regenerate")
        if regen_btn.is_visible(timeout=3000):
            regen_btn.scroll_into_view_if_needed()
            _prep(page)
            _save(page, d, _name("llm-title-regenerate", theme))
    except Exception:
        pass

    # ════════════════════════════════════════════════════════════════
    # STEP 4: Preview & Export
    # ════════════════════════════════════════════════════════════════
    next4 = page.get_by_role("button", name="Next: Preview & Export")
    try:
        next4.evaluate("el => el.click()")
        page.wait_for_url("**/step4", timeout=30_000)
        _wait(page)
        _prep(page)
        _save(page, d, _name("step4-preview-export", theme))
        _save(page, d, _name("step4-pre-generate", theme))

        # Hero: no sidebar
        _hide_sidebar(page)
        _save(page, d, _name("hero-step4", theme))
        _show_sidebar(page)
    except Exception:
        pass

    # ════════════════════════════════════════════════════════════════
    # SETTINGS PAGES
    # ════════════════════════════════════════════════════════════════
    _goto(page, f"{app_url}/settings/config")
    _wait(page)
    _prep(page)
    _save(page, d, _name("settings-config", theme))

    _goto(page, f"{app_url}/settings/cache")
    _wait(page)
    page.wait_for_timeout(2000)
    _prep(page)
    _save(page, d, _name("settings-cache", theme))


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_login_page(page: Page, app_url: str, screenshot_dir: Path, theme: str) -> None:
    """Capture the login page (skips if auth disabled)."""
    try:
        _goto(page, f"{app_url}/login")
    except Exception:
        return
    _wait(page)

    sign_in = page.get_by_role("button", name="Sign in")
    sso = page.get_by_text("Sign in with SSO")
    if not sign_in.is_visible(timeout=2000) and not sso.is_visible(timeout=1000):
        return

    set_theme(page, theme)
    _goto(page, f"{app_url}/login")
    _wait(page)
    _save(page, screenshot_dir, _name("login", theme))
