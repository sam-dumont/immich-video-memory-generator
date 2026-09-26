"""The owner's word on one picture, from the storyboard, the pool and the terminal (#1324)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from immich_memories.config_loader import Config
from immich_memories.store import owner_decisions
from tests.e2e.conftest import _build_launch_environment
from tests.e2e.fake_library import CARRIERS, LIBRARY
from tests.e2e.test_demo_assets import _TRIP_CLI_BOOTSTRAP
from tests.e2e.test_memory_page import _brief_for_june

pytestmark = pytest.mark.e2e

_ROOT = Path(__file__).resolve().parents[2]


def store_of(launch_workspace) -> Path:
    return launch_workspace.cache_dir / "annotations.sqlite"


def flag_by_the_detector(store: Path, asset_id: str) -> None:
    """Bank a nudity-detector `yes` for one stock picture, as ingest would."""
    owner_decisions.forget(store, "nobody")  # the store and its schema
    version = Config().editorial.head_versions["nsfw_marqo"]
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO head_facts (asset_id, head, version, label) "
            "VALUES (?, 'nsfw_marqo', ?, 'yes')",
            (asset_id, version),
        )


def _frame(locator, name: str) -> None:
    """One element of the walk, when UX_EVIDENCE_DIR is set; the full page loses a dialog."""
    target = os.environ.get("UX_EVIDENCE_DIR")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(Path(target) / f"{name}.png"))


def _cli(launch_workspace, *args: str) -> str:
    result = subprocess.run(
        [
            str(_ROOT / ".venv/bin/python"),
            "-c",
            _TRIP_CLI_BOOTSTRAP,
            str(launch_workspace.config_path),
            str(launch_workspace.root / "state"),
            "pictures",
            *args,
        ],
        cwd=_ROOT,
        env=_build_launch_environment(launch_workspace.root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return f"$ immich-memories pictures {' '.join(args)}\n{result.stdout}"


def test_never_use_from_the_storyboard_and_clear_a_hold_from_the_pool(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    store = store_of(launch_workspace)
    shot = CARRIERS[0].asset_id
    left_out = next(picture for picture in LIBRARY if picture not in CARRIERS)
    held = left_out.asset_id
    flag_by_the_detector(store, held)
    try:
        _brief_for_june(page, launch_app_url)
        page.get_by_role("button", name="Cut", exact=True).click()
        shots = page.locator(".storyboard-shot")
        expect(shots).to_have_count(len(CARRIERS), timeout=120_000)

        page.get_by_role("button", name="Picture decisions", exact=True).click()
        decisions = page.get_by_role("dialog")
        decisions.get_by_role("button", name="Never use").click()
        expect(decisions.get_by_text("You'll never use this picture.")).to_be_visible()
        expect(decisions.get_by_role("button", name="Undo")).to_be_visible()
        assert owner_decisions.decisions(store, [shot]) == {shot: owner_decisions.NEVER_USE}
        _frame(decisions, "1324-storyboard-never-use")
        decisions.get_by_role("button", name="Close", exact=True).click()

        page.get_by_role("button", name="Review the pool", exact=True).click()
        card = page.locator(".q-card").filter(has_text=left_out.filename[:17]).first
        expect(card.get_by_text("Held: a nudity detector flagged it.")).to_be_visible(
            timeout=60_000
        )
        card.get_by_role("button", name="Clear hold").click()
        dialog = page.locator(".clear-hold-dialog")
        expect(dialog.get_by_text("Clear this picture's hold?")).to_be_visible()
        _frame(card, "1324-pool-held")
        _frame(dialog, "1324-pool-clear-hold-dialog")

        dialog.get_by_role("button", name="Cancel").click()
        assert owner_decisions.decisions(store, [held]) == {}, "cancel writes nothing"

        card.get_by_role("button", name="Clear hold").click()
        # The dialog asks how far it may go, the family by default (#1325).
        expect(dialog.get_by_role("radio", name="Family: family films too")).to_be_checked()
        dialog.get_by_role("radio", name="Just us: only films for the household").click()
        dialog.get_by_role("button", name="Clear hold").click()
        expect(
            card.get_by_text("You cleared its hold for just us (a nudity detector flagged it).")
        ).to_be_visible()
        expect(card.get_by_role("button", name="Clear hold")).to_have_count(0)
        assert owner_decisions.decisions(store, [held]) == {held: "cleared_just_us"}
        _frame(card, "1324-pool-cleared")

        # Ruling out a ticked picture unticks it: the pool never says both.
        ticked = page.locator(".q-card").filter(has_text=CARRIERS[1].filename[:17]).first
        expect(ticked.get_by_role("checkbox", name="Include")).to_be_checked()
        ticked.get_by_role("button", name="Never use").click()
        expect(ticked.get_by_text("You'll never use this picture.")).to_be_visible()
        expect(ticked.get_by_role("checkbox", name="Include")).not_to_be_checked()
        _frame(ticked, "1324-pool-never-use-unticks")
        ticked.get_by_role("button", name="Undo").click()
        expect(ticked.get_by_role("button", name="Never use")).to_be_visible()
        assert owner_decisions.decisions(store, [CARRIERS[1].asset_id]) == {}

        transcript = [
            _cli(launch_workspace, "list"),
            _cli(launch_workspace, "show", held),
            _cli(launch_workspace, "undo", held),
            _cli(launch_workspace, "show", held),
            _cli(launch_workspace, "clear-hold", held, "--level", "anyone", "--yes"),
            _cli(launch_workspace, "never-use", held),
            _cli(launch_workspace, "show", held),
        ]
        assert "You cleared its hold for just us" in transcript[1]
        assert "Cleared for anyone" in transcript[4]
        assert "Held: a nudity detector flagged it." in transcript[3]
        assert "You'll never use this picture." in transcript[6]
        evidence = _ROOT / "test-results"
        evidence.mkdir(exist_ok=True)
        (evidence / "pictures-cli.txt").write_text(
            "\n".join(transcript).replace(str(launch_workspace.root), "<fixture-workspace>")
        )
    finally:
        for asset_id in (held, shot, CARRIERS[1].asset_id):
            owner_decisions.forget(store, asset_id)
