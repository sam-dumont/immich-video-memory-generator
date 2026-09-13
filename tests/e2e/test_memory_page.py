"""The Memory page on the hermetic launch: the brief, the cut, and what it produced."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from immich_memories.ui.pages.clip_grid import CLIPS_PER_PAGE
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from tests.e2e.fake_editorial import _EPISODES, PREVIEW_STAGE, STAGES
from tests.e2e.fake_library import BIG_MONTH, LIBRARY, STORIES, STORY_OF, THESIS
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
    expect(page.get_by_text(f"{len(LIBRARY)} pictures, 0:", exact=False)).to_be_visible()

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
    # Once the run moves on to the edit, nothing new arrives, so the strip goes
    # away instead of lingering under the Editing row.
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    expect(strip).to_have_count(0)


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

    expect(page.get_by_text("6 in the pool (3 videos, 3 photos)", exact=False)).to_be_visible(
        timeout=60_000
    )
    for filename in _POOL_FILES:
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()


def test_the_media_pool_loads_its_pictures_through_the_media_route(
    page: Page, launch_app_url: str
) -> None:
    """No base64 data URI in the DOM: every thumbnail is an <img> the browser fetches and caches."""
    _brief_for_june(page, launch_app_url)
    page.get_by_text("Advanced", exact=True).click()
    page.get_by_role("button", name="Open the media pool").click()
    expect(page.get_by_text("6 in the pool (3 videos, 3 photos)", exact=False)).to_be_visible(
        timeout=60_000
    )

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


# The wide filler month: one clip and forty-one stills, so the pool has to page.
_BIG_POOL = f"{len(BIG_MONTH)} in the pool (1 videos, {len(BIG_MONTH) - 1} photos)"
_PAGE = CLIPS_PER_PAGE


def _open_media_pool(page: Page) -> None:
    page.get_by_text("Advanced", exact=True).click()
    page.get_by_role("button", name="Open the media pool").click()


def _grid_images(page: Page):
    return page.locator(".media-pool-grid img")


def test_the_media_pool_shows_one_page_at_a_time(page: Page, launch_app_url: str) -> None:
    """Paging replaces the page in the DOM instead of appending to it (#824)."""
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "May")
    _open_media_pool(page)
    expect(page.get_by_text(_BIG_POOL, exact=False)).to_be_visible(timeout=60_000)
    total = len(BIG_MONTH)

    expect(_grid_images(page).first).to_be_visible(timeout=30_000)
    expect(page.get_by_text(f"1–{_PAGE} of {total}", exact=True)).to_be_visible()
    assert 0 < _grid_images(page).count() <= _PAGE

    page.get_by_role("button", name="Next page").click()
    expect(page.get_by_text(f"{_PAGE + 1}–{2 * _PAGE} of {total}", exact=True)).to_be_visible()
    assert _grid_images(page).count() <= _PAGE

    page.get_by_role("button", name="Next page").click()
    expect(page.get_by_text(f"{2 * _PAGE + 1}–{total} of {total}", exact=True)).to_be_visible()
    assert _grid_images(page).count() == total - 2 * _PAGE

    page.get_by_role("button", name="Previous page").click()
    expect(page.get_by_text(f"{_PAGE + 1}–{2 * _PAGE} of {total}", exact=True)).to_be_visible()


def test_a_tick_in_the_compact_grid_performs_no_navigation(page: Page, launch_app_url: str) -> None:
    """A toggled cell redraws in place; the page is not reloaded around it (#824)."""
    _brief_for_june(page, launch_app_url)
    _open_media_pool(page)
    expect(page.get_by_text("6 in the pool (3 videos, 3 photos)", exact=False)).to_be_visible(
        timeout=60_000
    )
    # The view toggle itself navigates; let that page settle before planting the marker.
    page.locator("button").filter(has=page.locator("i:has-text('grid_view')")).click()
    page.wait_for_load_state("networkidle")

    cells = page.locator(".media-pool-grid .cursor-pointer")
    expect(cells.first).to_be_visible(timeout=30_000)
    expect(cells.first.locator("i:has-text('check_circle')")).to_have_count(1)
    page.evaluate("window.__still_here = 'yes'")

    cells.first.click()
    expect(cells.first.locator("i:has-text('check_circle')")).to_have_count(0)
    cells.first.click()
    expect(cells.first.locator("i:has-text('check_circle')")).to_have_count(1)
    assert page.evaluate("window.__still_here") == "yes"


