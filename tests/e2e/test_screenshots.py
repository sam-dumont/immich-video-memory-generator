"""Capture UI screenshots in both light and dark mode.

All screenshots are captured in a SINGLE wizard pass per theme to avoid
exhausting NiceGUI's websocket connections. Each run produces matching
light/dark pairs saved to docs-site/static/img/screenshots/.

Usage:
    make screenshots          # light + dark, saves to docs-site/
    make e2e                  # required hermetic launch gate (no screenshots)
    make e2e-full             # all E2E, including optional visual flows
"""

from __future__ import annotations

import calendar
import contextlib
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.e2e.conftest import enable_demo_mode, set_theme
from tests.e2e.redaction import redact_page, redact_person_names

pytestmark = [pytest.mark.e2e, pytest.mark.visual]

_THEMES = ["light", "dark"]
# WHY: never capture the current year (maintainer's live library); previous year is stable
_CAPTURE_YEAR = int(os.environ.get("IMMICH_MEMORIES_E2E_CAPTURE_YEAR", "2025"))
# WHY: Steps 2-4 run the real pipeline; one month keeps the pool (and render) small
_CAPTURE_MONTH = int(os.environ.get("IMMICH_MEMORIES_E2E_CAPTURE_MONTH", "6"))
# WHY: first-time analysis of a real library can exceed 5 minutes; allow the capture
# session to raise the cap without editing the test (default unchanged).
_CLIP_LOAD_TIMEOUT = int(os.environ.get("IMMICH_MEMORIES_E2E_CLIP_TIMEOUT_MS", "300000"))
_GENERATION_TIMEOUT = int(os.environ.get("IMMICH_MEMORIES_E2E_GENERATION_TIMEOUT_MS", "600000"))


def _name(base: str, theme: str) -> str:
    return f"dark-{base}" if theme == "dark" else base


def _save(page: Page, d: Path, name: str) -> None:
    page.screenshot(path=str(d / f"{name}.png"))


def _save_navigation_diagnostic(page: Page, name: str) -> None:
    """Keep one browser artifact when optional visual navigation times out.

    WHY: diagnostics come from a real library, so redact before saving; the
    directory is gitignored (`test-results/`) — never commit these.
    """
    output = Path("test-results") / "e2e"
    output.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        _prep(page)
    page.screenshot(path=str(output / f"{name}.png"))


