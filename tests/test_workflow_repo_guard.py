"""Every job outside integration.yml runs only in the public repo, never in the billed mirror."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_workflow_repo_guard import unguarded_jobs  # noqa: E402

GUARD = "github.repository == 'sam-dumont/immich-video-memory-generator'"


def _workflow(tmp_path: Path, name: str, jobs: str) -> None:
    (tmp_path / name).write_text(f"name: t\non: push\njobs:\n{jobs}")


def test_the_repo_workflows_all_carry_the_guard() -> None:
    assert unguarded_jobs(Path(".github/workflows")) == []


def test_a_job_with_no_condition_is_reported(tmp_path: Path) -> None:
    _workflow(tmp_path, "new.yml", "  build:\n    runs-on: ubuntu-latest\n")

    assert unguarded_jobs(tmp_path) == ["new.yml: build"]


def test_the_guard_can_sit_anywhere_in_an_and_chain(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "ok.yml",
        f'  a:\n    if: "${{{{ always() && {GUARD} }}}}"\n'
        f"  b:\n    if: >-\n      {GUARD} &&\n      (github.event_name == 'push')\n",
    )

    assert unguarded_jobs(tmp_path) == []


def test_a_guard_that_an_or_can_bypass_is_reported(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "or.yml",
        f'  a:\n    if: "{GUARD} || always()"\n'
        f'  b:\n    if: "({GUARD} || always()) && success()"\n',
    )

    assert unguarded_jobs(tmp_path) == ["or.yml: a", "or.yml: b"]


def test_integration_yml_is_exempt_because_it_runs_in_the_mirror(tmp_path: Path) -> None:
    _workflow(tmp_path, "integration.yml", "  gpu:\n    runs-on: gpu\n")

    assert unguarded_jobs(tmp_path) == []
