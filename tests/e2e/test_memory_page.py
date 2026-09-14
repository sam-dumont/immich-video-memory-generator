"""The Memory page on the hermetic launch: the brief, the cut, and what it produced."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from itertools import groupby
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from immich_memories.api.person_expression import PersonExpression
from immich_memories.ui.pages.clip_grid import CLIPS_PER_PAGE
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from immich_memories.ui.pages.step1_people import PEOPLE_CONDITION_PANEL
from tests.e2e.conftest import _build_launch_environment
from tests.e2e.fake_editorial import _EPISODES, PREVIEW_STAGE, STAGES
from tests.e2e.fake_library import (
    CARRIERS,
    LIBRARY,
    STORIES,
    STORY_OF,
    THESIS,
    pool_line,
    summary_line,
)
from tests.e2e.test_demo_assets import _TRIP_CLI_BOOTSTRAP
from tests.e2e.test_launch_smoke import _choose

pytestmark = pytest.mark.e2e

# The scripted editor's thesis and its four stories, from the fixture.
_THESIS = THESIS
_STORY_TITLES = tuple(story.title for story in STORIES)
# The pool pages twenty at a time; the first page carries the first pictures of the month.
_POOL_FILES = tuple(picture.filename for picture in LIBRARY[:5])
_SUMMARY = summary_line()
_POOL = pool_line()
_CARRIER_VIDEOS = sum(1 for picture in CARRIERS if picture.is_video)
_CARRIER_STILLS = len(CARRIERS) - _CARRIER_VIDEOS


# One of the fixture's editing stages, exactly as the active row reports it once the
# attempt exists (the row titles alone never match, so this waits for the real run).
_EDITING_STAGE = re.compile("^(" + "|".join(re.escape(stage) for stage in STAGES[1:]) + ")$")

# The reading the editor gave a picture — a badge only the Details disclosure shows.
_STANDINGS = re.compile(r"^(remarkable|maybe)$")

# The Boolean override, and the fixture pictures holding both named faces.
_CONDITION = '"Robin" AND "Kit"'
_CONDITION_LABEL = PersonExpression.parse(_CONDITION).display_label
_CONDITION_ASSETS = {
    picture.asset_id for picture in LIBRARY if {"Robin", "Kit"} <= set(picture.people)
}
# The same two names read the plain way: any one of them is enough.
_EITHER_ASSETS = {picture.asset_id for picture in LIBRARY if {"Robin", "Kit"} & set(picture.people)}


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


def test_the_brief_finds_the_lake_trip_across_photographed_days(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Trip")
    trip = page.get_by_role("combobox", name="Select a trip")
    expect(trip).to_be_visible(timeout=30_000)
    trip.click()
    option = page.get_by_role(
        "option", name=re.compile(r"2024-06-21 to 2024-06-27, 7d, \d+ assets")
    )
    expect(option).to_be_visible()
    option.click()
    expect(page.get_by_role("button", name="Cut", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_role("button", name="Export", exact=True)).to_be_visible(timeout=120_000)
    expected = sum(p.story_key == "S0002" for p in CARRIERS)
    expect(page.locator(".storyboard-shot")).to_have_count(expected)
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            "-c",
            _TRIP_CLI_BOOTSTRAP,
            str(launch_workspace.config_path),
            str(launch_workspace.root / "state"),
            "generate",
            "--memory-type",
            "trip",
            "--year",
            "2024",
            "--trip-index",
            "1",
            "--no-render",
            "--no-music",
            "--quiet",
        ],
        cwd=root,
        env=_build_launch_environment(launch_workspace.root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    transcript = completed.stdout + completed.stderr
    evidence = root / "test-results"
    evidence.mkdir(exist_ok=True)
    (evidence / "trip-cli.txt").write_text(
        transcript.replace(str(launch_workspace.root), "<fixture-workspace>")
    )
    assert completed.returncode == 0, transcript
    assert "2024-06-21 to 2024-06-27" in transcript
    assert "Trip plan complete:" in transcript


@pytest.mark.parametrize("middle_type", ["IMAGE", "VIDEO"])
def test_the_brief_keeps_new_year_trips_whole_and_excludes_buffer_only_trips(
    page: Page, launch_app_url: str, monkeypatch, middle_type: str
) -> None:
    from tests.e2e import fake_immich

    dates = (
        "2023-12-05",
        "2023-12-07",
        "2023-12-30",
        "2023-12-31",
        "2024-01-01",
        "2025-01-05",
        "2025-01-07",
    )
    template = next(a for a in fake_immich.TIMELINE_ASSETS if a["exifInfo"]["city"] == "Annecy")
    home_video = next(
        a
        for a in fake_immich.TIMELINE_ASSETS
        if a["type"] == "VIDEO" and a["exifInfo"]["city"] == "Brussels"
    )
    assets = tuple(
        {
            **template,
            "id": f"new-year-{i}",
            "type": middle_type if i == 3 else "IMAGE",
            "fileCreatedAt": f"{day}T12:00:00.000Z",
        }
        for i, day in enumerate(dates)
    )
    # WHY: replace the HTTP fixture's library, keeping the real client and GPS detector.
    monkeypatch.setattr(fake_immich, "TIMELINE_ASSETS", (*assets, home_video))
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Trip")
    trip = page.get_by_role("combobox", name="Select a trip")
    expect(trip).to_be_visible(timeout=30_000)
    trip.click()
    expect(page.get_by_role("option")).to_have_count(1)
    expect(page.get_by_role("option")).to_have_text(
        re.compile(r"2023-12-30 to 2024-01-01, 3d, 3 assets")
    )
    page.keyboard.press("Escape")


@pytest.mark.parametrize("only_photos", [False, True])
def test_the_trip_picker_offers_a_year_with_only_photos(
    page: Page, launch_app_url: str, monkeypatch, only_photos: bool
) -> None:
    from tests.e2e import fake_immich

    template = next(a for a in fake_immich.TIMELINE_ASSETS if a["exifInfo"]["city"] == "Annecy")
    photos = tuple(
        {
            **template,
            "id": f"photo-year-{day}",
            "type": "IMAGE",
            "fileCreatedAt": f"2018-07-0{day}T12:00:00.000Z",
        }
        for day in (1, 2, 3)
    )
    # WHY: cover both a photo-only year in a mixed library and a photo-only library.
    existing = () if only_photos else fake_immich.TIMELINE_ASSETS
    monkeypatch.setattr(fake_immich, "TIMELINE_ASSETS", (*existing, *photos))
    _open_brief(page, launch_app_url)
    _choose(page, "Memory type", "Trip")
    _choose(page, "Year", "2018")
    trip = page.get_by_role("combobox", name="Select a trip")
    expect(trip).to_be_visible(timeout=30_000)
    trip.click()
    expect(page.get_by_role("option")).to_have_text(
        re.compile(r"2018-07-01 to 2018-07-03, 3d, 3 assets")
    )
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
    expect(page.get_by_text(_SUMMARY, exact=True)).to_be_visible()
    expect(page.get_by_text("Motion", exact=True)).to_have_count(_CARRIER_VIDEOS)
    expect(page.get_by_text("Still", exact=True)).to_have_count(_CARRIER_STILLS)
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
    expect(shots).to_have_count(len(CARRIERS))
    # The day is printed once, on the first shot of each day.
    expect(page.locator(".storyboard-shot .storyboard-day")).to_have_text(
        [day for day, _ in groupby(picture.taken_at[:10] for picture in CARRIERS)]
    )
    expect(page.locator(".storyboard-shot .storyboard-story")).to_have_text(
        [STORY_OF[picture.asset_id].title for picture in CARRIERS]
    )
    expect(page.locator(".storyboard-chapter")).to_have_text(["June 2024"])
    expect(page.get_by_text(f"{len(CARRIERS)} pictures, ", exact=False)).to_be_visible()

    # The weighed story is one tab away and comes back the same way.
    page.get_by_role("tab", name="Story", exact=True).click()
    expect(page.get_by_text(_SUMMARY, exact=True)).to_be_visible()
    page.get_by_role("tab", name="Storyboard", exact=True).click()
    expect(shots).to_have_count(len(CARRIERS))


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
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    """The wait has to look alive: the user's own library goes past, and a bar moves."""
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()

    strip = page.locator(".q-img").locator("visible=true")
    expect(strip.first).to_be_visible(timeout=60_000)
    # A real bar for the pass that reports numbers, from the engine's own count.
    expect(
        page.get_by_text(
            re.compile(rf"^{PREVIEW_STAGE} \d+ of {len(LIBRARY)} · ~\d+s left in this stage$")
        )
    ).to_be_visible(timeout=60_000)
    # Bounded by construction: a long stage must not grow the page.
    expect(page.locator(".q-linear-progress")).to_have_count(1)
    assert strip.count() <= 12
    # Once the run moves on to the edit, nothing new arrives, so the strip goes
    # away instead of lingering under the Editing row.
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    expect(strip).to_have_count(0)
    expect(page.get_by_text(re.compile("left in this stage"))).to_be_hidden()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    expect(page.locator(".storyboard-shot")).to_have_count(len(CARRIERS))
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            "-c",
            _TRIP_CLI_BOOTSTRAP,
            str(launch_workspace.config_path),
            str(launch_workspace.root / "state"),
            "generate",
            "--memory-type",
            "monthly_highlights",
            "--year",
            "2024",
            "--month",
            "6",
            "--no-render",
            "--no-music",
            "--quiet",
        ],
        cwd=root,
        env=_build_launch_environment(launch_workspace.root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    transcript = completed.stdout + completed.stderr
    evidence = root / "test-results"
    evidence.mkdir(exist_ok=True)
    (evidence / "stage-progress-cli.txt").write_text(
        transcript.replace(str(launch_workspace.root), "<fixture-workspace>")
    )
    assert completed.returncode == 0, transcript
    assert "left in this stage" in transcript
    assert f"Selected {len(CARRIERS)} clips" in transcript


def test_the_detail_lines_are_folded_away_until_asked_for(page: Page, launch_app_url: str) -> None:
    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)
    # The clean five-row view is the default: the lines exist but are not shown.
    a_preview_line = page.get_by_text(
        re.compile(rf"^Preparing {PREVIEW_STAGE}: \d+/{len(LIBRARY)}$")
    )
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

    expect(page.get_by_text(_POOL, exact=False)).to_be_visible(timeout=60_000)
    for filename in _POOL_FILES:
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()


