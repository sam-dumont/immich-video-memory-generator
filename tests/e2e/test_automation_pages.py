"""The scheduler's suggestions and run history are usable from the browser."""

import os

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_suggestions_page_offers_the_discovered_candidates(page, launch_app_url):
    page.goto(f"{launch_app_url}/suggestions")
    expect(page.get_by_text("Suggestions", exact=True).last).to_be_visible()
    expect(page.get_by_role("button", name="Refresh suggestions")).to_be_visible()
    expect(page.get_by_role("button", name="Check eligibility").first).to_be_visible(timeout=60_000)


def test_runs_page_reads_the_existing_database(page, launch_app_url, launch_workspace):
    from datetime import datetime

    from immich_memories.tracking import RunDatabase
    from immich_memories.tracking.models import RunMetadata

    db = RunDatabase(launch_workspace.database_path)
    db.save_run(
        RunMetadata(
            run_id="fixture-history",
            created_at=datetime.now(),
            status="failed",
            memory_type="monthly_highlights",
            warnings=["Fixture provider stopped answering"],
        )
    )
    page.goto(f"{launch_app_url}/runs")
    expect(page.get_by_text("fixture-history", exact=True)).to_be_visible()
    page.get_by_role("link", name="fixture-history", exact=True).click()
    expect(page.get_by_text("Fixture provider stopped answering", exact=True)).to_be_visible()


