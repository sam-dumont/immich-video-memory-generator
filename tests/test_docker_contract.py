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


_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _dockerfile() -> str:
    return (REPO_ROOT / "docker" / "Dockerfile").read_text()


def _logical_instructions(source: str) -> str:
    """Join Docker continuation lines so shell contracts are readable."""
    joined = re.sub(r"\\\s*\n\s*", " ", source)
    return re.sub(r"[ \t]+", " ", joined)


def _final_stage_instructions(source: str) -> list[str]:
    """Instructions of the last build stage, in file order — earlier stages are discarded."""
    lines = [
        line.strip()
        for line in _logical_instructions(source).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    final_stage = max(index for index, line in enumerate(lines) if line.startswith("FROM "))
    return lines[final_stage:]


# `rm` with its flags stripped, leaving the operands it deletes.
_REMOVED_PATHS = re.compile(r"\brm\b(?:\s+-\S+)*(?P<paths>(?:\s+[^\s;&|]+)+)")


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


def _marker_environment(
    sys_platform: str, platform_machine: str, python_version: str = "3.12"
) -> dict[str, str]:
    environment = {key: str(value) for key, value in default_environment().items()}
    environment["sys_platform"] = sys_platform
    environment["platform_machine"] = platform_machine
    environment["python_version"] = python_version
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
    wheel_build = re.search(r'(?m)^.*pip wheel[^\n]*"\$\{INSTALL_TARGET\}".*$', dockerfile)
    assert validator in dockerfile
    assert wheel_build, "the validated target must be what pip builds"
    instruction = wheel_build.group()
    assert "--wheel-dir=/wheels" in instruction
    # The target has to be resolved by the validator before pip is handed it.
    assert instruction.index(validator) < instruction.index("pip wheel")
    assert not re.search(r"pip wheel[^\n]*\|\|", dockerfile)


def test_docker_builds_the_bundled_music_package_from_the_tree() -> None:
    """The music extra resolves against the in-repo package, not a published release."""
    dockerfile = _logical_instructions(_dockerfile())

    assert "COPY packages/ ./packages/" in dockerfile
    assert (
        "pip wheel --no-cache-dir --wheel-dir=/wheels --constraint /constraints.txt ./packages/immich-memories-music"
        in (dockerfile)
    )
    assert "--find-links=/wheels" in dockerfile


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


def test_opencv_stays_on_the_5_line_because_only_that_line_is_graded() -> None:
    """5 is the line the suite and the pixel tests ran on; 6 is unverified, as <5 once was (#557)."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    opencv = next(
        Requirement(value)
        for value in pyproject["project"]["dependencies"]
        if Requirement(value).name == "opencv-python"
    )

    assert opencv.specifier.contains("5.0.0.93"), opencv
    assert not opencv.specifier.contains("4.13.0.92"), opencv
    assert not opencv.specifier.contains("6.0.0"), opencv


def test_the_arm64_image_gets_gpu_titles_from_the_base_install() -> None:
    """The whole point of the Quadrants default: linux/arm64 stops losing titles.

    The library the renderer used before Quadrants published no linux-aarch64
    wheel, so that platform lost its titles to PIL. Quadrants publishes one and
    is a base dependency, so the arm64 image renders titles on the GPU.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    base = [Requirement(value) for value in pyproject["project"]["dependencies"]]
    quadrants = next(requirement for requirement in base if requirement.name == "quadrants")
    freetype = next(requirement for requirement in base if requirement.name == "freetype-py")

    assert quadrants.marker is not None
    for platform, machine in (
        ("linux", "aarch64"),
        ("linux", "x86_64"),
        ("darwin", "arm64"),
        ("win32", "AMD64"),
    ):
        assert quadrants.marker.evaluate(_marker_environment(platform, machine)), (
            platform,
            machine,
        )

    # The two places Quadrants publishes no wheel, verified against PyPI: it
    # ships cp310-cp313 for linux x86_64/aarch64, macOS arm64 and win_amd64, and
    # no sdist. Those installs render title screens with PIL.
    assert not quadrants.marker.evaluate(_marker_environment("darwin", "x86_64"))
    assert not quadrants.marker.evaluate(_marker_environment("linux", "x86_64", "3.14"))

    # SDF font rendering ships with the kernels it feeds.
    assert freetype.marker is None


def test_no_extra_is_needed_for_gpu_titles() -> None:
    """GPU titles used to be `immich-memories[gpu]`; the kernels ship by default now."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    assert "gpu" not in pyproject["project"]["optional-dependencies"]
    assert not [
        value
        for values in pyproject["project"]["optional-dependencies"].values()
        for value in values
        if "gpu" in Requirement(value).extras
    ]


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
    """GPU integration needs the kernel library, not the full Torch and audio-ML stack."""
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


def test_quickstart_compose_publishes_the_ui_on_loopback_only() -> None:
    """The shipped compose file must not put the UI on the LAN by default.

    The container holds an Immich API key for the whole photo library and ships with
    authentication off, so reaching it from another machine has to be a deliberate
    edit paired with enabling auth — not what happens to anyone who runs `up -d`.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    published = [str(p) for p in compose["services"]["immich-memories"]["ports"]]

    assert published == ["127.0.0.1:8080:8080"], published


def test_quickstart_compose_pins_a_tier_that_needs_no_second_service() -> None:
    """The shipped file must name the tier, not inherit the code default.

    `EditorialPreparationConfig.tier` defaults to `full`, which demands a caption
    server at `caption_base_url`. Left unpinned, a first `up` with only IMMICH_URL
    and IMMICH_API_KEY stops its first cut asking for a service nothing in this
    file starts. `no_captions` is the richest tier the app serves on its own.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    environment = compose["services"]["immich-memories"]["environment"]

    assert environment["IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER"] == "no_captions"


def test_image_runs_as_uid_1000() -> None:
    """Bind mounts (./output, ./config) and the K8s manifests assume the common host UID.

    A system UID from `useradd -r` (~999) cannot write a host directory owned by
    the first Linux user, and the shipped manifests pin `runAsUser: 1000`.
    """
    dockerfile = _logical_instructions(_dockerfile())
    assert re.search(r"groupadd\s+(-r\s+)?-g\s+1000\s+immich", dockerfile), "group must be GID 1000"
    assert re.search(r"useradd\s+[^&]*-u\s+1000\s+", dockerfile), "user must be UID 1000"
    assert "useradd -r" not in dockerfile


def test_docker_installs_bundled_music_even_though_all_omits_it() -> None:
    """`all` cannot name the music package: it is not on PyPI, so an umbrella
    extra that requires it makes `pip install immich-memories[all]` unresolvable.
    The image therefore installs it explicitly from the tree."""
    dockerfile = _logical_instructions(_dockerfile())

    assert "./packages/immich-memories-music" in dockerfile
    install = re.search(r"(?m)^.*pip install[^\n]*/wheels[^\n]*$", dockerfile)
    assert install, "the image must install the wheels it built"


def test_all_extra_stays_installable_from_an_index() -> None:
    """Every dependency of `all` must be resolvable by pip from PyPI.

    `tool.uv.sources` is a uv-local mechanism that does not travel in wheel
    metadata, so an umbrella extra naming a package that exists only in this tree
    makes `pip install immich-memories[all]` unresolvable for everyone else.
    """
    import tomllib

    pyproject = tomllib.loads(_PYPROJECT.read_text())
    extras = pyproject["project"]["optional-dependencies"]
    local_sources = {
        name
        for name, spec in pyproject.get("tool", {}).get("uv", {}).get("sources", {}).items()
        if "path" in spec
    }
    # Packages this repository publishes itself. The release workflow puts them on
    # PyPI before the main wheel, so the local source is a dev convenience rather
    # than the only way to get them.
    published_by_us = {"immich-memories-music"}

    for umbrella in ("all", "all-mac"):
        for requirement in extras[umbrella]:
            package = requirement.split("[")[0].split(">")[0].split("=")[0].strip()

            assert package not in (local_sources - published_by_us), (
                f"{umbrella} requires {package}, which resolves only from a local path"
            )


def test_anything_we_publish_ourselves_is_published_before_the_main_wheel() -> None:
    """Otherwise the wheel lands on PyPI naming a dependency that is not there yet."""
    import yaml

    workflow = yaml.safe_load((_PYPROJECT.parent / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]

    assert "pypi-publish-music" in jobs
    assert "pypi-publish-music" in jobs["pypi-publish"]["needs"]


def test_every_published_platform_takes_torch_from_the_cpu_wheel_index() -> None:
    """Nothing in the image runs GPU inference, so the CUDA stack is dead weight.

    Both model seats are ONNX Runtime on the CPU provider, and the only remaining
    consumer of torch is local Demucs. torch pins its CUDA dependencies on
    `sys_platform == 'linux'` with no architecture guard, so aarch64 gets the same
    stack x86_64 does -- measured at 3.3 GB of `nvidia` plus 818 MB of triton in the
    arm64 image, for a torch that reports cuda_available: False. Adding a release
    platform without adding it here would quietly hand that platform the CUDA build.
    """
    source = _dockerfile()
    dockerfile = _logical_instructions(source)

    assert re.search(r"(?m)^ARG TARGETARCH\s*$", dockerfile)
    cpu_wheel = re.search(
        r'case "\$\{TARGETARCH\}" in (?P<arches>[a-z0-9|]+)\) pip wheel [^\n]*'
        r"--no-deps [^\n]*--wheel-dir=(?P<dir>/\S+) "
        r"--index-url https://download\.pytorch\.org/whl/cpu[^\n]* torch",
        dockerfile,
    )
    assert cpu_wheel, "the CPU index must supply torch for the platforms we publish"

    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
    published = {
        str(entry["platform"]).rsplit("/", maxsplit=1)[-1]
        for entry in workflow["jobs"]["docker-build"]["strategy"]["matrix"]["include"]
    }
    covered = set(cpu_wheel.group("arches").split("|"))
    assert published <= covered, (
        f"{sorted(published - covered)} is published but still resolves torch from PyPI"
    )
    assert cpu_wheel.group("dir") != "/wheels", (
        "the CPU wheel must stay out of /wheels so a torch-free extras set never installs it"
    )
    install_target = re.search(r'(?m)^.*pip wheel[^\n]*"\$\{INSTALL_TARGET\}".*$', dockerfile)
    assert install_target and f"--find-links={cpu_wheel.group('dir')}" in install_target.group(0)
    # Only the builder resolves anything; the runtime stage installs local wheels.
    assert "download.pytorch.org" not in source.split("# Stage 2:")[1]


def test_final_stage_never_deletes_what_an_earlier_layer_copied() -> None:
    """A COPY writes its bytes into a layer that no later `rm` can reclaim.

    /wheels was copied into the runtime stage and removed after the install, so the
    published image carried every dependency twice -- once as a wheel, once unpacked
    into site-packages -- and the tarball ran to ~10 GB.
    """
    instructions = _final_stage_instructions(_dockerfile())
    copied = {
        instruction.split()[-1].rstrip("/"): index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("COPY ")
    }

    for index, instruction in enumerate(instructions):
        deleted = {
            path.rstrip("/").removesuffix("/*")
            for match in _REMOVED_PATHS.finditer(instruction)
            for path in match.group("paths").split()
        }
        stale = sorted(
            destination
            for destination, copied_at in copied.items()
            if copied_at < index
            and any(destination == gone or destination.startswith(f"{gone}/") for gone in deleted)
        )
        assert not stale, (
            f"{stale} is copied into the runtime stage and deleted by a later instruction, "
            f"which frees nothing. Mount it for the command that needs it: {instruction}"
        )


def test_runtime_wheels_are_mounted_for_the_install_rather_than_copied() -> None:
    """Mounted wheels never enter a layer, so the image holds one copy of each dependency."""
    final_stage = "\n".join(_final_stage_instructions(_dockerfile()))
    install = re.search(r"(?m)^RUN[^\n]*pip install[^\n]*/wheels[^\n]*$", final_stage)

    assert install, "the runtime stage must install the wheels the builder produced"
    assert "--mount=type=bind,from=builder,source=/wheels,target=/wheels" in install.group()
    assert "COPY --from=builder /wheels" not in final_stage


def test_cuda_dependency_stays_compatible_with_cuda12() -> None:
    import tomllib

    from packaging.requirements import Requirement

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    requirements = config["project"]["optional-dependencies"]["editorial-cuda"]
    ort = next(Requirement(value) for value in requirements if value.startswith("onnxruntime-gpu"))
    assert "1.26.0" in ort.specifier
    assert "1.27.0" not in ort.specifier
    assert "1.28.0" not in ort.specifier


def test_quickstart_compose_sets_no_cpu_quota() -> None:
    """Docker's `cpus:` is a CFS quota, and not every kernel has the controller.

    Synology DSM on cgroup v1 is built without CFS bandwidth, so the shipped
    limit refused the whole `up` on a DS423+ with `NanoCPUs can not be set, as
    your kernel does not support CPU CFS scheduler or the cgroup is not mounted`.
    Memory limits stay: those work on every host tested.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())

    for name, service in compose["services"].items():
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        assert "cpus" not in limits, name
        assert limits.get("memory"), name


