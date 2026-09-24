"""The render worker's Kubernetes manifest is hardened like the app's own (#1212)."""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def _pod_spec(path: Path) -> dict:
    deployment = next(
        doc for doc in yaml.safe_load_all(path.read_text()) if doc and doc["kind"] == "Deployment"
    )
    return deployment["spec"]["template"]["spec"]


def test_the_worker_pod_runs_as_the_app_pod_does():
    app = _pod_spec(_ROOT / "deploy/kubernetes/base/deployment.yaml")["securityContext"]
    worker = _pod_spec(_ROOT / "services/render-worker/kubernetes.yaml")["securityContext"]

    assert worker == app


def test_every_worker_container_drops_privileges_and_writes_only_to_mounts():
    pod = _pod_spec(_ROOT / "services/render-worker/kubernetes.yaml")

    for container in pod["containers"]:
        context = container["securityContext"]
        assert context["allowPrivilegeEscalation"] is False
        assert context["readOnlyRootFilesystem"] is True
        assert context["capabilities"]["drop"] == ["ALL"]
        mounted = {mount["mountPath"] for mount in container["volumeMounts"]}
        assert {"/tmp", "/home/immich/.immich-memories", "/home/immich/.cache"} <= mounted
