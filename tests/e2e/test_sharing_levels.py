"""Who the film is for: the brief's choice, the CLI flag, and what the run records (#1325)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _build_launch_environment
from tests.e2e.test_demo_assets import _TRIP_CLI_BOOTSTRAP
from tests.e2e.test_launch_smoke import _choose
from tests.e2e.test_memory_page import (
    _attempts,
    _brief_for_june,
    _newest_attempt,
    _request_of_the_cut_after,
)

pytestmark = pytest.mark.e2e

_ROOT = Path(__file__).resolve().parents[2]


def _cli(launch_workspace, *args: str) -> str:
    result = subprocess.run(
        [
            str(_ROOT / ".venv/bin/python"),
            "-c",
            _TRIP_CLI_BOOTSTRAP,
            str(launch_workspace.config_path),
            str(launch_workspace.root / "state"),
            *args,
        ],
        cwd=_ROOT,
        env=_build_launch_environment(launch_workspace.root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return f"$ immich-memories {' '.join(args)}\n{result.stdout}"


def test_the_brief_asks_who_will_watch_and_the_cut_is_made_for_them(
    page: Page, launch_app_url: str, launch_workspace
) -> None:
    _brief_for_june(page, launch_app_url)
    who = page.get_by_role("combobox", name="Who will watch it")
    expect(who).to_have_value("Family")
    expect(page.get_by_text("Grandparents, siblings, the group chat.", exact=False)).to_be_visible()

    _choose(page, "Who will watch it", "Just us")
    expect(page.get_by_text("The household.", exact=False)).to_be_visible()
    before = _attempts(launch_workspace)
    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.locator(".storyboard-shot").first).to_be_visible(timeout=120_000)

    assert _request_of_the_cut_after(launch_workspace, before)["audience"] == "just_us"

    web_attempt = str(_newest_attempt(launch_workspace))
    before = _attempts(launch_workspace)
    month = ["--memory-type", "monthly_highlights", "--year", "2024", "--month", "6"]
    quiet = ["--no-render", "--no-music", "--quiet"]
    transcript = [_cli(launch_workspace, "generate", *month, "--sharing", "shareable", *quiet)]
    assert _request_of_the_cut_after(launch_workspace, before)["audience"] == "shareable"
    assert "Sharing: shareable" in transcript[0]
    cli_attempt = _newest_attempt(launch_workspace)
    status = json.loads((cli_attempt / "status.private.json").read_text())
    assert status["request"]["audience"] == "shareable"
    web_story = _cli(launch_workspace, "runs", "story", web_attempt)
    cli_story = _cli(launch_workspace, "runs", "story", str(cli_attempt))
    assert "Sharing: just us" in web_story
    assert "Sharing: shareable" in cli_story
    transcript += [web_story, cli_story]
    evidence = _ROOT / "test-results"
    evidence.mkdir(exist_ok=True)
    (evidence / "sharing-cli.txt").write_text(
        "\n".join(transcript).replace(str(launch_workspace.root), "<fixture-workspace>")
    )