def _goto(page: Page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        _save_navigation_diagnostic(page, "goto-timeout")
        page.wait_for_timeout(2000)


def _wait(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
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
    # WHY: Quasar positions the page with an inline padding-left; remember it so
    # _show_sidebar can restore it — removeProperty() would leave the drawer
    # overlaying the content and intercepting clicks.
    page.evaluate("""() => {
        const c = document.querySelector('.q-page-container');
        if (!c) return;
        c.dataset.imPrevPaddingLeft = c.style.paddingLeft;
        c.style.setProperty('padding-left', '0');
    }""")
    page.wait_for_timeout(200)


def _show_sidebar(page: Page) -> None:
    page.evaluate("document.querySelector('.q-drawer')?.style.removeProperty('display')")
    page.evaluate("""() => {
        const c = document.querySelector('.q-page-container');
        if (!c) return;
        const prev = c.dataset.imPrevPaddingLeft;
        if (prev) c.style.setProperty('padding-left', prev);
        else c.style.removeProperty('padding-left');
    }""")
    page.wait_for_timeout(200)


def _choose_select(page: Page, label_text: str, option_text: str) -> None:
    """Pick an option in a labelled Quasar q-select (e.g. Year → 2025)."""
    label = (
        page.locator(".q-select .q-field__label")
        .filter(has_text=re.compile(rf"^{re.escape(label_text)}$"))
        .first
    )
    if not label.count():
        return
    label.evaluate("el => el.closest('.q-select').click()")
    page.wait_for_timeout(500)
    option = (
        page.locator(".q-menu .q-item")
        .filter(has_text=re.compile(rf"^{re.escape(option_text)}$"))
        .first
    )
    if option.count():
        option.evaluate("el => el.click()")
        page.wait_for_timeout(700)
    else:
        page.keyboard.press("Escape")


def _choose_year(page: Page, year: int) -> None:
    """Pick a year in the preset's Year select.

    WHY: the capture must not default to the current year — its content is
    the maintainer's live library; a fixed earlier year keeps shots stable.
    """
    _choose_select(page, "Year", str(year))


def _set_manual_target_minutes(page: Page, minutes: float) -> None:
    """Turn the Auto duration switch off and type a manual target.

    WHY: a 1-minute target keeps the Step 4 render short enough for a capture
    session; Auto would plan the preset length (10 min for a year).
    """
    # WHY: real clicks (force=True) — synthetic el.click() is not reliably picked
    # up by Quasar's toggle/button handlers after a re-render
    auto_switch = page.locator(".q-toggle").filter(has_text="Auto duration").first
    if auto_switch.count() and auto_switch.get_attribute("aria-checked") == "true":
        auto_switch.click(force=True)
        page.wait_for_timeout(800)
    target_field = page.locator(".q-field").filter(has_text="Target duration")
    if target_field.count():
        field_input = target_field.first.locator("input")
        field_input.click(force=True)
        field_input.fill(str(minutes))
        field_input.press("Tab")
    page.wait_for_timeout(700)


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
        _choose_year(page, _CAPTURE_YEAR)
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
        except PlaywrightTimeoutError:
            _save_navigation_diagnostic(page, "trip-detection-timeout")
            pass

    # Wizard navigation runs the real pipeline: use one month of the capture year
    monthly_btn = page.get_by_text("Monthly Highlights")
    if monthly_btn.is_visible(timeout=3000):
        monthly_btn.click()
        page.wait_for_timeout(500)
        _choose_select(page, "Year", str(_CAPTURE_YEAR))
        _choose_select(page, "Month", calendar.month_name[_CAPTURE_MONTH])

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
    except PlaywrightTimeoutError:
        _save_navigation_diagnostic(page, "step2-navigation-timeout")
        return
    try:
        page.wait_for_selector('[role="dialog"]', timeout=5_000)
        # Capture loading/analysis state while dialog is visible
        _prep(page)
        _save(page, d, _name("step2-fresh-analysis", theme))
    except PlaywrightTimeoutError:
        pass
    page.wait_for_function(
        "() => !document.querySelector('[role=\"dialog\"]')",
        timeout=_CLIP_LOAD_TIMEOUT,
    )
    _wait(page)
    try:
        page.wait_for_selector('button:has-text("clips")', timeout=30_000)
    except PlaywrightTimeoutError:
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
    # WHY: NiceGUI expansion headers are .q-item[role=button], not <button>; has_text needs a compiled regex
    month_button = (
        page.locator(".q-expansion-item .q-item")
        .filter(has_text=re.compile(r"\(\d+ clips?\)"))
        .first
    )
    if month_button.is_visible():
        month_button.scroll_into_view_if_needed()
        month_button.click()
        page.wait_for_timeout(1000)
        month_button.scroll_into_view_if_needed()
        _prep(page)
        _save(page, d, _name("step2-clip-grid", theme))

    # Run the analysis pipeline from the "Generate Memories" panel.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    _set_manual_target_minutes(page, 1)
    # WHY: the expansion header carries the same accessible name; target the real <button>
    gen_btn = page.locator("button.q-btn:visible").filter(has_text="Generate Memories").first
    if gen_btn.is_visible(timeout=3000):
        gen_btn.click(force=True)
        # WHY: the click navigates to the pipeline page; evaluate() on the old
        # document raises "execution context was destroyed"
        page.wait_for_timeout(1500)
        _wait(page)
        page.wait_for_timeout(2500)
        _prep(page)
        _save(page, d, _name("pipeline-loading", theme))
        review_btn = (
            page.locator("button.q-btn:visible")
            .filter(has_text="Review & Refine Selected Clips")
            .first
        )
        try:
            review_btn.wait_for(timeout=_CLIP_LOAD_TIMEOUT)
        except PlaywrightTimeoutError:
            _save_navigation_diagnostic(page, "pipeline-timeout")
            return
        review_btn.click(force=True)
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
        cont.click(force=True)
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
        # WHY: the capture environment has no in-process music backend; render
        # without music so the Step 4 frames don't show a failed-music warning.
        _choose_select(page, "Background music", "None")
    except PlaywrightTimeoutError:
        _save_navigation_diagnostic(page, "step3-navigation-timeout")
        pass

    # ════════════════════════════════════════════════════════════════
    # STEP 4: Preview & Export
    # ════════════════════════════════════════════════════════════════
    next4 = page.get_by_role("button", name="Next: Preview & Export")
    try:
        next4.click(force=True)
        page.wait_for_url("**/step4", timeout=30_000)
        _wait(page)
        _prep(page)
        _save(page, d, _name("step4-preview-export", theme))
        _save(page, d, _name("step4-pre-generate", theme))

        # Hero: no sidebar
        _hide_sidebar(page)
        _save(page, d, _name("hero-step4", theme))
        _show_sidebar(page)
    except PlaywrightTimeoutError:
        _save_navigation_diagnostic(page, "step4-navigation-timeout")
        pass

    # ════════════════════════════════════════════════════════════════
    # STEP 4: Generation (requires FFmpeg — captures generating + complete)
    # ════════════════════════════════════════════════════════════════
    try:
        gen_btn = page.locator("button.q-btn:visible").filter(has_text="Generate Video").first
        if gen_btn.is_visible(timeout=3000):
            gen_btn.click()
            # Wait for progress to appear
            page.wait_for_timeout(3000)
            _prep(page)
            _save(page, d, _name("step4-generating", theme))
            _save(page, d, _name("pipeline-loading", theme))

            # Wait for generation to complete (up to 10 minutes)
            try:
                try:
                    page.wait_for_selector(
                        "text=Your memory video is ready!", timeout=_GENERATION_TIMEOUT
                    )
                except PlaywrightTimeoutError:
                    # WHY: the completion write can miss a client that NiceGUI
                    # considers gone; the run itself is durable, so reload once.
                    page.reload(wait_until="domcontentloaded")
                    _wait(page)
                    page.wait_for_selector("text=Your memory video is ready!", timeout=60_000)
                page.wait_for_timeout(1000)
                _prep(page)
                _save(page, d, _name("step4-complete", theme))

                # Hero: no sidebar
                _hide_sidebar(page)
                _save(page, d, _name("hero-step4-complete", theme))
                _show_sidebar(page)
            except PlaywrightTimeoutError:
                _save_navigation_diagnostic(page, "generation-timeout")
                pass  # Generation may timeout — pre-generate screenshots still captured
    except PlaywrightTimeoutError:
        _save_navigation_diagnostic(page, "generation-start-timeout")
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
    _goto(page, f"{app_url}/login")
    _wait(page)

    sign_in = page.get_by_role("button", name="Sign in")
    sso = page.get_by_text("Sign in with SSO")
    if not sign_in.is_visible(timeout=2000) and not sso.is_visible(timeout=1000):
        return

    set_theme(page, theme)
    _goto(page, f"{app_url}/login")
    _wait(page)
    _save(page, screenshot_dir, _name("login", theme))
