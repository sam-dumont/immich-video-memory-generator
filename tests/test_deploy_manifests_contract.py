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


def test_only_the_kustomization_pin_names_a_concrete_version() -> None:
    """One `0.59.2` was copied into five prose sites and all six rotted together (#732).

    The pin is the only number a reader should trust, so everything else states the rule
    (`vX.Y.Z` ships as `X.Y.Z`) instead of quoting a release that goes stale within a day.
    """
    offenders = {}
    for name, text in _deploy_texts().items():
        if name.endswith("base/kustomization.yaml"):
            text = re.sub(r"(?m)^\s*newTag:.*$", "", text)
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
