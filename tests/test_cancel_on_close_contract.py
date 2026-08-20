"""A closed pull request must stop paying for runner slots.

`pull_request` runs do not stop when the PR merges, so a batch of merges leaves
a tail of runs for branches nobody is waiting on. Against GitHub's caps for a
public repo — 5 concurrent macOS jobs, 20 overall — that tail starves whatever
is still open: eleven of twelve queued runs once belonged to already-merged PRs
while the single open one sat behind them.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cancel-on-close.yml"


def _workflow() -> dict:
    # PyYAML reads the `on:` key as the boolean True.
    return yaml.safe_load(WORKFLOW.read_text())


def test_it_fires_when_a_pull_request_closes() -> None:
    triggers = _workflow()[True]["pull_request"]["types"]

    assert triggers == ["closed"]


def test_it_may_cancel_runs() -> None:
    """Without actions: write the cancel call returns 403 and the tail survives."""
    assert _workflow()["permissions"]["actions"] == "write"


def test_it_cancels_queued_runs_as_well_as_running_ones() -> None:
    """The starving runs are the *queued* ones; cancelling only in_progress misses them."""
    body = WORKFLOW.read_text()

    assert "queued" in body
    assert "in_progress" in body


def test_the_branch_name_is_passed_as_an_environment_variable() -> None:
    """A head ref is attacker-controlled on a fork; interpolating it into the
    shell would be a command injection. Read through env instead."""
    body = WORKFLOW.read_text()

    assert (
        "${{ github.event.pull_request.head.ref }}"
        not in body.split("env:", 1)[-1].split("run:", 1)[-1]
    ), "head.ref must not appear inside the run script"
    assert "BRANCH: ${{ github.event.pull_request.head.ref }}" in body
