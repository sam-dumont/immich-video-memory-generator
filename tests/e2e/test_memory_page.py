"""The Memory page on the hermetic launch: the brief, the cut, and what it produced."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from tests.e2e.fake_editorial import _EPISODES, PREVIEW_STAGE, STAGES
from tests.e2e.fake_library import LIBRARY, STORIES, STORY_OF, THESIS
from tests.e2e.test_launch_smoke import _choose

pytestmark = pytest.mark.e2e

# The scripted editor's thesis and its three stories, from the fixture.
_THESIS = THESIS
_STORY_TITLES = tuple(story.title for story in STORIES)
_POOL_FILES = tuple(picture.filename for picture in LIBRARY)


# One of the fixture's editing stages, exactly as the active row reports it once the
# attempt exists (the row titles alone never match, so this waits for the real run).
_EDITING_STAGE = re.compile("^(" + "|".join(re.escape(stage) for stage in STAGES[1:]) + ")$")

# The reading the editor gave a picture — a badge only the Details disclosure shows.
_STANDINGS = re.compile(r"^(remarkable|maybe)$")


def _active_stage(page: Page):
    """The stage on the active phase row, not the same string echoed in the detail panel."""
    return page.locator(".cut-phase-rows").get_by_text(_EDITING_STAGE)


def _attempts_written(launch_workspace) -> int:
    return len(list(launch_workspace.cache_dir.glob("editorial-runs/*/attempts/*")))


def _newest_attempt(launch_workspace):
    return max(
        launch_workspace.cache_dir.glob("editorial-runs/*/attempts/*"),
        key=lambda path: path.stat().st_mtime,
    )


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
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _brief_for_june(page, launch_app_url)
    expect(page.get_by_text("Auto · 1m 00s", exact=True)).to_be_visible()

    page.get_by_role("button", name="Cut", exact=True).click()

    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    page.get_by_role("tab", name="Story", exact=True).click()
    # Weight order, not capture order: the heaviest story leads, the lightest closes.
    titles = page.locator(".q-card .text-base.font-semibold")
    expect(titles).to_have_text(list(_STORY_TITLES))
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible()
    expect(page.get_by_text("Motion", exact=True)).to_have_count(3)
    expect(page.get_by_text("Still", exact=True)).to_have_count(3)
    expect(page.get_by_text(re.compile(r"^\d+ s of pictures and video selected"))).to_be_visible()
    expect(page.get_by_role("button", name="Export", exact=True)).to_be_visible()
    # The attempt keeps what each story was read from, so a later drift can be diffed.
    provenance = json.loads(
        (_newest_attempt(launch_workspace) / "evidence-hashes.json").read_text()
    )
    assert [episode["group_id"] for episode in provenance["episodes"]] == [
        episode["key"] for episode in _EPISODES
    ]
    assert "IMG_" not in json.dumps(provenance)


def test_the_storyboard_is_the_default_view_and_plays_in_capture_order(
    page: Page, launch_app_url: str
) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)

    # The storyboard is what opens: one shot per picture, in the order the video plays them.
    shots = page.locator(".storyboard-shot")
    expect(shots).to_have_count(len(LIBRARY))
    expect(page.locator(".storyboard-shot .storyboard-day")).to_have_text(
        [picture.taken_at[:10] for picture in LIBRARY]
    )
    expect(page.locator(".storyboard-shot .storyboard-story")).to_have_text(
        [STORY_OF[picture.asset_id].title for picture in LIBRARY]
    )
    expect(page.locator(".storyboard-chapter")).to_have_text(["June 2024"])
    expect(page.get_by_text(f"{len(LIBRARY)} shots, 0:", exact=False)).to_be_visible()

    # The weighed story is one tab away and comes back the same way.
    page.get_by_role("tab", name="Story", exact=True).click()
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Storyboard", exact=True).click()
    expect(shots).to_have_count(len(LIBRARY))


def test_a_reload_mid_cut_joins_the_running_cut_instead_of_starting_another(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    attempts_before = _attempts_written(launch_workspace)

    # WHY: a reload is what a user does when a run seems stuck; it deletes the NiceGUI
    # client, so the new page must find the cut through the attempt tree, not the old timer.
    page.reload(wait_until="domcontentloaded", timeout=30_000)

    expect(page.get_by_text("Cutting the memory...", exact=True)).to_be_visible(timeout=30_000)
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    assert _attempts_written(launch_workspace) == attempts_before


def test_the_cut_shows_the_pictures_it_is_working_on_while_it_works(
    page: Page, launch_app_url: str
) -> None:
    """The wait has to look alive: the user's own library goes past, and a bar moves."""
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()

    strip = page.locator(".q-img").locator("visible=true")
    expect(strip.first).to_be_visible(timeout=60_000)
    # A real bar for the pass that reports numbers, from the engine's own count.
    expect(page.get_by_text(re.compile(rf"^{PREVIEW_STAGE} \d+ of 6$"))).to_be_visible(
        timeout=60_000
    )
    # Bounded by construction: a long stage must not grow the page.
    expect(page.locator(".q-linear-progress")).to_have_count(1)
    assert strip.count() <= 12
    # The pictures stay while the run moves on to the stages that count nothing.
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    expect(strip.first).to_be_visible()