def test_review_rows_hold_a_video_only_while_they_are_open(page: Page, launch_app_url: str) -> None:
    """A closed row shows a thumbnail; opening it starts the preview, closing it releases it."""
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    page.get_by_role("button", name="Review the pool").click()
    page.get_by_role("button", name="Trim the video clips").click()

    # Only the videos the cut kept have a row: a still has no seconds to trim; rows are in capture order.
    videos = [p for p in LIBRARY if p.is_video]
    rows = page.locator(".review-clip-row")
    expect(rows).to_have_count(len(videos), timeout=30_000)
    expect(page.locator(".review-clip-row video")).to_have_count(1, timeout=60_000)

    # The fourth picture in capture order is a video; a still would never hold a <video>.
    second_video = 1
    rows.nth(second_video).locator(".q-expansion-item__toggle-icon").first.click()
    expect(page.locator(".review-clip-row video")).to_have_count(2, timeout=60_000)

    rows.nth(second_video).locator(".q-expansion-item__toggle-icon").first.click()
    expect(page.locator(".review-clip-row video")).to_have_count(1, timeout=30_000)


def _latest_request(launch_workspace) -> dict:
    return json.loads((_newest_attempt(launch_workspace) / "status.private.json").read_text())[
        "request"
    ]


def _evidence(page: Page, name: str) -> None:
    """Save a walk-through frame when UX_EVIDENCE_DIR is set (the owner's browser-tested rule)."""
    target = os.environ.get("UX_EVIDENCE_DIR")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def test_a_tick_survives_the_cut_and_cut_again_keeps_the_pool(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    """Untick one picture, cut again: it is out. Tick it back, cut again: it is required, and in."""
    _brief_for_june(page, launch_app_url)
    _evidence(page, "01-brief")
    page.get_by_role("button", name="Cut", exact=True).click()
    story_tab = page.get_by_role("tab", name="Story", exact=True)
    expect(story_tab).to_be_visible(timeout=120_000)
    story_tab.click()
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "02-first-cut-six-pictures")

    # The pool stays reachable after a cut, with the cut's own ticks.
    page.get_by_role("button", name="Review the pool", exact=True).click()
    boxes = page.get_by_role("checkbox", name="Include")
    expect(boxes).to_have_count(6)
    for index in range(6):
        expect(boxes.nth(index)).to_be_checked()
    _evidence(page, "03-pool-after-cut-all-ticked")

    # A NiceGUI checkbox flips after the server round trip: click, then wait for the state.
    boxes.first.click()
    expect(boxes.first).not_to_be_checked()
    _evidence(page, "04-pool-one-unticked")
    page.get_by_role("button", name="Cut again", exact=True).click()
    story_tab = page.get_by_role("tab", name="Story", exact=True)
    expect(story_tab).to_be_visible(timeout=120_000)
    story_tab.click()
    expect(page.get_by_text("3 stories, 5 pictures", exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "05-second-cut-five-pictures")
    request = _latest_request(launch_workspace)
    assert len(request["requested_assets"]) == 5
    assert request["required_assets"] == []

    # Tick the one the cut left out: it is required now, and the next cut carries it.
    page.get_by_role("button", name="Review the pool", exact=True).click()
    boxes = page.get_by_role("checkbox", name="Include")
    expect(boxes).to_have_count(6)
    expect(boxes.first).not_to_be_checked()
    boxes.first.click()
    expect(boxes.first).to_be_checked()
    _evidence(page, "06-pool-ticked-back-in")
    page.get_by_role("button", name="Cut again", exact=True).click()
    story_tab = page.get_by_role("tab", name="Story", exact=True)
    expect(story_tab).to_be_visible(timeout=120_000)
    story_tab.click()
    expect(page.get_by_text("3 stories, 6 pictures", exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "07-third-cut-six-pictures")
    request = _latest_request(launch_workspace)
    assert len(request["required_assets"]) == 1
    assert request["required_assets"][0] in request["requested_assets"]

    page.get_by_role("button", name="Export", exact=True).click()
    expect(page.get_by_text("Preview & Export", exact=True).first).to_be_visible()
    _evidence(page, "08-export-page")
