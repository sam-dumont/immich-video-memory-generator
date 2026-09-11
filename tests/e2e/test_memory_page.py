"""The Memory page on the hermetic launch: the brief, the cut, and what it produced."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from tests.e2e.fake_editorial import STAGES
from tests.e2e.test_launch_smoke import _choose

pytestmark = pytest.mark.e2e

# The scripted editor's thesis and its three stories, from the fixture.
_THESIS = re.compile(r"^A month of short test-pattern captures")
_STORY_TITLES = ("The first captures", "The midmonth captures", "The closing captures")
_POOL_FILES = (
    "video-1.mp4",
    "video-2.mp4",
    "video-3.mp4",
    "photo-1.jpg",
    "photo-2.jpg",
    "photo-3.jpg",
)


# One of the fixture's editing stages, exactly as the active row reports it once the
# attempt exists (the row titles alone never match, so this waits for the real run).
_EDITING_STAGE = re.compile("^(" + "|".join(re.escape(stage) for stage in STAGES[1:]) + ")$")


def _attempts_written(launch_workspace) -> int:
    return len(list(launch_workspace.cache_dir.glob("editorial-runs/*/attempts/*")))


def _open_brief(page: Page, launch_app_url: str) -> None:
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("combobox", name="Memory type")).to_be_visible(timeout=30_000)


def _brief_for_june(page: Page, launch_app_url: str) -> None:
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")


def test_the_brief_offers_the_cli_memory_types(page: Page, launch_app_url: str) -> None:
    _open_brief(page, launch_app_url)

    page.get_by_role("combobox", name="Memory type").click()

    expect(page.get_by_role("option")).to_have_text(list(MEMORY_TYPE_LABELS.values()))
    page.keyboard.press("Escape")


def test_a_cut_from_the_brief_shows_the_story_and_offers_export(
    page: Page, launch_app_url: str
) -> None:
    _brief_for_june(page, launch_app_url)
    expect(page.get_by_text("Auto · 1m 00s", exact=True)).to_be_visible()

    page.get_by_role("button", name="Cut", exact=True).click()

    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    # Weight order, not capture order: the dominant story leads, the glimpse closes.
    titles = page.locator(".q-card .text-base.font-semibold")
    expect(titles).to_have_text(list(_STORY_TITLES))
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible()
    expect(page.get_by_text("Motion", exact=True)).to_have_count(3)
    expect(page.get_by_text("Still", exact=True)).to_have_count(3)
    expect(page.get_by_text(re.compile(r"^\d+ s of pictures and video selected"))).to_be_visible()
    expect(page.get_by_role("button", name="Export", exact=True)).to_be_visible()


def test_a_reload_mid_cut_joins_the_running_cut_instead_of_starting_another(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_EDITING_STAGE)).to_be_visible(timeout=60_000)
    attempts_before = _attempts_written(launch_workspace)

    # WHY: a reload is what a user does when a run seems stuck; it deletes the NiceGUI
    # client, so the new page must find the cut through the attempt tree, not the old timer.
    page.reload(wait_until="domcontentloaded", timeout=30_000)

    expect(page.get_by_text("Cutting the memory...", exact=True)).to_be_visible(timeout=30_000)
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    assert _attempts_written(launch_workspace) == attempts_before


def test_cancel_ends_the_cut_and_offers_to_cut_again(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_EDITING_STAGE)).to_be_visible(timeout=60_000)

    page.get_by_role("button", name="Cancel", exact=True).click()

    expect(page.get_by_text(re.compile(r"cancelled before it finished"))).to_be_visible(
        timeout=60_000
    )
    expect(page.get_by_role("button", name="Cut again")).to_be_visible()


def test_the_media_pool_stays_reachable_from_advanced(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_text("Advanced", exact=True).click()
    include_photos = page.locator(".q-toggle").filter(has_text="Include Photos")
    expect(include_photos).to_have_attribute("aria-checked", "true")

    page.get_by_role("button", name="Open the media pool").click()

    expect(page.get_by_text("3 Videos, 3 Photos Found", exact=True)).to_be_visible(timeout=60_000)
    for filename in _POOL_FILES:
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()
