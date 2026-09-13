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
from tests.e2e.fake_library import THESIS
from tests.e2e.redaction import redact_page
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
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible()
    _save(page, d, _name("memory-story-ranked", theme))

    export.click()
    page.wait_for_url("**/step4", timeout=30_000)
    expect(page.get_by_role("button", name="Generate Video")).to_be_visible(timeout=30_000)
    _save(page, d, _name("memory-export", theme))

    page.get_by_role("button", name="Back to Generation Options").click()
    page.wait_for_url("**/step3", timeout=30_000)
    expect(page.get_by_role("button", name="Next: Preview & Export")).to_be_visible(timeout=30_000)
    _save(page, d, _name("memory-options", theme))