def test_the_media_pool_loads_its_pictures_through_the_media_route(
    page: Page, launch_app_url: str
) -> None:
    """No base64 data URI in the DOM: every thumbnail is an <img> the browser fetches and caches."""
    _brief_for_june(page, launch_app_url)
    page.get_by_text("Advanced", exact=True).click()
    page.get_by_role("button", name="Open the media pool").click()
    expect(page.get_by_text(_POOL, exact=False)).to_be_visible(timeout=60_000)

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
    per_story = Counter(picture.story_key for picture in CARRIERS)
    for count, stories in Counter(per_story.values()).items():
        expect(page.get_by_text(f"{count} pictures", exact=True)).to_have_count(stories)
    for machine_word in ("dominant", "remarkable", "maybe"):
        expect(page.get_by_text(machine_word, exact=True).first).to_be_hidden()

    lead = page.locator(".q-card").filter(has_text=_STORY_TITLES[0]).first
    lead.get_by_text("Details", exact=True).click()

    expect(lead.get_by_text("dominant", exact=True)).to_be_visible()
    expect(lead.get_by_text(_STANDINGS).first).to_be_visible()


_PAGE = CLIPS_PER_PAGE


def _open_media_pool(page: Page) -> None:
    page.get_by_text("Advanced", exact=True).click()
    page.get_by_role("button", name="Open the media pool").click()


