"""Capture the Memory page walkthrough for the docs, in light and dark.

Every frame comes from the hermetic launch -- the fake Immich service and the
scripted editorial route -- so nothing personal can reach a screenshot. Each
theme is one pass through the flow: the brief, Advanced, the cut in progress,
the story it produced, Export and the options page behind it. The files land
in docs-site/static/img/screenshots/ under the names the docs embed.

Usage:
    make screenshots          # light + dark, saves to docs-site/
    make e2e                  # required hermetic launch gate (no screenshots)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import set_theme
from tests.e2e.fake_editorial import STAGES
from tests.e2e.fake_library import THESIS, summary_line
from tests.e2e.redaction import assert_no_real_address, redact_page
from tests.e2e.test_launch_smoke import _choose

pytestmark = [pytest.mark.e2e, pytest.mark.visual]

_THEMES = ("light", "dark")
_THESIS = THESIS
# Any editing stage the fixture announces after preparation: the active phase row
# shows it, which is the frame the cutting screenshot wants.
_EDITING_STAGE = re.compile("^(" + "|".join(re.escape(stage) for stage in STAGES[1:]) + ")$")


def _name(base: str, theme: str) -> str:
    return f"dark-{base}" if theme == "dark" else base


def _save(page: Page, directory: Path, name: str) -> None:
    """Park the pointer, drop leftover tooltips, redact temp paths, then shoot."""
    page.mouse.move(0, 0)
    page.evaluate("""() => {
        document.querySelectorAll('.q-tooltip, .q-menu').forEach(
            el => el.style.display = 'none'
        );
    }""")
    # WHY: the hermetic launch writes under a pytest temp root that carries the
    # developer's user name in its path; the redaction rewrites those lines.
    redact_page(page)
    assert_no_real_address(page)
    page.wait_for_timeout(300)
    page.screenshot(path=str(directory / f"{name}.png"))


def _hide_sidebar(page: Page) -> None:
    page.evaluate("document.querySelector('.q-drawer')?.style.setProperty('display','none')")
    # WHY: Quasar positions the page with an inline padding-left; remember it so
    # _show_sidebar can restore it -- removeProperty() would leave the drawer
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


def _open_brief(page: Page, launch_app_url: str) -> None:
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("combobox", name="Memory type")).to_be_visible(timeout=30_000)


@pytest.mark.parametrize("theme", _THEMES)
def test_trip_walkthrough(
    page: Page, launch_app_url: str, screenshot_dir: Path, theme: str
) -> None:
    _open_brief(page, launch_app_url)
    set_theme(page, theme)
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Trip")
    trip = page.get_by_role("combobox", name="Select a trip")
    expect(trip).to_be_visible(timeout=30_000)
    trip.click()
    page.get_by_role("option", name=re.compile(r"2024-06-21 to 2024-06-27")).click()
    _save(page, screenshot_dir, _name("memory-trip-brief", theme))
    page.get_by_role("button", name="Cut", exact=True).click()
    export = page.get_by_role("button", name="Export", exact=True)
    expect(export).to_be_visible(timeout=120_000)
    _save(page, screenshot_dir, _name("memory-trip-story", theme))
    page.get_by_role("tab", name="Story", exact=True).click()
    # The story title also appears behind the hidden storyboard tab.
    expect(page.get_by_text("The story", exact=True)).to_be_visible()
    export.click()
    page.wait_for_url("**/step4", timeout=30_000)
    expect(page.get_by_role("button", name="Generate Video")).to_be_visible(timeout=30_000)
    _save(page, screenshot_dir, _name("memory-trip-export", theme))


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_memory_walkthrough(
    page: Page, launch_app_url: str, screenshot_dir: Path, theme: str
) -> None:
    """One pass through the Memory page for one theme, saving every frame the docs embed."""
    d = screenshot_dir

    _open_brief(page, launch_app_url)
    # WHY: the toggle reloads the page; the brief has to be back before the select is used.
    set_theme(page, theme)
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")
    expect(page.get_by_text("Auto · 1m 00s", exact=True)).to_be_visible()
    _save(page, d, _name("memory-brief", theme))

    advanced = page.get_by_text("Advanced", exact=True)
    advanced.click()
    expect(page.get_by_role("button", name="Open the media pool")).to_be_visible()
    # WHY: the expansion animates open; scrolling before it settles lands short of the pool.
    page.wait_for_timeout(600)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(400)
    _save(page, d, _name("memory-brief-advanced", theme))
    advanced.click()
    page.wait_for_timeout(400)
    page.evaluate("window.scrollTo(0, 0)")

    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(re.compile("left in this stage$"))).to_be_visible(timeout=60_000)
    _save(page, d, _name("memory-cutting-estimate", theme))
    # WHY .cut-phase-rows: the detail panel echoes the same stage string, and an
    # unscoped match is two elements the moment that panel has caught up.
    active_stage = page.locator(".cut-phase-rows").get_by_text(_EDITING_STAGE)
    expect(active_stage).to_be_visible(timeout=60_000)
    _save(page, d, _name("memory-cutting", theme))

    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    export = page.get_by_role("button", name="Export", exact=True)
    expect(export).to_be_visible()
    _save(page, d, _name("memory-story", theme))
    _hide_sidebar(page)
    _save(page, d, _name("hero-memory", theme))
    _show_sidebar(page)
    page.get_by_role("tab", name="Story", exact=True).click()
    expect(page.get_by_text(summary_line(), exact=True)).to_be_visible()
    _save(page, d, _name("memory-story-ranked", theme))

    export.click()
    page.wait_for_url("**/step4", timeout=30_000)
    expect(page.get_by_role("button", name="Generate Video")).to_be_visible(timeout=30_000)
    _save(page, d, _name("memory-export", theme))

    page.get_by_role("button", name="Back to Generation Options").click()
    page.wait_for_url("**/step3", timeout=30_000)
    expect(page.get_by_role("button", name="Next: Preview & Export")).to_be_visible(timeout=30_000)
    _save(page, d, _name("memory-options", theme))


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_pool_outcomes(page: Page, launch_app_url: str, screenshot_dir: Path, theme: str):
    _open_brief(page, launch_app_url)
    set_theme(page, theme)
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    page.get_by_role("button", name="Review the pool", exact=True).click()
    expect(page.locator(".pool-outcome").first).to_contain_text("In the cut")
    _save(page, screenshot_dir, _name("memory-pool-outcomes", theme))
    page.locator("button").filter(has=page.locator("i:has-text('grid_view')")).click()
    expect(page.locator(".pool-outcome").first).to_contain_text("In the cut")
    _save(page, screenshot_dir, _name("memory-pool-outcomes-grid", theme))


def test_capture_automation_walkthrough(page: Page, launch_app_url: str, launch_workspace):
    from tests.e2e.test_automation_pages import test_choose_generate_and_read_the_same_automatic_run

    test_choose_generate_and_read_the_same_automatic_run(page, launch_app_url, launch_workspace)


# The stock library's swim picture, as a nudity detector that misread it would bank it: the
# false positive the owner clears by hand. Seeded by the recipe, never placed by hand.
_HELD = "trip-swim-02"


def _save_part(page: Page, locator, directory: Path, name: str) -> None:
    """One element of the page (a card, a dialog), redacted like every full frame."""
    locator.scroll_into_view_if_needed()
    # The thumbnail loads lazily: shoot the picture, not its spinner.
    expect(locator.locator(".q-img__loading")).to_have_count(0, timeout=30_000)
    page.mouse.move(0, 0)
    redact_page(page)
    assert_no_real_address(page)
    page.wait_for_timeout(300)
    locator.screenshot(path=str(directory / f"{name}.png"))


def _pool_card(page: Page, asset_id: str):
    """Page the pool to this picture's card; the pool lists the library in capture order."""
    from immich_memories.ui.pages.clip_grid import CLIPS_PER_PAGE
    from tests.e2e.fake_library import BY_ID, LIBRARY

    expect(page.locator(".picture-decision").first).to_be_visible(timeout=60_000)
    index = [picture.asset_id for picture in LIBRARY].index(asset_id)
    for number in range(index // CLIPS_PER_PAGE):
        page.get_by_role("button", name="Next page").click()
        first = (number + 1) * CLIPS_PER_PAGE + 1
        expect(page.get_by_text(re.compile(f"^{first}–"))).to_be_visible()
    card = page.locator(".q-card").filter(has_text=BY_ID[asset_id].filename).first
    expect(card).to_be_visible()
    return card


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_picture_decisions(
    page: Page, launch_app_url: str, launch_workspace, screenshot_dir: Path, theme: str
) -> None:
    """Clear hold and Never use, on the storyboard and in the pool (#1324)."""
    from tests.e2e.fake_library import CARRIERS
    from tests.e2e.test_picture_decisions import flag_by_the_detector, store_of

    store = store_of(launch_workspace)
    flag_by_the_detector(store, _HELD)
    shot, ticked = CARRIERS[1].asset_id, CARRIERS[2].asset_id
    d = screenshot_dir
    try:
        _open_brief(page, launch_app_url)
        set_theme(page, theme)
        _open_brief(page, launch_app_url)
        _choose(page, "Memory type", "Monthly Highlights")
        _choose(page, "Month", "June")
        page.get_by_role("button", name="Cut", exact=True).click()
        shots = page.locator(".storyboard-shot")
        expect(shots.nth(1)).to_be_visible(timeout=120_000)
        shots.nth(1).get_by_role("button", name="Never use").click()
        expect(shots.nth(1).get_by_text("You'll never use this picture.")).to_be_visible()
        page.wait_for_timeout(3500)  # the toast fades
        _save_part(page, shots.nth(1), d, _name("pictures-storyboard-never-use", theme))

        page.get_by_role("button", name="Review the pool", exact=True).click()
        card = _pool_card(page, _HELD)
        expect(card.get_by_text("Held: a nudity detector flagged it.")).to_be_visible()
        _save_part(page, card, d, _name("pictures-pool-held", theme))
        card.get_by_role("button", name="Clear hold").click()
        dialog = page.locator(".clear-hold-dialog")
        expect(dialog.locator("img")).to_be_visible()
        page.wait_for_timeout(600)
        _save_part(page, dialog, d, _name("pictures-clear-dialog", theme))
        dialog.get_by_role("button", name="Clear hold").click()
        expect(card.get_by_text(re.compile("^You cleared its hold"))).to_be_visible()
        page.wait_for_timeout(3500)
        _save_part(page, card, d, _name("pictures-pool-cleared", theme))

        page.get_by_role("button", name="Back to the cut").click()
        page.get_by_role("button", name="Review the pool", exact=True).click()
        other = _pool_card(page, ticked)
        other.get_by_role("button", name="Never use").click()
        expect(other.get_by_role("checkbox", name="Include")).not_to_be_checked()
        page.wait_for_timeout(3500)
        _save_part(page, other, d, _name("pictures-pool-never-use", theme))
    finally:
        from immich_memories.store import owner_decisions

        for asset_id in (_HELD, shot, ticked):
            owner_decisions.forget(store, asset_id)


@pytest.mark.parametrize("theme", _THEMES)
def test_capture_sharing_levels(page: Page, launch_app_url: str, screenshot_dir: Path, theme: str):
    """Who will watch it: the brief's sharing level, open on its three choices (#1325)."""
    _open_brief(page, launch_app_url)
    set_theme(page, theme)
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")
    _choose(page, "Sharing", "Just us")
    expect(page.get_by_text("The household.", exact=False)).to_be_visible()
    _save(page, screenshot_dir, _name("memory-brief-sharing", theme))