CAPTIONER_SERVICE = "immich-memories-captioner"
HWACCEL_CAPTIONER = REPO_ROOT / "docker" / "hwaccel.captioner.yml"


def _commented_block(service: str, first_line: str) -> dict:
    """A block docker-compose.yml ships commented out inside one service, read as YAML.

    The published file names no `extends:` (#882), so what a checkout gets from
    docker/hwaccel.*.yml a downloaded file has to get from its own comments. Read
    back here so the two cannot say different things.
    """
    lines = (REPO_ROOT / "docker-compose.yml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{service}:")
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == f"# {first_line}")
    indent = lines[start].index("#")
    block = []
    for line in lines[start:]:
        body = line[indent:]
        if not body.startswith("#"):
            break
        block.append(body[1:].removeprefix(" "))
    return yaml.safe_load("\n".join(block))


def test_the_captioner_reaches_a_card_the_way_the_inference_service_does() -> None:
    """One tag and one device, and neither of them restates what the server serves.

    `extends` replaces a list rather than appending to it, so a cuda variant
    carrying `--n-gpu-layers` in `command:` would carry a second copy of the
    alias, the projector path and the context size with it. llama-server reads
    the same setting from LLAMA_ARG_N_GPU_LAYERS, and a mapping merges.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    cuda = yaml.safe_load(HWACCEL_CAPTIONER.read_text())["services"]["cuda"]
    captioner = compose["services"][CAPTIONER_SERVICE]

    assert "command" not in cuda and "image" not in cuda
    assert cuda["environment"]["LLAMA_ARG_N_GPU_LAYERS"] == "99"
    device = cuda["deploy"]["resources"]["reservations"]["devices"][0]
    assert device["driver"] == "nvidia" and device["capabilities"] == ["gpu"]
    # The image is the other half, and it cannot come through `extends`: the
    # extending service's own `image:` wins, so the tag has to be a variable.
    assert captioner["image"].endswith(":${CAPTIONER_TAG:-server}")
    # One llama.cpp image on the host, not two: the downloader only curls and
    # hashes, so it has no reason to pull the CPU build beside a CUDA one.
    assert compose["services"]["immich-memories-caption-models"]["image"] == captioner["image"]


def test_the_captioners_commented_gpu_block_says_what_its_overlay_says() -> None:
    """The reservation exists twice, for the checkout and for the curled file."""
    cuda = yaml.safe_load(HWACCEL_CAPTIONER.read_text())["services"]["cuda"]

    assert _commented_block(CAPTIONER_SERVICE, "reservations:") == cuda["deploy"]["resources"]
    assert _commented_block(CAPTIONER_SERVICE, "environment:") == {
        "environment": cuda["environment"]
    }


def test_the_captioner_on_a_card_keeps_the_memory_limit_and_takes_no_cpu_quota() -> None:
    """A GPU changes where the layers run, not which kernels refuse `cpus:`.

    The Synology that refused `NanoCPUs can not be set` is the same host whether
    or not a card is attached, and the weights are still mapped into host memory
    while they are copied to the device.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    shipped = compose["services"][CAPTIONER_SERVICE]["deploy"]["resources"]
    cuda = yaml.safe_load(HWACCEL_CAPTIONER.read_text())["services"]["cuda"]

    assert shipped["limits"]["memory"]
    assert "cpus" not in shipped["limits"]
    assert "limits" not in cuda["deploy"]["resources"], "the variant must not restate the limits"


