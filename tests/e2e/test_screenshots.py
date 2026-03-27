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


def _hide_warnings(page: Page) -> None:
    """Hide warning/error alert cards that clutter screenshots."""
    page.evaluate("""() => {
        document.querySelectorAll('.im-alert-warning, .im-alert-error').forEach(
            el => el.style.display = 'none'
        );
    }""")


def _prep(page: Page) -> None:
    enable_demo_mode(page)
    redact_page(page)
    _hide_warnings(page)


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

        # WHY: Default year is current (2026) which may have no trips.
        # Switch to 2025 for a better screenshot with detected trips.
        year_combo = page.get_by_role("combobox", name="Year")
        if year_combo.is_visible(timeout=3000):
            year_combo.click()
            page.wait_for_timeout(300)
            y2025 = page.get_by_role("option", name="2025")
            if y2025.is_visible(timeout=2000):
                y2025.click()
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

    # WHY: Low target duration prevents "exceeds available content" warning in step2.
    # NiceGUI/Quasar inputs need JS injection — get_by_label + fill doesn't trigger state.
    page.evaluate("""() => {
        const fields = document.querySelectorAll('.q-field');
        for (const field of fields) {
            const label = field.querySelector('.q-field__label');
            if (label && label.textContent.includes('Target Duration')) {
                const input = field.querySelector('input');
                if (input) {
                    const nativeSet = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeSet.call(input, '1');
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                break;
            }
        }
    }""")
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
        # Capture loading/analysis state while dialog is visible
        _prep(page)
        _save(page, d, _name("step2-fresh-analysis", theme))
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

    # Grid view — scroll past controls to show clip thumbnails
    grid_btn = page.locator('button:has(i:text("grid_view"))')
    if grid_btn.is_visible(timeout=3000):
        grid_btn.evaluate("el => el.click()")
        page.wait_for_timeout(2000)
        _wait(page)
        # WHY: The clips section is below the fold; scroll the grid toggle into view
        new_grid_btn = page.locator('button:has(i:text("grid_view"))')
        if new_grid_btn.is_visible(timeout=5000):
            new_grid_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("step2-grid", theme))

    # List view — scroll past controls to show month expansions
    list_btn = page.locator('button:has(i:text("view_list"))')
    if list_btn.is_visible(timeout=3000):
        list_btn.evaluate("el => el.click()")
        page.wait_for_timeout(2000)
        _wait(page)
        new_list_btn = page.locator('button:has(i:text("view_list"))')
        if new_list_btn.is_visible(timeout=5000):
            new_list_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)
        _prep(page)
        _save(page, d, _name("step2-list", theme))

    # Expand first month in list view
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

        # WHY: No LLM runs during screenshot tests, so title fields are empty.
        # Inject realistic values via JS to show the cinematic title screen feature.
        page.evaluate("""() => {
            const nativeSet = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
            ).set;
            const fields = document.querySelectorAll('.q-field');
            for (const field of fields) {
                const label = field.querySelector('.q-field__label');
                const input = field.querySelector('input');
                if (!label || !input) continue;
                const text = label.textContent.trim();
                if (text === 'Title') {
                    nativeSet.call(input, 'Summer in Provence');
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                } else if (text === 'Subtitle') {
                    nativeSet.call(input, 'June – August 2025');
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }
        }""")
        page.wait_for_timeout(500)

        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
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
        title_field = page.get_by_label("Title")
        if title_field.is_visible(timeout=3000):
            title_field.scroll_into_view_if_needed()
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
    # STEP 4: Generation (requires FFmpeg — captures generating + complete)
    # ════════════════════════════════════════════════════════════════
    try:
        gen_btn = page.get_by_role("button", name="Generate")
        if gen_btn.is_visible(timeout=3000):
            gen_btn.click()
            # Wait for progress to appear
            page.wait_for_timeout(3000)
            _prep(page)
            _save(page, d, _name("step4-generating", theme))
            _save(page, d, _name("pipeline-loading", theme))

            # Wait for generation to complete (up to 10 minutes)
            try:
                page.wait_for_selector("text=Generation complete", timeout=600_000)
                page.wait_for_timeout(1000)
                _prep(page)
                _save(page, d, _name("step4-complete", theme))

                # Hero: no sidebar
                _hide_sidebar(page)
                _save(page, d, _name("hero-step4-complete", theme))
                _show_sidebar(page)
            except Exception:
                pass  # Generation may timeout — pre-generate screenshots still captured
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
