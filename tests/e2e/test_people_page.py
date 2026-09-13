"""Settings → People on the hermetic launch: the roster, the answers, and no reloads (#824, S7).

The roster the page shows comes from the people file under the launch
workspace's HOME, never the developer's own: the test writes that file itself
and checks the names it wrote are the names on the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

_ROSTER = 34


def _entry(index: int) -> dict:
    return {
        "ids": [f"fake-person-{index:02d}"],
        "name": f"Fixture Person {index:02d}",
        "birth_date": None,
        "inferred": {
            "tier": "inner" if index < 4 else "recurring",
            "counts_reliable": True,
            "evidence": {
                "count": 400 - index,
                "active_months": 12,
                "first_month": "2019-01",
                "last_month": "2021-06",
                "span_years": 2.4,
                "onset": "2019-03",
                "concentration": 8.3,
                "continuity": 0.4,
            },
            "links": [],
        },
        "confirmed": {"role": None, "links": [], "notes": None},
    }


def _write_people_file(root: Path) -> Path:
    path = root / ".immich-memories" / "people.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "people": [_entry(index) for index in range(_ROSTER)]},
            sort_keys=False,
        )
    )
    return path


def _open_people(page: Page, launch_app_url: str, launch_workspace) -> Path:
    path = _write_people_file(launch_workspace.root)
    page.goto(f"{launch_app_url}/settings/people", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator(".roster-pager")).to_be_visible(timeout=30_000)
    return path


def _cards(page: Page):
    return page.locator(".roster-card")


def test_the_roster_reads_the_workspace_file_twenty_to_a_page(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _open_people(page, launch_app_url, launch_workspace)

    expect(page.get_by_text("Showing 1–20 of 34")).to_be_visible()
    expect(_cards(page)).to_have_count(20)
    expect(_cards(page).first).to_contain_text("Fixture Person 00")
    assert page.locator('img[src^="data:"]').count() == 0, "no face is inlined as a data URI"
    faces = page.locator('img[src^="/media/person/"]')
    assert faces.count() == 20
    expect(faces.first).to_have_js_property("naturalWidth", 128, timeout=15_000)

    page.get_by_role("button", name="Next").click()

    expect(page.get_by_text("Showing 21–34 of 34")).to_be_visible()
    expect(_cards(page)).to_have_count(14)
    expect(_cards(page).last).to_contain_text("Fixture Person 33")


def test_a_role_and_a_note_are_saved_without_reloading_the_page(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    path = _open_people(page, launch_app_url, launch_workspace)
    page.evaluate("window.__s7_same_document = true")
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_text("Showing 21–34 of 34")).to_be_visible()
    last = _cards(page).last
    last.scroll_into_view_if_needed()
    scrolled = page.evaluate("window.scrollY")
    assert scrolled > 0

    role = last.get_by_role("combobox", name="Role")
    role.click()
    role.fill("godparent")
    role.press("Enter")
    expect(page.get_by_text("Fixture Person 33: godparent")).to_be_visible(timeout=10_000)

    notes = last.get_by_label("Notes")
    notes.fill("lives abroad")
    page.wait_for_timeout(1_500)

    assert page.evaluate("window.__s7_same_document") is True, "the page was reloaded"
    assert page.evaluate("window.scrollY") > 0, "the page lost its place"
    expect(page.get_by_text("Showing 21–34 of 34")).to_be_visible()
    saved = yaml.safe_load(path.read_text())
    person = next(entry for entry in saved["people"] if entry["ids"] == ["fake-person-33"])
    assert person["confirmed"]["role"] == "godparent"
    assert person["confirmed"]["notes"] == "lives abroad"


def test_adding_a_person_redraws_the_roster_in_place(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    path = _open_people(page, launch_app_url, launch_workspace)
    page.evaluate("window.__s7_same_document = true")

    page.get_by_role("button", name="Add someone not in Immich").click()
    page.get_by_label("Full name").fill("Off Camera Uncle")
    page.get_by_role("button", name="Add person").click()

    expect(page.get_by_text("Showing 1–20 of 35")).to_be_visible(timeout=10_000)
    assert page.evaluate("window.__s7_same_document") is True, "the page was reloaded"
    saved = yaml.safe_load(path.read_text())
    assert any(entry["name"] == "Off Camera Uncle" for entry in saved["people"])