def test_a_failed_generation_still_offers_the_run_it_started(
    page, launch_app_url, launch_workspace
):
    """A failure is exactly when the run record and its transcript are worth reaching."""
    from tests.e2e.fake_automation import FAIL_MARKER

    marker = launch_workspace.root / "state" / FAIL_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    page.goto(f"{launch_app_url}/suggestions")
    card = page.locator(".suggestion-card").filter(has_text="monthly review")
    expect(card).to_be_visible(timeout=60_000)
    card.get_by_role("button", name="Run this suggestion").click()
    expect(page.get_by_text("Failed:", exact=False)).to_be_visible(timeout=120_000)
    expect(page.get_by_role("link", name="Open run", exact=True)).to_be_visible()
    expect(page.get_by_text("the fixture provider refused", exact=False)).to_be_visible()
    page.get_by_role("link", name="Open run", exact=True).click()
    expect(page.get_by_text("The fixture provider refused the render", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Download child output")).to_be_visible()


def test_choose_generate_and_read_the_same_automatic_run(page, launch_app_url, launch_workspace):
    from pathlib import Path

    from immich_memories.automation.state_store import AutomationStateStore
    from immich_memories.tracking import RunDatabase
    from immich_memories.ui.pages.suggestions import SUGGESTION_REASON
    from tests.e2e.conftest import set_theme
    from tests.e2e.fake_library import CARRIERS, THESIS
    from tests.e2e.test_screenshots import _save

    page.goto(f"{launch_app_url}/suggestions")
    card = page.locator(".suggestion-card").filter(has_text="monthly review")
    expect(card).to_be_visible(timeout=60_000)
    card.get_by_text("Candidate key", exact=True).click()
    expect(
        card.get_by_text("monthly_highlights:2024-06-01:2024-06-30:", exact=True)
    ).to_be_visible()
    card.get_by_role("button", name="Check eligibility").click()
    expect(page.get_by_text("Eligible. No video was generated.", exact=True)).to_be_visible(
        timeout=60_000
    )
    evidence = Path(__file__).resolve().parents[2] / "docs-site/static/img/screenshots"
    _save(page, evidence, "suggestions")
    set_theme(page, "dark")
    expect(card).to_be_visible(timeout=60_000)
    _save(page, evidence, "dark-suggestions")
    set_theme(page, "light")
    expect(card).to_be_visible(timeout=60_000)
    card.get_by_role("button", name="Run this suggestion").click()
    expect(page.get_by_text("Running on the server", exact=False)).to_be_visible(timeout=60_000)
    # A run somebody clicked for is not a nightly wake, and only the live attempt
    # carries that: finishing overwrites the reason with the outcome.
    live = AutomationStateStore(launch_workspace.database_path).get_last_attempt()
    assert live.reason == SUGGESTION_REASON
    expect(page.get_by_role("link", name="Open run", exact=True)).to_be_visible(timeout=660_000)
    page.get_by_role("link", name="Open run", exact=True).click()
    disclosure = page.get_by_text("Read the cut", exact=True)
    # The run-details page reads the saved plan after navigating; every other
    # wait on this page is 60 s, and the 5 s default lost the race on CI.
    expect(disclosure).to_be_visible(timeout=60_000)
    disclosure.click()
    expect(page.get_by_text(THESIS, exact=True)).to_be_hidden()
    disclosure.click()
    expect(page.get_by_text(THESIS, exact=True)).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Download child output").click()
    transcript = Path(download.value.path()).read_text()
    assert f"Selected {len(CARRIERS)} clips" in transcript
    rows = RunDatabase(launch_workspace.database_path).list_runs(status="completed", source="auto")
    assert len(rows) == 1 and rows[0].output_path
    assert Path(rows[0].output_path).is_file()
    assert rows[0].run_id in page.url
    page.evaluate("window.scrollTo(0, 0)")
    _save(page, evidence, "run-details")
    set_theme(page, "dark")
    _save(page, evidence, "dark-run-details")
    set_theme(page, "light")
    page.get_by_role("link", name="Back to runs").click()
    expect(page.get_by_role("link", name=rows[0].run_id, exact=True)).to_be_visible()
    _save(page, evidence, "runs")
    set_theme(page, "dark")
    _save(page, evidence, "dark-runs")
    set_theme(page, "light")
    # A successful child's real generate transcript is also the CLI evidence for this flow.
    target = (
        Path(os.environ.get("UX_EVIDENCE_DIR", str(launch_workspace.root)))
        / "automatic-child-cli.txt"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(transcript.replace(str(launch_workspace.root), "<fixture-workspace>"))


def test_variety_rejections_and_all_sidebar_destinations(page, launch_app_url, launch_workspace):
    from datetime import datetime, timedelta

    from immich_memories.tracking import RunDatabase
    from immich_memories.tracking.models import RunMetadata

    db = RunDatabase(launch_workspace.database_path)
    stamp = datetime.now() + timedelta(seconds=1)
    db.save_run(
        RunMetadata(
            run_id="fixture-variety",
            created_at=stamp,
            completed_at=stamp,
            status="completed",
            source="auto",
            memory_category="person_spotlight",
        )
    )
    page.goto(f"{launch_app_url}/suggestions")
    page.get_by_role("button", name="Refresh suggestions").click()
    skipped = page.get_by_text("Why other suggestions were skipped", exact=True)
    expect(skipped).to_be_visible(timeout=60_000)
    skipped.click()
    expect(
        page.get_by_text("The previous automatic memory used this category.", exact=False).first
    ).to_be_visible()
    sidebar = page.locator(".q-drawer")
    for label, path in [
        ("Memory", "/"),
        ("Runs", "/runs"),
        ("Media pool", "/step2"),
        ("Settings", "/settings/config"),
        ("Suggestions", "/suggestions"),
    ]:
        sidebar.get_by_role("link", name=label, exact=True).click()
        page.wait_for_url(launch_app_url + path)
        if label == "Settings":
            for name, target in [
                ("Configuration", "config"),
                ("People", "people"),
                ("Cache", "cache"),
            ]:
                sidebar.get_by_role("link", name=name, exact=True).click()
                page.wait_for_url(f"{launch_app_url}/settings/{target}")


def test_run_history_filters_and_replaces_pages(page, launch_app_url, launch_workspace):
    from datetime import datetime, timedelta

    from immich_memories.tracking import RunDatabase
    from immich_memories.tracking.models import RunMetadata
    from tests.e2e.test_launch_smoke import _choose

    db = RunDatabase(launch_workspace.database_path)
    for index in range(25):
        db.save_run(
            RunMetadata(
                run_id=f"history-{index:02d}",
                created_at=datetime(2024, 1, 1) + timedelta(minutes=index),
                status="failed",
            )
        )
    page.goto(f"{launch_app_url}/runs")
    _choose(page, "Status", "failed")
    expect(page.locator(".run-row")).to_have_count(20)
    page.get_by_role("button", name="Next runs").click()
    expect(page.get_by_role("button", name="Next runs")).to_be_disabled()
    assert 0 < page.locator(".run-row").count() < 20
    page.get_by_role("button", name="Previous runs").click()
    expect(page.locator(".run-row")).to_have_count(20)
    page.get_by_role("button", name="Refresh runs").click()
    expect(page.locator(".run-row")).to_have_count(20)
