"""Cross-surface contract for the application build version."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

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
    summary_command = "\n".join(
        str(step.get("run", "")) for step in workflow["jobs"]["ci-success"]["steps"]
    )
    assert "needs['launch-check'].result" in summary_command

    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "failure()"
    artifact_paths = str(upload["with"]["path"])
    for expected in ("tests/e2e-junit.xml", "server.log", "output-probe.json"):
        assert expected in artifact_paths
