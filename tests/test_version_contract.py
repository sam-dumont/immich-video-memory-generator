"""Cross-surface contract for the application build version."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

import immich_memories
from immich_memories._version import __version__ as generated_version

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_target_prerequisites(target: str) -> set[str]:
    result = subprocess.run(
        ["make", "--no-print-directory", "-qp"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode in {0, 1}, result.stderr
    prefix = f"{target}:"
    declaration = next(line for line in result.stdout.splitlines() if line.startswith(prefix))
    return set(declaration.removeprefix(prefix).split())


def _make_dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _ci_success_result(
    event_name: str,
    setup_result: str,
    launch_result: str,
) -> subprocess.CompletedProcess[str]:
    """Run the checked-in summary gate against one GitHub-result scenario."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    command = workflow["jobs"]["ci-success"]["steps"][0]["run"]
    replacements = {
        "${{ github.event_name }}": event_name,
        "${{ needs.setup.result }}": setup_result,
        "${{ needs['launch-check'].result }}": launch_result,
    }
    for token, result in replacements.items():
        command = command.replace(token, result)
    for job in workflow["jobs"]:
        command = command.replace(f"${{{{ needs.{job}.result }}}}", "success")
    assert "${{" not in command
    return subprocess.run(
        ["bash", "-o", "pipefail", "-c", command],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_package_exports_generated_version() -> None:
    """The public package version must be the version Hatch VCS generated."""
    assert immich_memories.__version__ == generated_version


def test_hatch_vcs_is_the_only_configured_version_source() -> None:
    """Packaging and release tooling must not maintain a second static version."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["tool"]["hatch"]["version"]["source"] == "vcs"
    semantic_release = pyproject["tool"]["semantic_release"]
    assert "version" not in semantic_release
    assert "version_toml" not in semantic_release


def test_launch_check_composes_every_release_gate() -> None:
    """One local target must exercise the same artifacts required for launch."""
    assert _make_target_prerequisites("launch-check") == {
        "check",
        "build",
        "build-check",
        "docs-check",
        "e2e",
    }


def test_launch_check_consumes_the_preinstalled_ci_environment() -> None:
    """The launch job must not replace dev-test with every heavyweight extra."""
    commands = _make_dry_run("launch-check")

    assert "uv sync --all-extras" not in commands
    assert "Using preinstalled launch-check dependencies" in commands


def test_ci_runs_the_hermetic_launch_check_with_runtime_dependencies() -> None:
    """Pull requests must run the browser/FFmpeg launch gate without credentials."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    launch_job = workflow["jobs"]["launch-check"]
    steps = launch_job["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)

    assert launch_job["timeout-minutes"] == 30
    assert "ffmpeg" in commands
    assert "playwright install --with-deps chromium" in commands
    assert "make launch-check" in commands
    assert all("IMMICH_API_KEY" not in str(step) for step in steps)

    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "failure()"
    artifact_paths = str(upload["with"]["path"])
    for expected in ("tests/e2e-junit.xml", "server.log", "output-probe.json"):
        assert expected in artifact_paths
    assert "launch-smoke*/output/**" in artifact_paths


@pytest.mark.parametrize(
    ("event_name", "setup_result", "launch_result", "expected_returncode"),
    [
        ("pull_request", "success", "success", 0),
        ("pull_request", "success", "skipped", 1),
        ("pull_request", "success", "cancelled", 1),
        ("workflow_call", "success", "skipped", 0),
        ("workflow_call", "success", "cancelled", 0),
        ("workflow_call", "failure", "skipped", 1),
    ],
)
def test_ci_summary_gate_enforces_event_sensitive_launch_result_matrix(
    event_name: str,
    setup_result: str,
    launch_result: str,
    expected_returncode: int,
) -> None:
    """PR launch results are required; workflow calls permit the skipped launch job."""
    result = _ci_success_result(event_name, setup_result, launch_result)

    assert result.returncode == expected_returncode, result.stdout + result.stderr
