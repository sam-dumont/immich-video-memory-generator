"""The extras job must run the extras tests, and only those.

`Test Extras` installs the torch family (face/audio-ml/demucs) and then ran the
entire 5,378-test suite in one process. The extras unlock heavy code paths that
allocate and are never reclaimed within a session, so later ffmpeg-forking tests
could not fork and the kernel killed the runner — Error 137, no test failure, on
both platforms. The plain `test` matrix already covers the whole suite; this job
exists for what the extras unlock.

Selecting by filename would rot as tests move, so selection is by marker and
these tests hold the marker honest.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPO_ROOT / "tests"

# How a test says "I need the torch family". Anything matching this must live in
# a module the extras job selects, or the job silently stops covering it.
_TORCH_GATES = re.compile(r"""importorskip\(\s*["']torch["']|_has_torch""")


def _modules_gating_on_torch() -> set[Path]:
    return {path for path in TESTS.glob("test_*.py") if _TORCH_GATES.search(path.read_text())}


def test_every_torch_gated_module_carries_the_extras_marker() -> None:
    unmarked = [
        path.name
        for path in _modules_gating_on_torch()
        if "pytest.mark.extras" not in path.read_text()
    ]

    assert not unmarked, (
        f"these gate on torch but the extras job will not select them: {sorted(unmarked)}"
    )


def test_the_marker_is_registered() -> None:
    """An unregistered marker is a warning, and under -W error a failure."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()

    assert re.search(r'^\s*"extras:', pyproject, re.M), "extras marker not declared"


def test_ci_runs_the_extras_suite_not_the_whole_one() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = workflow["jobs"]["test-extras"]
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])

    assert re.search(r"^\s*make test-extras\s*$", commands, re.M)
    assert not re.search(r"^\s*make test\s*$", commands, re.M), (
        "the plain test matrix already runs the full suite on 3 Pythons x 2 OS"
    )


def test_the_extras_job_gives_up_rather_than_wedging_for_six_hours() -> None:
    """It had no timeout, so a wedged run burned to the default 360 minutes."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())

    assert workflow["jobs"]["test-extras"]["timeout-minutes"] <= 30
