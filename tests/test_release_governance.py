"""Release publication requires deliberate dispatch and a passing image smoke test."""

import os
import shutil
import subprocess
from graphlib import TopologicalSorter
from pathlib import Path

import pytest
import yaml


def release_workflow():
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
    # PyYAML reads the GitHub Actions `on` key as a YAML 1.1 boolean.
    workflow["on"] = workflow.pop(True)
    return workflow


def test_a_merge_to_main_does_not_publish_a_release():
    assert set(release_workflow()["on"]) == {"workflow_dispatch"}


def test_release_publication_waits_for_the_image_smoke_test():
    jobs = release_workflow()["jobs"]
    dependencies = {name: set(job.get("needs", [])) for name, job in jobs.items()}
    tuple(TopologicalSorter(dependencies).static_order())

    def ancestors(name):
        return dependencies[name] | {
            ancestor for parent in dependencies[name] for ancestor in ancestors(parent)
        }

    for publication in ("release", "pypi-publish", "docker-manifest", "deploy-docs"):
        assert "docker-smoke" in ancestors(publication), publication


def test_dry_run_uses_boolean_guards_for_publication():
    workflow = release_workflow()
    for job in workflow["jobs"].values():
        for item in (job, *job.get("steps", [])):
            condition = item.get("if", "")
            assert "inputs.dry_run != 'true'" not in condition
            assert "inputs.dry_run == 'true'" not in condition
    release_steps = workflow["jobs"]["release"]["steps"]
    tag_step = next(step for step in release_steps if "git push" in step.get("run", ""))
    assert "!inputs.dry_run" in tag_step["if"]
    for name in ("pypi-publish", "pypi-publish-music", "docker-build", "deploy-docs"):
        assert "!inputs.dry_run" in workflow["jobs"][name]["if"]


def test_package_build_finishes_before_the_release_tag_is_pushed():
    steps = release_workflow()["jobs"]["release"]["steps"]
    build_index = next(i for i, step in enumerate(steps) if step.get("run") == "uv build")
    tag_index = next(i for i, step in enumerate(steps) if "git push" in step.get("run", ""))
    assert build_index < tag_index


def test_the_first_registry_push_keeps_the_release_environment_approval():
    jobs = release_workflow()["jobs"]

    def guarded(name):
        job = jobs[name]
        return "production-major" in str(job.get("environment", "")) or any(
            guarded(parent) for parent in job.get("needs", [])
        )

    assert guarded("docker-build")


def test_secret_scan_checks_every_unreleased_commit_independent_of_event(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, env=env, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release@example.test")
    git("commit", "--allow-empty", "-qm", "fix: released")
    git("tag", "v1.2.3")
    git("commit", "--allow-empty", "-qm", "fix: first change")
    first = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "fix: second change")
    second = git("rev-parse", "HEAD")
    scanner = tmp_path / "gitleaks"
    # WHY: capture the external scanner's range without requiring its binary in unit CI.
    scanner.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > scan-args.txt\n')
    scanner.chmod(0o755)
    for event in ("pull_request", "push", "workflow_dispatch"):
        subprocess.run(
            ["make", "-f", str(Path("Makefile").resolve()), "secret-scan"],
            cwd=tmp_path,
            env={**env, "PATH": f"{tmp_path}:{env['PATH']}", "GITHUB_EVENT_NAME": event},
            check=True,
            capture_output=True,
            text=True,
        )
        args = (tmp_path / "scan-args.txt").read_text().splitlines()
        assert "--redact" in args
        scan_range = next(
            arg.removeprefix("--log-opts=") for arg in args if arg.startswith("--log-opts=")
        )
        assert set(git("rev-list", scan_range).splitlines()) == {first, second}


@pytest.mark.parametrize(
    "message",
    [
        "fix(api)!: remove the obsolete endpoint",
        "docs!: remove the legacy installation path",
        "feat: replace the configuration\n\nBREAKING CHANGE: the old key is no longer accepted",
        "fix!: incompatible change\n\n" + "Detailed integration history.\n" * 2000,
    ],
    ids=["fix-marker", "docs-marker", "body-footer", "long-squash-body"],
)
def test_breaking_squash_messages_produce_a_major_release(tmp_path, message):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Release test"),
        ("config", "user.email", "release@example.test"),
        ("commit", "--allow-empty", "-qm", "fix: released"),
        ("tag", "v1.2.3"),
        ("commit", "--allow-empty", "-qm", message),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True, capture_output=True)
    # WHY: v1.2.3 stands for a published release here; the fake gh answers the
    # release-existence question the analyze step asks GitHub in production.
    (tmp_path / "gh").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "gh").chmod(0o755)
    env = {**env, "PATH": f"{tmp_path}:{env['PATH']}"}
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(
        Path(__file__).parents[1] / "scripts" / "release_analyze.py",
        scripts / "release_analyze.py",
    )
    analyze = next(
        step
        for step in release_workflow()["jobs"]["analyze"]["steps"]
        if step.get("id") == "analyze"
    )
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", analyze["run"]],
        cwd=tmp_path,
        env={
            **env,
            "GITHUB_OUTPUT": str(output),
            "FORCE_VERSION": "auto",
            "INFERENCE_ONLY": "false",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert "next_version=2.0.0" in output.read_text().splitlines()


def test_an_interrupted_release_is_resumed_with_the_same_version(tmp_path):
    """Tag pushed, GitHub Release never created: the next run republishes it (#1010)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Release test"),
        ("config", "user.email", "release@example.test"),
        ("commit", "--allow-empty", "-qm", "fix: released"),
        ("tag", "v1.2.3"),
        ("commit", "--allow-empty", "-qm", "fix: released"),
        ("tag", "-a", "v1.2.4", "-m", "Release v1.2.4"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True, capture_output=True)
    # WHY: v1.2.4 exists as a tag but its publication failed before the GitHub
    # Release was created, so the fake gh reports no release for it.
    (tmp_path / "gh").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / "gh").chmod(0o755)
    env = {**env, "PATH": f"{tmp_path}:{env['PATH']}"}
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(
        Path(__file__).parents[1] / "scripts" / "release_analyze.py",
        scripts / "release_analyze.py",
    )
    analyze = next(
        step
        for step in release_workflow()["jobs"]["analyze"]["steps"]
        if step.get("id") == "analyze"
    )
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", analyze["run"]],
        cwd=tmp_path,
        env={
            **env,
            "GITHUB_OUTPUT": str(output),
            "FORCE_VERSION": "auto",
            "INFERENCE_ONLY": "false",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    lines = output.read_text().splitlines()
    assert "should_release=true" in lines
    assert "next_version=1.2.4" in lines, "the stranded version, not an advance past it"
