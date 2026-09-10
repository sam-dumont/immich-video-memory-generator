"""The Memory page on the hermetic launch: the brief, the cut, and what it produced."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from tests.e2e.conftest import enable_demo_mode, set_theme
from tests.e2e.redaction import redact_page
from tests.e2e.test_launch_smoke import _choose

pytestmark = pytest.mark.e2e

# The fixture's six sources, every one of which the scripted editor keeps.
_CUT_COMPLETE = re.compile(r"^Pipeline complete! Planned 6 clips from 6 eligible media items\.$")
_POOL_FILES = (
    "video-1.mp4",
    "video-2.mp4",
    "video-3.mp4",
    "photo-1.jpg",
    "photo-2.jpg",
    "photo-3.jpg",
)


def _open_brief(page: Page, launch_app_url: str) -> None:
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("combobox", name="Memory type")).to_be_visible(timeout=30_000)


def _brief_for_june(page: Page, launch_app_url: str) -> None:
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")


def capture_pair(page: Page, screenshot_dir: Path, name: str) -> None:
    """One light and one dark screenshot for the docs, from synthetic data only."""
    for theme in ("light", "dark"):
        set_theme(page, theme)
        enable_demo_mode(page)
        redact_page(page)
        prefix = "dark-" if theme == "dark" else ""
        page.screenshot(path=str(screenshot_dir / f"{prefix}{name}.png"))
    set_theme(page, "light")


def test_the_brief_offers_the_cli_memory_types(page: Page, launch_app_url: str) -> None:
    _open_brief(page, launch_app_url)

    page.get_by_role("combobox", name="Memory type").click()

    expect(page.get_by_role("option")).to_have_text(list(MEMORY_TYPE_LABELS.values()))
    page.keyboard.press("Escape")


def test_a_cut_from_the_brief_runs_the_editor_and_offers_export(
    page: Page, launch_app_url: str, screenshot_dir: Path
) -> None:
    _brief_for_june(page, launch_app_url)
    expect(page.get_by_text("Auto · 1m 00s", exact=True)).to_be_visible()
    capture_pair(page, screenshot_dir, "memory-brief")

    page.get_by_role("button", name="Cut", exact=True).click()

    expect(page.get_by_text(_CUT_COMPLETE)).to_be_visible(timeout=120_000)
    expect(page.get_by_role("button", name="Export", exact=True)).to_be_visible()


def test_the_media_pool_stays_reachable_from_advanced(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_text("Advanced", exact=True).click()
    include_photos = page.locator(".q-toggle").filter(has_text="Include Photos")
    expect(include_photos).to_have_attribute("aria-checked", "true")

    page.get_by_role("button", name="Open the media pool").click()

    expect(page.get_by_text("3 Videos, 3 Photos Found", exact=True)).to_be_visible(timeout=60_000)
    for filename in _POOL_FILES:
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()