def _grid_images(page: Page):
    return page.locator(".media-pool-grid img")


def test_the_media_pool_shows_one_page_at_a_time(page: Page, launch_app_url: str) -> None:
    """Paging replaces the page in the DOM instead of appending to it (#824)."""
    _brief_for_june(page, launch_app_url)
    _open_media_pool(page)
    expect(page.get_by_text(_POOL, exact=False)).to_be_visible(timeout=60_000)
    total = len(LIBRARY)
    pages = -(-total // _PAGE)

    expect(_grid_images(page).first).to_be_visible(timeout=30_000)
    expect(page.get_by_text(f"1–{_PAGE} of {total}", exact=True)).to_be_visible()
    assert 0 < _grid_images(page).count() <= _PAGE

    page.get_by_role("button", name="Next page").click()
    expect(page.get_by_text(f"{_PAGE + 1}–{2 * _PAGE} of {total}", exact=True)).to_be_visible()
    assert _grid_images(page).count() <= _PAGE

    for index in range(2, pages):
        page.get_by_role("button", name="Next page").click()
        last = total if index == pages - 1 else (index + 1) * _PAGE
        expect(
            page.get_by_text(f"{index * _PAGE + 1}–{last} of {total}", exact=True)
        ).to_be_visible()
    assert _grid_images(page).count() == total - (pages - 1) * _PAGE

    page.get_by_role("button", name="Previous page").click()
    expect(
        page.get_by_text(f"{(pages - 2) * _PAGE + 1}–{(pages - 1) * _PAGE} of {total}", exact=True)
    ).to_be_visible()


def test_a_tick_in_the_compact_grid_performs_no_navigation(page: Page, launch_app_url: str) -> None:
    """A toggled cell redraws in place; the page is not reloaded around it (#824)."""
    _brief_for_june(page, launch_app_url)
    _open_media_pool(page)
    expect(page.get_by_text(_POOL, exact=False)).to_be_visible(timeout=60_000)
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
    videos = [p for p in CARRIERS if p.is_video]
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


def _attempts(launch_workspace) -> set[Path]:
    return set(launch_workspace.cache_dir.glob("editorial-runs/*/attempts/*"))


def _request_of_the_cut_after(launch_workspace, before: set[Path]) -> dict:
    """The request this cut wrote, not one an earlier cut is still writing into.

    A test that stops watching a run leaves it going, so the newest attempt on
    disk belongs to whichever cut last touched a file. The attempt that was not
    there before the click is the one this cut opened.
    """
    written = _attempts(launch_workspace) - before
    assert written, "the cut opened no attempt"
    newest = max(written, key=lambda path: path.stat().st_mtime)
    return json.loads((newest / "status.private.json").read_text())["request"]


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
    expect(page.get_by_text(_SUMMARY, exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "02-first-cut")

    # The pool stays reachable after a cut, with the cut's own ticks: the first page
    # of the pool opens on the first picture of the month, which the cut kept.
    page.get_by_role("button", name="Review the pool", exact=True).click()
    boxes = page.get_by_role("checkbox", name="Include")
    expect(boxes).to_have_count(min(_PAGE, len(LIBRARY)))
    assert LIBRARY[0].shipped
    expect(boxes.first).to_be_checked()
    _evidence(page, "03-pool-after-cut")

    # A NiceGUI checkbox flips after the server round trip: click, then wait for the state.
    boxes.first.click()
    expect(boxes.first).not_to_be_checked()
    _evidence(page, "04-pool-one-unticked")
    page.get_by_role("button", name="Cut again", exact=True).click()
    story_tab = page.get_by_role("tab", name="Story", exact=True)
    expect(story_tab).to_be_visible(timeout=120_000)
    story_tab.click()
    one_fewer = _SUMMARY.replace(f"{len(CARRIERS)} pictures", f"{len(CARRIERS) - 1} pictures")
    expect(page.get_by_text(one_fewer, exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "05-second-cut-one-fewer")
    # After a cut the pool's ticks are the cut's own, so the next request carries
    # the cut minus the one picture that was unticked.
    request = _latest_request(launch_workspace)
    assert len(request["requested_assets"]) == len(CARRIERS) - 1
    assert request["required_assets"] == []

    # Tick the one the cut left out: it is required now, and the next cut carries it.
    page.get_by_role("button", name="Review the pool", exact=True).click()
    boxes = page.get_by_role("checkbox", name="Include")
    expect(boxes).to_have_count(min(_PAGE, len(LIBRARY)))
    expect(boxes.first).not_to_be_checked()
    boxes.first.click()
    expect(boxes.first).to_be_checked()
    _evidence(page, "06-pool-ticked-back-in")
    page.get_by_role("button", name="Cut again", exact=True).click()
    story_tab = page.get_by_role("tab", name="Story", exact=True)
    expect(story_tab).to_be_visible(timeout=120_000)
    story_tab.click()
    expect(page.get_by_text(_SUMMARY, exact=True)).to_be_visible(timeout=120_000)
    _evidence(page, "07-third-cut")
    request = _latest_request(launch_workspace)
    assert len(request["required_assets"]) == 1
    assert request["required_assets"][0] in request["requested_assets"]

    page.get_by_role("button", name="Export", exact=True).click()
    expect(page.get_by_text("Preview & Export", exact=True).first).to_be_visible()
    _evidence(page, "08-export-page")


def test_the_people_condition_waits_under_advanced_and_still_reaches_the_cut(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    """Quoted names are the override, not the first screen, and they still narrow a cut (#887)."""
    _brief_for_june(page, launch_app_url)
    condition = page.get_by_label("Grouped people condition (optional)")
    expect(page.get_by_label("Only with (optional)")).to_be_visible()
    expect(condition).to_be_hidden()
    _evidence(page, "01-brief-condition-folded-away")

    page.get_by_text(PEOPLE_CONDITION_PANEL, exact=True).click()

    expect(condition).to_be_visible()
    condition.fill(_CONDITION)
    expect(page.get_by_text(f"Active condition: {_CONDITION_LABEL}", exact=True)).to_be_visible()
    _evidence(page, "02-condition-typed-under-advanced")

    before = _attempts(launch_workspace)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)

    request = _request_of_the_cut_after(launch_workspace, before)
    assert set(request["requested_assets"]) == _CONDITION_ASSETS


def test_the_picker_says_together_or_any_of_these_people_without_a_condition(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    """Two names and one word for what they mean is the whole plain path (#887)."""
    _brief_for_june(page, launch_app_url)
    any_of = page.get_by_role("button", name="Any of these people")
    expect(any_of).to_be_hidden()

    people = page.get_by_role("combobox", name="Only with (optional)")
    people.click()
    for name in ("Robin", "Kit"):
        page.get_by_role("option", name=name, exact=True).click()
    page.keyboard.press("Escape")

    # One name means nothing to choose between; the second is what raises the question.
    expect(any_of).to_be_visible()
    any_of.click()
    _evidence(page, "03-together-or-any-of-these-people")

    before = _attempts(launch_workspace)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(_active_stage(page)).to_be_visible(timeout=60_000)

    requested = set(_request_of_the_cut_after(launch_workspace, before)["requested_assets"])
    assert requested == _EITHER_ASSETS
    assert requested > _CONDITION_ASSETS


def test_pool_outcomes_match_saved_cut_and_survive_ticks(
    page: Page, launch_app_url, launch_workspace
):
    from immich_memories.cli._runs_reading import why_text
    from immich_memories.operations.candidate_fates import CandidateFates

    _brief_for_june(page, launch_app_url)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_role("tab", name="Story", exact=True)).to_be_visible(timeout=120_000)
    page.get_by_role("button", name="Review the pool", exact=True).click()
    fates = CandidateFates.read(_newest_attempt(launch_workspace))
    labels = page.locator(".pool-outcome")
    expect(labels).to_have_count(min(_PAGE, len(LIBRARY)))
    assert labels.all_text_contents() == [fates.describe(p.asset_id) for p in LIBRARY[:_PAGE]]
    before = labels.first.inner_text()
    page.get_by_role("checkbox", name="Include").first.click()
    expect(labels.first).to_have_text(before)
    _evidence(page, "824-pool-outcomes-list")
    page.locator("button").filter(has=page.locator("i:has-text('grid_view')")).click()
    expect(labels).to_have_count(min(_PAGE, len(LIBRARY)))
    page.locator(".media-pool-grid .cursor-pointer").first.click()
    expect(labels.first).to_have_text(before)
    _evidence(page, "824-pool-outcomes-grid")
    assert fates.trace is not None
    dropped = next(p for p in LIBRARY[:_PAGE] if not p.shipped)
    assert dropped.drop_reason in why_text(
        dropped.asset_id, fates.trace.story_of(dropped.asset_id), fates.board
    )
    assert dropped.drop_reason in fates.describe(dropped.asset_id)

    # Run the same month through the real Click entry point with the fixture editor.
    from tests.e2e.conftest import _build_launch_environment
    from tests.e2e.test_demo_assets import _TRIP_CLI_BOOTSTRAP

    root = Path(__file__).resolve().parents[2]
    prefix = [
        str(root / ".venv/bin/python"),
        "-c",
        _TRIP_CLI_BOOTSTRAP,
        str(launch_workspace.config_path),
        str(launch_workspace.root / "state"),
    ]
    transcript = []
    commands = [
        [
            "generate",
            "--memory-type",
            "monthly_highlights",
            "--year",
            "2024",
            "--month",
            "6",
            "--no-render",
            "--no-music",
            "--quiet",
        ],
        ["runs", "why", dropped.asset_id, "--run", str(_newest_attempt(launch_workspace))],
    ]
    for command in commands:
        if command[0] == "runs":
            command[-1] = str(_newest_attempt(launch_workspace))
        result = subprocess.run(
            prefix + command,
            cwd=root,
            env=_build_launch_environment(launch_workspace.root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        transcript.append(result.stdout + result.stderr)
        assert result.returncode == 0, transcript[-1]
    cli_fates = CandidateFates.read(_newest_attempt(launch_workspace))
    assert cli_fates.board is not None and fates.board is not None
    assert [s.asset_id for s in cli_fates.board.shots] == [s.asset_id for s in fates.board.shots]
    assert dropped.drop_reason in " ".join(transcript[-1].split())
    evidence = root / "test-results"
    evidence.mkdir(exist_ok=True)
    (evidence / "pool-outcomes-cli.txt").write_text(
        "\n".join(transcript).replace(str(launch_workspace.root), "<fixture-workspace>")
    )
