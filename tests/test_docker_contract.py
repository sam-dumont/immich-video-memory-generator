"""Launch-critical contracts for container builds and local Docker commands."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.markers import default_environment
from packaging.requirements import Requirement

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


def _resolve_install_target(extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, REPO_ROOT / "docker" / "validate_install_extras.py", extra],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _marker_environment(sys_platform: str, platform_machine: str) -> dict[str, str]:
    environment = {key: str(value) for key, value in default_environment().items()}
    environment["sys_platform"] = sys_platform
    environment["platform_machine"] = platform_machine
    return environment


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
    validator = 'python docker/validate_install_extras.py "${INSTALL_EXTRAS}"'
    wheel_build = 'pip wheel --no-cache-dir --wheel-dir=/wheels "${INSTALL_TARGET}"'
    assert validator in dockerfile
    assert wheel_build in dockerfile
    assert dockerfile.index(validator) < dockerfile.index(wheel_build)
    assert not re.search(r"pip wheel[^\n]*\|\|", dockerfile)


@pytest.mark.parametrize("extra", ["", "definitely-not-a-real-extra"])
def test_install_extra_validator_rejects_blank_and_unknown_names(extra: str) -> None:
    """A typo must stop before pip can turn it into a successful base install."""
    result = _resolve_install_target(extra)

    assert result.returncode != 0
    assert f"Invalid INSTALL_EXTRAS={extra!r}" in result.stderr
    assert "Use 'none' or one of:" in result.stderr
    assert "all" in result.stderr


@pytest.mark.parametrize(
    ("extra", "expected"),
    [("none", "."), ("auth", ".[auth]"), ("all", ".[all]")],
)
def test_install_extra_validator_resolves_declared_feature_sets(extra: str, expected: str) -> None:
    """The explicit base selector and declared extras must map to exact wheel targets."""
    result = _resolve_install_target(extra)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_gpu_extra_excludes_taichi_only_on_linux_arm64() -> None:
    """The all image must keep GPU support except where Taichi publishes no wheel."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]
    gpu_requirements = [Requirement(value) for value in extras["gpu"]]
    taichi = next(requirement for requirement in gpu_requirements if requirement.name == "taichi")
    freetype = next(
        requirement for requirement in gpu_requirements if requirement.name == "freetype-py"
    )

    assert taichi.marker is not None
    for machine in ("aarch64", "arm64"):
        environment = _marker_environment("linux", machine)
        assert not taichi.marker.evaluate(environment)

    for platform, machine in (("linux", "x86_64"), ("darwin", "arm64"), ("win32", "AMD64")):
        environment = _marker_environment(platform, machine)
        assert taichi.marker.evaluate(environment)

    assert freetype.marker is None
    assert "immich-memories[gpu]" in extras["all"]


def test_builder_provides_native_opus_for_arm64_source_wheels() -> None:
    """ARM64 audio wheels must link system Opus instead of an obsolete bundled build."""
    builder_stage = _logical_instructions(_dockerfile()).split("# Stage 2:", maxsplit=1)[0]

    assert "libopus-dev" in builder_stage
    assert "pkg-config" in builder_stage


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


def test_release_publisher_supports_core_metadata_2_5() -> None:
    """The PyPI publisher must understand metadata emitted by current build tooling."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
    steps = workflow["jobs"]["pypi-publish"]["steps"]
    publish_step = next(step for step in steps if step.get("name") == "Publish to PyPI")

    assert publish_step["uses"] == (
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
    )


def test_gpu_integration_uses_ci_dependency_set() -> None:
    """GPU integration needs Taichi, not the full Torch and audio-ML stack."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "integration.yml").read_text())
    run_commands = [
        step.get("run") for step in workflow["jobs"]["integration"]["steps"] if "run" in step
    ]

    assert "make dev-test" in run_commands
    assert "make dev" not in run_commands


def test_pull_request_images_receive_required_build_arguments() -> None:
    """Every PR image build must satisfy the Dockerfile's fail-closed arguments."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    build_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]

    assert build_steps
    for step in build_steps:
        build_args = str(step["with"].get("build-args", ""))
        assert "APP_VERSION=0+g${{ github.sha }}" in build_args
        assert "INSTALL_EXTRAS=all" in build_args
        assert "VCS_REF=${{ github.sha }}" in build_args
        assert "SOURCE_URL=https://github.com/${{ github.repository }}" in build_args


def test_image_default_output_directory_is_the_compose_output_mount() -> None:
    """Videos generated in the container must land on the volume the quickstart mounts.

    Without this, the default `~/Videos/Memories` resolves inside the container's
    home and `docker compose pull && up -d` silently discards every generated video.
    """
    dockerfile = _logical_instructions(_dockerfile())
    match = re.search(r"ENV IMMICH_MEMORIES_OUTPUT__DIRECTORY=(\S+)", dockerfile)
    assert match, "Dockerfile must pin the default output directory to a mounted path"
    image_output_dir = match.group(1)

    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    volumes = compose["services"]["immich-memories"]["volumes"]
    mount_targets = {str(v).split(":")[1] for v in volumes if ":" in str(v)}
    assert image_output_dir in mount_targets, (
        f"{image_output_dir} is not a compose mount target: {sorted(mount_targets)}"
    )


def test_image_runs_as_uid_1000() -> None:
    """Bind mounts (./output, ./config) and the K8s manifests assume the common host UID.

    A system UID from `useradd -r` (~999) cannot write a host directory owned by
    the first Linux user, and the shipped manifests pin `runAsUser: 1000`.
    """
    dockerfile = _logical_instructions(_dockerfile())
    assert re.search(r"groupadd\s+(-r\s+)?-g\s+1000\s+immich", dockerfile), "group must be GID 1000"
    assert re.search(r"useradd\s+[^&]*-u\s+1000\s+", dockerfile), "user must be UID 1000"
    assert "useradd -r" not in dockerfile
