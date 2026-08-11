"""Launch-critical contracts for container builds and local Docker commands."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dockerfile() -> str:
    return (REPO_ROOT / "docker" / "Dockerfile").read_text()


def _logical_instructions(source: str) -> str:
    """Join Docker continuation lines so shell contracts are readable."""
    joined = re.sub(r"\\\s*\n\s*", " ", source)
    return re.sub(r"[ \t]+", " ", joined)


def _dry_run_make(target: str, *assignments: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target, *assignments],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return _logical_instructions(result.stdout)


def test_docker_build_requires_an_explicit_application_version() -> None:
    """A missing release version must stop the image build, not invent one."""
    dockerfile = _dockerfile()

    assert re.search(r"(?m)^ARG APP_VERSION\s*$", dockerfile)
    assert "ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}" in dockerfile
    assert re.search(r'RUN if \[ -z "\$\{APP_VERSION\}" \]; then .*exit 1', dockerfile)
    assert "SETUPTOOLS_SCM_PRETEND_VERSION=0.2.0" not in dockerfile


def test_docker_build_installs_exactly_the_requested_feature_set() -> None:
    """Dependency failure must fail instead of silently dropping optional features."""
    dockerfile = _logical_instructions(_dockerfile())

    assert re.search(r"(?m)^ARG INSTALL_EXTRAS=all\s*$", dockerfile)
    assert (
        'if [ "${INSTALL_EXTRAS}" = "none" ]; then '
        "pip wheel --no-cache-dir --wheel-dir=/wheels .; else" in dockerfile
    )
    assert 'pip wheel --no-cache-dir --wheel-dir=/wheels ".[${INSTALL_EXTRAS}]"' in dockerfile
    assert not re.search(r"pip wheel[^\n]*\|\|", dockerfile)


def test_runtime_image_exposes_standard_oci_build_identity() -> None:
    """Registries must expose the release version, revision, and source repository."""
    dockerfile = _logical_instructions(_dockerfile())

    assert 'org.opencontainers.image.version="${APP_VERSION}"' in dockerfile
    assert 'org.opencontainers.image.revision="${VCS_REF}"' in dockerfile
    assert 'org.opencontainers.image.source="${SOURCE_URL}"' in dockerfile
    assert 'LABEL version="0.2.0"' not in dockerfile


def test_container_healthcheck_uses_process_liveness() -> None:
    """Dependency readiness must not make the container runtime restart a live process."""
    dockerfile = _dockerfile()

    assert "http://localhost:8080/health/live" in dockerfile
    assert "http://localhost:8080/health')" not in dockerfile


def test_local_docker_build_passes_version_and_feature_selection() -> None:
    """Make must forward the operator's exact version and extras to Docker."""
    command = _dry_run_make(
        "docker",
        "APP_VERSION=9.8.7",
        "INSTALL_EXTRAS=none",
        "DOCKER_TAG=contract-test",
    )

    assert "--build-arg APP_VERSION=9.8.7" in command
    assert "--build-arg INSTALL_EXTRAS=none" in command
    assert "-t immich-memories:contract-test" in command


def test_local_docker_version_comes_from_current_git_state(tmp_path: Path) -> None:
    """A stale generated Python version must not label a newer checkout."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nprintf '9.8.7\\n'\n")
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    command = _dry_run_make("docker", "DOCKER_TAG=vcs-contract", env=env)
    short_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked_changes = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    expected_version = f"0+g{short_sha}{'.dirty' if tracked_changes else ''}"

    assert f"--build-arg APP_VERSION={expected_version}" in command
    assert "--build-arg APP_VERSION=9.8.7" not in command


@pytest.mark.parametrize("target", ["docker-run", "docker-shell"])
def test_local_docker_commands_use_docker_owned_volumes(target: str) -> None:
    """Local persistence must retain the ownership baked into the image."""
    command = _dry_run_make(target)

    assert (
        "--mount type=volume,source=immich-memories-config,"
        "target=/home/immich/.immich-memories" in command
    )
    assert "--mount type=volume,source=immich-memories-output,target=/app/output" in command
    assert "mkdir -p" not in command
    assert " -v " not in command
    assert (
        "chown -R immich:immich /home/immich/.immich-memories /app/output"
        in _logical_instructions(_dockerfile())
    )


@pytest.mark.parametrize("target", ["docker-run", "docker-shell"])
def test_local_volume_overrides_remain_named_volumes(target: str) -> None:
    """Operators may rename volumes without changing them into host binds."""
    command = _dry_run_make(
        target,
        "IMMICH_CONFIG_VOLUME=contract-config",
        "IMMICH_OUTPUT_VOLUME=contract-output",
    )

    assert (
        "--mount type=volume,source=contract-config,target=/home/immich/.immich-memories" in command
    )
    assert "--mount type=volume,source=contract-output,target=/app/output" in command
    assert " -v " not in command


def test_release_images_receive_one_explicit_build_identity() -> None:
    """Every release platform must receive the analyzed version and commit identity."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
    steps = workflow["jobs"]["docker-build"]["steps"]
    build_step = next(
        step for step in steps if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )
    build_args = str(build_step["with"].get("build-args", ""))

    assert "APP_VERSION=${{ needs.analyze.outputs.next_version }}" in build_args
    assert "INSTALL_EXTRAS=all" in build_args
    assert "VCS_REF=${{ github.sha }}" in build_args
    assert "SOURCE_URL=https://github.com/${{ github.repository }}" in build_args
