"""Pull-request CI must fit inside GitHub's macOS concurrency allowance.

This repo is public, so hosted runners are free — but capped, and **macOS is
capped at 5 concurrent jobs**. A 3-Python x 2-OS test matrix asks for 3 macOS
jobs per pull request; with ten PRs open that is ~30 macOS jobs queueing for 5
slots. Runs then sit for hours and GitHub reclaims them mid-job, which surfaces
as `The runner has received a shutdown signal` followed by `Error 137` — a
symptom of the runner going away, not of anything the tests did.

Ubuntu keeps every Python version. macOS keeps one per pull request, and the
full matrix still runs on main via `workflow_call`.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_MACOS_CONCURRENCY_LIMIT = 5


def _ci() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())


def _pull_request_matrix() -> list[dict]:
    """The cells a pull request actually schedules."""
    raw = _ci()["jobs"]["test"]["strategy"]["matrix"]
    if isinstance(raw, str):  # a fromJSON expression, evaluated per event
        pr_branch = raw.split("&&", 1)[1].split("||", 1)[0]
        return json.loads(pr_branch.strip().strip("'"))["include"]
    return [
        {"os": os_name, "python-version": version}
        for os_name in raw["os"]
        for version in raw["python-version"]
    ]


def test_a_pull_request_asks_for_at_most_one_macos_test_cell() -> None:
    macos = [cell for cell in _pull_request_matrix() if "macos" in cell["os"]]

    assert len(macos) <= 1, (
        f"{len(macos)} macOS cells per PR; ten open PRs would queue "
        f"{len(macos) * 10} jobs against a limit of {_MACOS_CONCURRENCY_LIMIT}"
    )


def test_ubuntu_still_covers_every_supported_python() -> None:
    """Trimming macOS must not quietly drop version coverage."""
    ubuntu = {cell["python-version"] for cell in _pull_request_matrix() if "ubuntu" in cell["os"]}

    assert ubuntu == {"3.11", "3.12", "3.13"}