def _stage_instructions(source: str) -> list[list[str]]:
    lines = [
        line.strip()
        for line in _logical_instructions(source).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    stages: list[list[str]] = []
    for line in lines:
        if line.startswith("FROM "):
            stages.append([])
        if stages:
            stages[-1].append(line)
    return stages


def _first(stage: list[str], predicate) -> int:
    return next(index for index, line in enumerate(stage) if predicate(line))


def test_a_code_only_commit_reuses_the_dependency_layers() -> None:
    """Source and the per-commit APP_VERSION come after every dependency download (#1280)."""
    builder, runtime = _stage_instructions(_dockerfile())

    deps = _first(builder, lambda line: "--wheel-dir=/deps" in line)
    torch = _first(builder, lambda line: "--wheel-dir=/torch-cpu" in line)
    source = _first(builder, lambda line: line.startswith("COPY src/"))
    version = _first(builder, lambda line: line == "ARG APP_VERSION")
    assert max(deps, torch) < source < version
    app_wheels = _first(builder, lambda line: "INSTALL_TARGET=" in line)
    assert "--find-links=/deps" in builder[app_wheels]

    system_packages = _first(runtime, lambda line: "apt-get install" in line)
    assert system_packages < _first(runtime, lambda line: line == "ARG APP_VERSION")
