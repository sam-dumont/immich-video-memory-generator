"""What the inference images and their compose wiring must keep doing.

Both variants come out of one Dockerfile, and the traps they have to avoid are
build-time ones a unit test is the only cheap guard against: a CUDA wheel in the
cpu image, overlapping CPU/GPU runtime packages, and a fatbin without cubins for
the cards people actually own.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.inference"
HWACCEL = REPO_ROOT / "docker" / "hwaccel.inference.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
SERVICE = "immich-memories-inference"
PORT = "8092"

# Every architecture the -cuda image must carry compiled kernels for, and why
# it is on the list: 61 GTX 10-series and the P40s bought for cheap VRAM, 75
# T4/T1000/RTX 20, 86 RTX 30, 89 RTX 40/L4, 120 RTX 50.
REAL_ARCHITECTURES = ("61", "75", "86", "89", "120")


def instructions(source: str) -> list[str]:
    """Logical Dockerfile lines: continuations joined, comments dropped."""
    joined = re.sub(r"\\\s*\n\s*", " ", source)
    return [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def stage(name: str) -> list[str]:
    """The instructions of one build stage, in file order."""
    lines = instructions(DOCKERFILE.read_text())
    starts = [index for index, line in enumerate(lines) if re.match(rf"FROM .+ AS {name}$", line)]
    assert starts, f"no stage named {name}"
    following = [
        index for index in range(starts[0] + 1, len(lines)) if lines[index].startswith("FROM ")
    ]
    return lines[starts[0] : following[0] if following else len(lines)]


def compose_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"][SERVICE]


def commented_gpu_reservation() -> dict:
    """The GPU block docker-compose.yml ships commented out, read as YAML."""
    lines = COMPOSE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "# reservations:")
    block = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("#") or stripped == "#":
            break
        indent = " " * (len(line) - len(line.lstrip()))
        block.append(indent + stripped.removeprefix("# "))
    return yaml.safe_load(textwrap.dedent("\n".join(block)))


def compose_image(service: str) -> str:
    return yaml.safe_load(COMPOSE.read_text())["services"][service]["image"]


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_each_device_selects_a_builder_and_a_runtime_stage(device: str) -> None:
    lines = instructions(DOCKERFILE.read_text())

    assert stage(f"builder-{device}")
    assert stage(f"prod-{device}")
    assert "FROM builder-${DEVICE} AS builder" in lines
    assert "FROM prod-${DEVICE} AS prod" in lines


def test_each_variant_installs_only_its_device_extra() -> None:
    cpu = " ".join(stage("builder-cpu"))
    cuda = " ".join(stage("builder-cuda"))
    build = " ".join(stage("builder"))
    assert "INFERENCE_EXTRA=editorial" in cpu
    assert "INFERENCE_EXTRA=editorial-cuda" in cuda
    assert '".[${INFERENCE_EXTRA}]"' in build
    assert "--constraint /constraints.txt" in build
    assert "pip check" in build
    assert "torch" not in build
    assert "pip uninstall" not in build


def test_the_cuda_variant_is_built_on_a_cuda_runtime_with_cudnn() -> None:
    # onnxruntime-gpu's wheels are built against CUDA 12 and cuDNN 9; a base
    # without them loads and then has no CUDA provider to offer.
    base = next(
        line for line in instructions(DOCKERFILE.read_text()) if line.startswith("ARG CUDA_BASE=")
    )

    assert "nvidia/cuda:12." in base
    assert "cudnn" in base
    # Pinned by digest, like the app image's base: a mutable tag is a hole.
    assert "@sha256:" in base


def test_every_cuda_architecture_people_own_is_compiled_not_jitted() -> None:
    # A Turing card compiled 858 PTX modules on its first request against the
    # stock llama.cpp image: 31.3 s once per container, with a cache that dies
    # with it. A service that unloads on idle pays that on every reload.
    architectures = next(
        line for line in stage("builder-cuda") if "CMAKE_CUDA_ARCHITECTURES" in line
    )

    for architecture in REAL_ARCHITECTURES:
        assert f"{architecture}-real" in architectures
    # PTX for anything unlisted, so no card is excluded — it just pays one JIT.
    assert "-virtual" in architectures


def test_the_cuda_jit_cache_outlives_the_container_and_is_bounded() -> None:
    cuda = " ".join(stage("prod-cuda"))

    assert "CUDA_CACHE_PATH=/cache/cuda-jit" in cuda
    assert "CUDA_CACHE_MAXSIZE=536870912" in cuda
    # Inert on cpu, so it must not be set where it would only confuse.
    assert "CUDA_CACHE" not in " ".join(stage("prod-cpu"))


def test_the_image_publishes_the_port_the_caption_default_already_names() -> None:
    prod = " ".join(stage("prod"))

    assert f"EXPOSE {PORT}" in prod
    assert f"IMMICH_MEMORIES_INFERENCE_PORT={PORT}" in prod
    # The service's own default is loopback; a container has to bind wider.
    assert "IMMICH_MEMORIES_INFERENCE_HOST=0.0.0.0" in prod


def test_the_healthcheck_needs_nothing_the_slim_image_lacks() -> None:
    healthcheck = next(line for line in stage("prod") if line.startswith("HEALTHCHECK"))

    assert "python -c" in healthcheck
    assert "curl" not in healthcheck


def test_the_service_tree_is_importable_in_the_image() -> None:
    prod = " ".join(stage("prod"))

    assert "COPY services/inference /app/services/inference" in prod
    assert "PYTHONPATH=/app/services/inference" in prod


def test_the_model_cache_is_a_volume_the_service_user_can_write() -> None:
    prod = " ".join(stage("prod"))

    assert "IMMICH_MEMORIES_INFERENCE_CACHE_DIR=/cache" in prod
    assert "chown -R immich:immich /cache" in prod
    # Everything fetched lands on the one volume, detector snapshots included,
    # or a restart re-downloads them.
    assert "IMMICH_MEMORIES_INFERENCE_DETECTOR_CACHE_DIR=/cache/huggingface" in prod
    assert compose_service()["volumes"] == ["immich-memories-model-cache:/cache"]


def test_the_backends_are_cpu_and_a_cuda_device_reservation() -> None:
    backends = yaml.safe_load(HWACCEL.read_text())["services"]

    assert backends == {
        "cpu": {},
        "cuda": {
            "deploy": {
                "resources": {
                    "reservations": {
                        "devices": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]
                    }
                }
            }
        },
    }


def test_the_published_compose_file_reads_no_file_beside_itself() -> None:
    # `extends:` is resolved when compose loads the file, whatever profiles are
    # on, so naming docker/hwaccel.inference.yml here broke the documented
    # `curl -O … && docker compose up` in an empty directory before any profile
    # was considered (#882). `make compose-check` proves the parse; this says
    # which line would break it again.
    assert "extends" not in compose_service()


def test_the_commented_gpu_block_says_what_the_hwaccel_overlay_says() -> None:
    # The CUDA reservation now exists twice: the overlay for checkouts, and a
    # commented block in the published file. They have to stay one answer.
    cuda = yaml.safe_load(HWACCEL.read_text())["services"]["cuda"]

    assert commented_gpu_reservation() == cuda["deploy"]["resources"]


def test_the_compose_service_publishes_inference_on_loopback_only() -> None:
    # The service holds no credential, but it will answer anything that reaches
    # it. Reaching it from another machine is a deliberate edit, as with the UI.
    assert compose_service()["ports"] == [f"127.0.0.1:{PORT}:{PORT}"]


def test_the_quickstart_does_not_start_a_service_nothing_uses_yet() -> None:
    # The app has no facts_base_url switch until W8, and the models are a
    # ~500 MB fetch: `docker compose up` must stay one container.
    assert compose_service()["profiles"] == ["inference"]


def test_compose_pulls_the_inference_image_the_release_actually_publishes() -> None:
    """The compose name must be the name the manifest job pushes, segment for segment.

    They drifted: compose said `...-generator-inference` where the workflow pushes
    `...-generator/inference`, a path segment rather than a hyphen. Nothing failed
    in CI, because the only thing that resolves the name is a self-hoster running
    the `--profile inference up` line the file itself prints, against a manifest
    that has never existed.
    """
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    step = next(
        s
        for s in workflow["jobs"]["inference-manifest"]["steps"]
        if s.get("name") == "Create and push manifests"
    )
    # The app image is `ghcr.io/<github.repository>`, so it resolves the template.
    repository = compose_image("immich-memories").removeprefix("ghcr.io/").split(":")[0]
    published = step["env"]["IMAGE"].replace("${{ github.repository }}", repository)

    reference = compose_image(SERVICE)
    image, _, tag = reference.partition(":")

    assert image == published, f"{reference} is not published; the release pushes {published}"
    # The default and the documented GPU override must both be tags a release moves.
    assert tag == "${INFERENCE_TAG:-latest}"
    release_tags = set(re.findall(r"-t \$IMAGE:([^\s\"]+)", step["run"]))
    assert {"latest", "latest-cuda"} <= release_tags


def test_inference_only_analysis_cannot_create_a_release(tmp_path, monkeypatch):
    import os
    import subprocess

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    step = next(s for s in workflow["jobs"]["analyze"]["steps"] if s.get("id") == "analyze")
    fake_git = tmp_path / "git"
    fake_git.write_text("#!/bin/sh\nexit 0\n")
    fake_git.chmod(0o755)
    output = tmp_path / "outputs"
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("INFERENCE_ONLY", "true")
    monkeypatch.setenv("FORCE_VERSION", "major")
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, check=True, capture_output=True)
    assert output.read_text().splitlines() == [
        "should_release=false",
        "next_version=0+gabcdef0123456789",
    ]
    assert step["env"]["INFERENCE_ONLY"] == "${{ inputs.inference_only }}"
    guard = workflow["jobs"]["inference-build"]["if"]
    assert "!cancelled()" in guard
    assert "needs.release.result == 'success'" in guard
    # The intentionally skipped release ancestor must not skip successful
    # inference manifests through GitHub's implicit success() condition.
    manifest_guard = workflow["jobs"]["inference-manifest"].get("if", "")
    assert "!cancelled()" in manifest_guard
    assert "needs.inference-build.result == 'success'" in manifest_guard


def test_inference_digest_artifacts_round_trip_to_separate_manifests(tmp_path, monkeypatch):
    import os
    import subprocess

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    export = next(
        s for s in workflow["jobs"]["inference-build"]["steps"] if s.get("name") == "Export digest"
    )
    digests = tmp_path / "digests"
    expected = {"cpu": [], "cuda": []}
    image = "ghcr.io/example/inference"
    for index, device in enumerate(("cpu", "cpu", "cuda")):
        digest = "sha256:" + str(index) * 64
        directory = digests / f"inference-digests-{device}-{index}"
        script = export["run"].replace("/tmp/digests", str(directory))
        script = script.replace("${{ steps.build.outputs.digest }}", digest)
        subprocess.run(["bash", "-e", "-c", script], check=True, capture_output=True)
        names = [p.name for p in directory.iterdir()]
        assert names == [digest.removeprefix("sha256:")], "Artifact filenames must be portable"
        expected[device].append(f"{image}@{digest}")

    calls = tmp_path / "docker-calls"
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\n')
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_CALLS", str(calls))
    monkeypatch.setenv("IMAGE", image)
    monkeypatch.setenv("INFERENCE_ONLY", "true")
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    manifest = next(
        s
        for s in workflow["jobs"]["inference-manifest"]["steps"]
        if s.get("name") == "Create and push manifests"
    )
    script = manifest["run"].replace("/tmp/digests", str(digests))
    subprocess.run(["bash", "-e", "-c", script], check=True, capture_output=True)
    cpu, cuda = [line.split() for line in calls.read_text().splitlines()]
    assert cpu[:5] == ["buildx", "imagetools", "create", "-t", f"{image}:sha-abcdef012345"]
    assert cuda[:5] == ["buildx", "imagetools", "create", "-t", f"{image}:sha-abcdef012345-cuda"]
    assert sorted(cpu[5:]) == expected["cpu"]
    assert cuda[5:] == expected["cuda"]
