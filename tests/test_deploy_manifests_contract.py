"""Contracts for the shipped Kubernetes manifests and Terraform module (issue #307).

These files never boot in a cluster during CI, so the properties that made them
fail as shipped are pinned here: the Secret is applied, the config directory is
writable, probes hit the real endpoints, no GPU is required by default, and no
stale config keys survive.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_ROOT = REPO_ROOT / "deploy" / "kubernetes"
K8S_DIR = K8S_ROOT / "base"
TF_DIR = REPO_ROOT / "deploy" / "terraform"

# The image runs as `immich`, UID 1000, HOME=/home/immich (docker/Dockerfile).
CONFIG_DIR = "/home/immich/.immich-memories"
OUTPUT_DIR = "/app/output"
IMMICH_PORT = 2283
# The caption server default (editorial.preparation.caption_base_url).
CAPTION_PORT = 8092
MODELS_DIR = "/models"
# ggml-org/SmolVLM2-500M-Video-Instruct-GGUF, the revision and file digests the
# captioner overlay downloads. Docs and manifest must not drift apart.
CAPTION_GGUF_REVISION = "ccd7aae53bcb1997355c2f094959e72b3642ce17"
CAPTION_GGUF_SHA256 = (
    "6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc",
    "921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b",
)
# Every path-valued setting a pinned artifact lands on: the encoder, the
# sensitive-content export and the Hugging Face cache the detectors read.
MODEL_PATH_ENV = (
    "IMMICH_MEMORIES_TRIAGE__ENCODER",
    "IMMICH_MEMORIES_EDITORIAL__PREPARATION__MARQO_ONNX",
    "IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR",
)


def _yaml_docs(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _kustomization() -> dict:
    return yaml.safe_load((K8S_DIR / "kustomization.yaml").read_text())


def _deployment() -> dict:
    docs = _yaml_docs(K8S_DIR / "deployment.yaml")
    return next(doc for doc in docs if doc["kind"] == "Deployment")


def _pod_specs() -> list[tuple[str, dict]]:
    """Every pod template shipped in the base directory, labelled by its file."""
    specs = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for doc in _yaml_docs(path):
            kind = doc.get("kind")
            if kind in ("Deployment", "Job"):
                specs.append(
                    (f"{path.name}:{doc['metadata']['name']}", doc["spec"]["template"]["spec"])
                )
            elif kind == "CronJob":
                pod = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                specs.append((f"{path.name}:{doc['metadata']['name']}", pod))
    return specs


def _deploy_texts() -> dict[str, str]:
    return {
        str(path.relative_to(REPO_ROOT)): path.read_text()
        for path in list(K8S_ROOT.rglob("*")) + list(TF_DIR.rglob("*"))
        if path.is_file()
    }


def test_kustomization_applies_the_secret_the_deployment_needs() -> None:
    """`kubectl apply -k .` must not leave the pod in CreateContainerConfigError."""
    resources = _kustomization()["resources"]

    assert "secret.yaml" in resources
    assert "configmap.yaml" not in resources
    assert (K8S_DIR / "secret.yaml.example").exists()
    assert not (K8S_DIR / "configmap.yaml").exists()


def test_kustomization_pins_a_published_image_tag() -> None:
    """Published image tags carry no `v` prefix; `v1.0.0` never existed."""
    kustomization = _kustomization()
    images = kustomization["images"]
    image = next(
        entry
        for entry in images
        if entry["name"] == "ghcr.io/sam-dumont/immich-video-memory-generator"
    )

    assert re.fullmatch(r"\d+\.\d+\.\d+", str(image["newTag"])), image
    assert "commonLabels" not in kustomization


def test_the_two_inference_overlays_name_the_same_release() -> None:
    """The CUDA image is the CPU tag with `-cuda` on the end, and only it carries that provider.

    They drift apart silently: a cluster applying one overlay and reading the
    other's release notes gets a service that answers with different weights.
    """
    tags = {}
    for overlay in ("inference", "inference-cuda"):
        kustomization = yaml.safe_load(
            (K8S_ROOT / "overlays" / overlay / "kustomization.yaml").read_text()
        )
        entry = next(
            image for image in kustomization["images"] if image["name"].endswith("/inference")
        )
        tags[overlay] = str(entry["newTag"])

    assert re.fullmatch(r"\d+\.\d+\.\d+", tags["inference"]), tags
    assert tags["inference-cuda"] == f"{tags['inference']}-cuda", tags


def test_only_the_kustomization_pin_names_a_concrete_version() -> None:
    """One `0.59.2` was copied into five prose sites and all six rotted together (#732).

    The pin is the only number a reader should trust, so everything else states the rule
    (`vX.Y.Z` ships as `X.Y.Z`) instead of quoting a release that goes stale within a day.
    """
    offenders = {}
    for name, text in _deploy_texts().items():
        if name.endswith("kustomization.yaml"):
            text = re.sub(r"(?m)^\s*newTag:.*$", "", text)
        # A dotted quad is an address, not a release: the captioner binds 0.0.0.0.
        text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "", text)
        if found := re.findall(r"\d+\.\d+\.\d+", text):
            offenders[name] = found

    assert not offenders, offenders


def test_config_directory_is_a_writable_persistent_volume() -> None:
    """The app writes cache/, projects/, cache.db and .storage_secret at startup."""
    for label, pod in _pod_specs():
        container = pod["containers"][0]
        mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
        volumes = {volume["name"]: volume for volume in pod["volumes"]}

        config_mount = mounts[CONFIG_DIR]
        assert not config_mount.get("readOnly"), label
        assert "subPath" not in config_mount, label
        assert "persistentVolumeClaim" in volumes[config_mount["name"]], label

        output_mount = mounts[OUTPUT_DIR]
        assert "persistentVolumeClaim" in volumes[output_mount["name"]], label

        tmp = volumes[mounts["/tmp"]["name"]]["emptyDir"]
        assert re.fullmatch(r"([2-9]|\d{2,})Gi", tmp["sizeLimit"]), label

        assert not any(path.startswith("/home/appuser") for path in mounts), label
        assert "/output" not in mounts, label


def test_pods_write_output_to_the_mounted_directory() -> None:
    for label, pod in _pod_specs():
        env = {item["name"]: item.get("value") for item in pod["containers"][0].get("env", [])}
        assert env.get("IMMICH_MEMORIES_OUTPUT__DIRECTORY") == OUTPUT_DIR, label
        assert "HOME" not in env, label


def test_pods_read_immich_credentials_from_the_secret() -> None:
    for label, pod in _pod_specs():
        container = pod["containers"][0]
        secret_refs = {ref["secretRef"]["name"] for ref in container.get("envFrom", [])}
        assert "immich-memories-secrets" in secret_refs, label


def test_probes_use_liveness_and_readiness_endpoints() -> None:
    """`/health` and `/` always answer 200, so they cannot signal anything."""
    container = _deployment()["spec"]["template"]["spec"]["containers"][0]

    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"


def test_base_manifests_do_not_require_a_gpu() -> None:
    """The base must schedule on a CPU-only cluster; GPU is an overlay."""
    for label, pod in _pod_specs():
        container = pod["containers"][0]
        assert "runtimeClassName" not in pod, label
        assert "nodeSelector" not in pod, label
        assert "tolerations" not in pod, label
        for section in ("requests", "limits"):
            assert "nvidia.com/gpu" not in container["resources"][section], label
        env_names = {item["name"] for item in container.get("env", [])}
        assert not any(name.startswith("NVIDIA_") for name in env_names), label


def test_gpu_overlay_adds_nvidia_scheduling_to_the_deployment() -> None:
    overlay = yaml.safe_load((K8S_ROOT / "overlays" / "gpu" / "kustomization.yaml").read_text())
    patch_name = overlay["patches"][0]["path"]
    patch = yaml.safe_load((K8S_ROOT / "overlays" / "gpu" / patch_name).read_text())
    pod = patch["spec"]["template"]["spec"]

    assert overlay["resources"] == ["../../base"]
    assert patch["kind"] == "Deployment"
    assert pod["runtimeClassName"] == "nvidia"
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert pod["nodeSelector"] == {"nvidia.com/gpu.present": "true"}


def test_security_context_is_kept() -> None:
    for label, pod in _pod_specs():
        assert pod["securityContext"]["runAsUser"] == 1000, label
        assert pod["securityContext"]["fsGroup"] == 1000, label
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault", label
        container = pod["containers"][0]
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"], label
        assert container["securityContext"]["allowPrivilegeEscalation"] is False, label


def test_network_policy_allows_the_immich_port() -> None:
    policy = _yaml_docs(K8S_DIR / "networkpolicy.yaml")[0]
    egress_ports = {
        port["port"] for rule in policy["spec"]["egress"] for port in rule.get("ports", [])
    }

    assert IMMICH_PORT in egress_ports
    assert 3001 not in egress_ports


def test_service_file_ships_no_ingress() -> None:
    """Auth is off by default; an Ingress must be an explicit opt-in."""
    kinds = {doc["kind"] for doc in _yaml_docs(K8S_DIR / "service.yaml")}
    resources = _kustomization()["resources"]

    assert kinds == {"Service"}
    assert not any(name.startswith("ingress") for name in resources)
    assert (K8S_DIR / "ingress.yaml.example").exists()


def test_batch_jobs_use_realistic_durations_and_current_flags() -> None:
    """`--duration` is seconds: 10 produced a ten-second video."""
    text = (K8S_DIR / "job.yaml").read_text()

    for match in re.finditer(r"--duration\s+\"?(\d+)", text):
        assert int(match.group(1)) >= 60, match.group(0)
    assert "--cooldown" in text
    assert "/output/" not in text.replace(OUTPUT_DIR, "")


def test_no_stale_config_keys_or_paths_survive_in_deploy_files() -> None:
    stale = (
        "appuser",
        "PIXABAY",
        "OLLAMA_URL",
        "ollama_url",
        "ollama_model",
        "content_analysis.provider",
        "CONTENT_ANALYSIS__ENABLED",
        'provider = "auto"',
        "hardware_backend",
        "target_duration_seconds",
        "output_orientation",
        "v1.0.0",
        "3001",
        "0.2.0",
    )
    for name, text in _deploy_texts().items():
        for needle in stale:
            assert needle not in text, f"{needle!r} in {name}"


def test_terraform_module_defaults_to_cpu_and_writable_state() -> None:
    variables = (TF_DIR / "variables.tf").read_text()
    main = (TF_DIR / "main.tf").read_text()

    gpu_default = re.search(r'variable "gpu_enabled"[^}]*default\s*=\s*(\w+)', variables, re.S)
    assert gpu_default and gpu_default.group(1) == "false"
    assert f'"{CONFIG_DIR}"' in main
    assert f'"{OUTPUT_DIR}"' in main
    assert not re.search(r"read_only\s*=\s*true", main)
    assert "IMMICH_MEMORIES_OUTPUT__DIRECTORY" in main
    probe_paths = re.findall(r'http_get \{\s*path\s*=\s*"([^"]+)"', main)
    assert probe_paths == ["/health/live", "/health/ready"]
    assert 'dynamic "node_selector"' not in main
    assert "kubernetes_config_map" not in main


def test_terraform_examples_only_set_declared_variables() -> None:
    declared = set(re.findall(r'variable "(\w+)"', (TF_DIR / "variables.tf").read_text()))
    for example in ("basic", "production"):
        example_dir = TF_DIR / "examples" / example
        example_vars = set(
            re.findall(r'variable "(\w+)"', (example_dir / "variables.tf").read_text())
        )
        tfvars = (example_dir / "terraform.tfvars.example").read_text()
        assigned = set(re.findall(r"(?m)^(\w+)\s*=", tfvars))
        assert assigned <= example_vars, f"{example}: {sorted(assigned - example_vars)}"
        module_block = re.search(
            r'module "immich_memories" \{\n(.*?)\n\}', (example_dir / "main.tf").read_text(), re.S
        )
        assert module_block, example
        module_args = set(re.findall(r"(?m)^  (\w+)\s*=", module_block.group(1)))
        assert module_args - {"source"} <= declared, f"{example}: {sorted(module_args - declared)}"


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize("target", ["base", "overlays/gpu"])
def test_kustomize_renders_with_a_secret_created_from_the_example(
    tmp_path: Path, target: str
) -> None:
    """The documented quick start: copy the secret example, then `kubectl apply -k`."""
    workdir = tmp_path / "kubernetes"
    shutil.copytree(K8S_ROOT, workdir)
    shutil.copy(workdir / "base" / "secret.yaml.example", workdir / "base" / "secret.yaml")

    result = subprocess.run(
        ["kubectl", "kustomize", str(workdir / target)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = list(yaml.safe_load_all(result.stdout))
    kinds = {doc["kind"] for doc in rendered}
    assert {"Namespace", "Secret", "PersistentVolumeClaim", "Deployment", "Service"} <= kinds
    assert "Ingress" not in kinds
    deployment = next(doc for doc in rendered if doc["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert re.fullmatch(
        r"ghcr\.io/sam-dumont/immich-video-memory-generator:\d+\.\d+\.\d+", container["image"]
    )
    has_gpu = "nvidia.com/gpu" in container["resources"]["limits"]
    assert has_gpu == (target == "overlays/gpu")


def _kustomize(path: Path) -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(path)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize("target", ["overlays/inference", "overlays/inference-cuda"])
def test_inference_overlays_build_without_the_secret(target: str) -> None:
    """The inference service holds no credential, so its overlay must not need base/secret.yaml."""
    rendered = _kustomize(K8S_ROOT / target)

    assert "Secret" not in {doc["kind"] for doc in rendered}
    deployment = next(doc for doc in rendered if doc["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"][0]["containerPort"] == CAPTION_PORT
    cuda = target.endswith("-cuda")
    assert ("nvidia.com/gpu" in container["resources"]["limits"]) == cuda
    assert container["image"].endswith("-cuda") == cuda


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize("target", ["overlays/inference", "overlays/inference-cuda"])
def test_the_inference_pod_can_write_everywhere_it_downloads_to(target: str) -> None:
    """readOnlyRootFilesystem plus a Hugging Face cache under $HOME is how a cold
    PVC answered 503 to every request: the snapshot download had nowhere to land."""
    deployment = next(doc for doc in _kustomize(K8S_ROOT / target) if doc["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {entry["name"]: entry.get("value") for entry in container["env"]}
    mounts = {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}

    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert env["TMPDIR"] == "/tmp"
    assert "emptyDir" in volumes[mounts["/tmp"]]
    # One variable covers the hub, xet and assets caches: huggingface_hub derives
    # all three from HF_HOME unless each is named separately.
    assert env["HF_HOME"] == "/cache/huggingface"
    assert "persistentVolumeClaim" in volumes[mounts["/cache"]]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_the_lan_overlay_adds_a_service_and_changes_nothing_else() -> None:
    """A patch on the ClusterIP Service would put every in-cluster caller on an external address."""
    rendered = _kustomize(K8S_ROOT / "overlays/inference-lan")

    assert [doc["kind"] for doc in rendered] == ["Service"]
    service = rendered[0]
    assert service["metadata"]["name"] == "inference-lan"
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["spec"]["ports"][0]["port"] == CAPTION_PORT
    # Same pods as the ClusterIP Service, which keeps its own name and type.
    assert service["spec"]["selector"] == {"app.kubernetes.io/name": "immich-memories-inference"}


def test_every_pod_can_reach_the_pinned_encoder_and_the_detector_cache() -> None:
    """A first cut stops without the encoder, and the root filesystem is read-only."""
    for label, pod in _pod_specs():
        for container in pod.get("initContainers", []) + pod["containers"]:
            where = f"{label}:{container['name']}"
            mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
            env = {entry["name"]: entry.get("value") for entry in container["env"]}

            assert mounts.get("models") == MODELS_DIR, where
            for key in MODEL_PATH_ENV:
                assert env[key].startswith(f"{MODELS_DIR}/"), f"{where}: {key}"


def test_every_pod_fetches_the_pinned_models_before_its_first_cut() -> None:
    """A fresh models claim holds nothing, and prepare is where that surfaces.

    The pod came up, the cut ran, and it stopped naming three files nobody had
    told the operator to fetch. The init step is the same image running the same
    `models fetch` the docs give a Docker user, so an empty claim fills itself
    and a warm one costs a `test`.
    """
    for label, pod in _pod_specs():
        fetch = next(
            (item for item in pod.get("initContainers", []) if item["name"] == "fetch-models"),
            None,
        )
        assert fetch, label
        assert fetch["image"] == pod["containers"][0]["image"], label
        script = " ".join(fetch["command"])
        assert "immich-memories models fetch" in script, label
        # Idempotent: a claim that already carries all three is left alone, so a
        # restart and a nightly CronJob do not go back to the network.
        assert script.count("test -") == len(MODEL_PATH_ENV), label
        mounts = {mount["mountPath"] for mount in fetch["volumeMounts"]}
        assert {MODELS_DIR, CONFIG_DIR} <= mounts, label


def test_network_policy_allows_the_caption_endpoint() -> None:
    """The shipped policy used to block the caption port this app documents by default."""
    policy = _yaml_docs(K8S_DIR / "networkpolicy.yaml")[0]
    egress_ports = {
        port["port"] for rule in policy["spec"]["egress"] for port in rule.get("ports", [])
    }

    assert {CAPTION_PORT, 11434} <= egress_ports


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_the_captioner_overlay_serves_the_alias_the_app_demands() -> None:
    """`tier: full` refuses any endpoint that advertises something else at /models.

    Three flags carry the whole contract, and each has a failure that looks like
    something else: no `--alias` and preflight reads "serves another model", no
    `--mmproj` and every picture is described as if it were blank.
    """
    from immich_memories.analysis.editorial_description_contract import API_MODEL

    rendered = _kustomize(K8S_ROOT / "overlays/captioner")

    assert "Secret" not in {doc["kind"] for doc in rendered}
    deployment = next(doc for doc in rendered if doc["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    args = container["args"]

    assert container["ports"][0]["containerPort"] == CAPTION_PORT
    assert args[args.index("--alias") + 1] == API_MODEL
    assert args[args.index("--mmproj") + 1].startswith(f"{MODELS_DIR}/mmproj-")
    assert "--jinja" in args
    assert "nvidia.com/gpu" not in container["resources"]["limits"]

    service = next(doc for doc in rendered if doc["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == CAPTION_PORT


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_the_captioner_init_container_pins_the_weights_it_downloads() -> None:
    """llama-server serves whatever file is at the path, so the digest is the only pin."""
    rendered = _kustomize(K8S_ROOT / "overlays/captioner")
    deployment = next(doc for doc in rendered if doc["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    init = pod["initContainers"][0]
    script = "\n".join(init["args"])

    assert CAPTION_GGUF_REVISION in script
    for digest in CAPTION_GGUF_SHA256:
        assert digest in script
    assert "sha256sum -c" in script
    # The serving container mounts the same claim read-only: nothing rewrites a
    # verified file after the check.
    assert next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "models")[
        "readOnly"
    ]
