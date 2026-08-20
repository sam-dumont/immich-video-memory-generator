"""The quality gates share one runner slot, not thirteen.

Each gate takes 10-35 seconds, but each used to hold its own job. This repo is
public, so the account gets 20 concurrent jobs; with several pull requests in
flight roughly 65 slots were occupied by half-minute jobs while the test matrix
queued behind them and was reclaimed mid-run -- `The runner has received a
shutdown signal` followed by `Error 137`, which is the runner going away rather
than anything the tests did.

Sequenced in one job the gates cost the same four minutes of work and one slot,
and `make dev-ci` -- most of each old job's wall time -- runs once.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every gate that used to be its own job. Losing one silently would be the
# failure this consolidation could plausibly cause.
_REQUIRED_GATES = (
    "make lint",
    "make format-check",
    "make docs-cli-check",
    "make typecheck",
    "make dead-code",
    "make complexity",
    "make cognitive-complexity",
    "make file-length",
    "make refurb",
    "make dep-check",
    "make arch-check",
    "make duplication",
    "make critique",
    "make commitlint",
)


def _ci() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())


def _quality_steps() -> list[dict]:
    return _ci()["jobs"]["quality"]["steps"]


def _run_lines() -> str:
    return "\n".join(str(step.get("run", "")) for step in _quality_steps())


def test_every_gate_still_runs():
    commands = _run_lines()

    missing = [gate for gate in _REQUIRED_GATES if gate not in commands]

    assert missing == [], f"gates dropped in consolidation: {missing}"


def test_one_failing_gate_does_not_hide_the_others():
    """Thirteen parallel jobs reported every failure at once; steps stop at the
    first by default, which would make a red build tell you one thing at a time.
    """
    unguarded = [
        step.get("name", step.get("run", "?"))
        for step in _quality_steps()
        if any(gate in str(step.get("run", "")) for gate in _REQUIRED_GATES)
        and "cancelled()" not in str(step.get("if", ""))
    ]

    assert unguarded == [], f"these gates stop the run when an earlier one fails: {unguarded}"


def test_the_environment_is_built_once():
    """Repeating `make dev-ci` per gate was most of the old wall time."""
    assert _run_lines().count("make dev-ci") == 1


def test_the_gates_do_not_fan_back_out_into_their_own_jobs():
    jobs = _ci()["jobs"]
    gate_named_jobs = [
        name
        for name in jobs
        if name
        in {
            "lint",
            "typecheck",
            "dead-code",
            "complexity",
            "cognitive-complexity",
            "file-length",
            "refurb",
            "dep-check",
            "arch-check",
            "duplication",
            "critique",
            "commitlint",
        }
    ]

    assert gate_named_jobs == [], f"gates split back into separate slots: {gate_named_jobs}"


def test_the_aggregate_gate_still_watches_the_quality_job():
    """`ci-success` is the required check; it must fail when the gates fail."""
    ci_success = _ci()["jobs"]["ci-success"]

    assert "quality" in ci_success["needs"]
    assert "needs.quality.result" in str(ci_success["steps"])