def test_the_detail_lines_are_folded_away_until_asked_for(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    # The clean five-row view is the default: the lines exist but are not shown.
    a_preview_line = page.get_by_text(re.compile(rf"^Preparing {PREVIEW_STAGE}: \d+/6$"))
    expect(a_preview_line.first).to_be_hidden()

    page.get_by_text("Details", exact=True).click()

    # The engine's own stage strings, newest last, including ones already passed.
    expect(a_preview_line.first).to_be_visible()
    expect(page.get_by_text(STAGES[0], exact=True).last).to_be_visible()


def test_cancel_ends_the_cut_and_offers_to_cut_again(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)

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


def test_the_media_pool_loads_its_pictures_through_the_media_route(
    page: Page, launch_app_url: str
) -> None:
    """No base64 data URI in the DOM: every thumbnail is an <img> the browser fetches and caches."""
    _brief_for_june(page, launch_app_url)
    page.get_by_text("Advanced", exact=True).click()
    page.get_by_role("button", name="Open the media pool").click()
    expect(page.get_by_text("3 Videos, 3 Photos Found", exact=True)).to_be_visible(timeout=60_000)

    routed = page.locator("img[src^='/media/thumb/']")
    expect(routed.first).to_be_visible(timeout=30_000)
    assert page.locator("img[src^='data:']").count() == 0
    assert routed.count() >= len(_POOL_FILES)
    loaded = page.evaluate(
        "() => Array.from(document.querySelectorAll(\"img[src^='/media/thumb/']\"))"
        ".filter(img => img.complete && img.naturalWidth > 0).length"
    )
    assert loaded == routed.count(), "every routed thumbnail decoded in the browser"


def test_the_story_reads_in_reader_words_and_hides_the_answer_schema_behind_details(
    page: Page, launch_app_url: str
) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    page.get_by_role("tab", name="Story", exact=True).click()

    for badge in ("Main story", "Important", "Small moment"):
        expect(page.get_by_text(badge, exact=True)).to_be_visible()
    expect(page.get_by_text("2 pictures", exact=True)).to_have_count(3)
    for machine_word in ("dominant", "remarkable", "maybe"):
        expect(page.get_by_text(machine_word, exact=True).first).to_be_hidden()

    lead = page.locator(".q-card").filter(has_text=_STORY_TITLES[0]).first
    lead.get_by_text("Details", exact=True).click()

    expect(lead.get_by_text("dominant", exact=True)).to_be_visible()
    expect(lead.get_by_text(_STANDINGS).first).to_be_visible()
