---
sidebar_label: "Kubernetes + GPU"
---

# Kubernetes + GPU Setup

For Kubernetes clusters with GPU nodes. This is the most advanced setup: if you're not already running K8s, start with [Docker](../installation/docker.md) instead.

## Who this is for

You run a Kubernetes cluster with NVIDIA GPU nodes (on-prem, cloud, or hybrid). You want Immich Memories as a scheduled workload with GPU-accelerated encoding and optional music generation pods.

## Prerequisites

1. **NVIDIA GPU Operator** installed:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace
```

2. **Storage class** available for PersistentVolumeClaims
3. **Immich** accessible from the cluster (same namespace, different namespace, or external)

## Deploy with Kustomize

The manifests live in `deploy/kubernetes/` in the repo: a CPU-only `base/` and an
`overlays/gpu/` patch that adds the NVIDIA bits. Detailed manifest reference:
[Kubernetes deployment](../installation/kubernetes.md).

```bash
cd deploy/kubernetes

# Secret: Immich URL + API key
cp base/secret.yaml.example base/secret.yaml
vim base/secret.yaml

# GPU nodes
kubectl apply -k overlays/gpu
# (CPU only: kubectl apply -k base)
```

`kubectl kustomize overlays/gpu` shows the rendered result. `base/kustomization.yaml` pins the
image tag (no `v` prefix: release `vX.Y.Z` is tag `X.Y.Z`). The checked-in pin is only current as
of whenever someone last bumped it, so check it against the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) before you
apply.

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

Open [http://localhost:8080](http://localhost:8080). No Ingress is shipped: enable
[authentication](../configuration/authentication.mdx) first, then copy
`base/ingress.yaml.example` into place.

## GPU resource requests

`overlays/gpu/deployment-gpu.yaml` requests one `nvidia.com/gpu`, sets `runtimeClassName: nvidia`
and the `NVIDIA_*` env vars. Adjust there:

```yaml
resources:
  requests:
    nvidia.com/gpu: "1"
  limits:
    nvidia.com/gpu: "1"
```

The base Deployment keeps `2Gi/1000m` requests and `8Gi/4000m` limits.

## Node selection

The overlay schedules on nodes with `nvidia.com/gpu.present=true` (set by the GPU Operator) and
tolerates the `nvidia.com/gpu` taint. If your cluster uses different labels:

```yaml
nodeSelector:
  nvidia.com/gpu.present: "true"
  # Or your custom label:
  # gpu-node: "true"
```

For music generation pods (MusicGen/ACE-Step), you might want separate node affinity rules to schedule on nodes with more VRAM.

## Batch jobs

Run one-off generation without the UI:

```bash
kubectl apply -f base/job.yaml
kubectl logs -n immich-memories -f job/immich-memories-generate
```

`--duration` is in **seconds** (the example job uses `600`). The jobs are CPU-only as shipped;
copy the fields from `overlays/gpu/deployment-gpu.yaml` into the pod spec for GPU nodes. They
share the Deployment's `ReadWriteOnce` PVCs, so the job pod has to land on the same node, or
skip the CronJobs and set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment instead.

## Storage

Default PVC sizes:

| Volume | Size | Purpose |
|-----|------|---------|
| State PVC | 50Gi | `~/.immich-memories`: configuration, banks, run history, downloads and previews; `~/.cache` and `/tmp` use separate subPaths |
| Output PVC | 50Gi | mounted at `/app/output`: generated videos |
| Models PVC | 5Gi | mounted at `/models`: the pinned DINOv2 export and the detector Hugging Face cache, written by `immich-memories models fetch` |

There is no ConfigMap: connection details come from the Secret, everything else from
`IMMICH_MEMORIES_*` env vars or the UI settings page (which writes `config.yaml` on the PVC).

`cache/annotations.sqlite` on the cache PVC holds every caption, head answer, detector verdict and
reading the editor has banked. That is the valuable data: losing it means re-reading your whole
library. Stop writers before copying or snapshotting the state PVC. `cache backup` copies
`cache.db` only, which holds run history and automation state, not the annotation banks.

## Secrets management

Don't commit plain secrets to git. Use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets) or your cluster's secret management:

```bash
kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml
kubectl apply -f base/sealed-secret.yaml
```

## Health monitoring

`/health/ready` returns `200` when config is present and Immich is reachable, `503` otherwise, with a JSON body like:

```json
{
  "status": "ready",
  "configuration": "configured",
  "immich_reachable": true,
  "last_successful_run": "2025-12-15T10:30:00",
  "version": "X.Y.Z"
}
```

`/health/live` only says the process is up. `/health` returns the same JSON as `/health/ready` but
always with HTTP `200` (`ready` is rewritten to `ok`, degraded stays degraded), so it is useless as a probe: the manifests use
`/health/live` for liveness and `/health/ready` for readiness.

Point your monitoring (Uptime Kuma, Prometheus blackbox exporter, etc.) at `/health/ready` on port 8080.

## What works / what doesn't

Same as the [Linux + NVIDIA](./linux-nvidia.md) setup: the card does NVENC encoding and GPU titles, and nothing else in this pod runs on it. The Kubernetes layer adds scheduling and PVC-based storage, not scaling: the UI is single-replica.

## Sizing and validation

The defaults are starting requests, not per-resolution guarantees. The state PVC must fit
10 GB of thumbnails, 10 GB of videos and 2 GB of previews at configured defaults, plus banks,
active files and FFmpeg scratch. Scratch persists across pod restarts; clean it only while app
and batch writers are stopped. See [storage details](../installation/kubernetes.md#how-the-pod-is-wired).

The tests render Kustomize; they do not apply the manifests to a live cluster. Use
[Running modes](../running-modes.md) for recorded selection timings and measure your own render
and peak memory before sizing a GPU node around them.

## Further reading

- [Terraform deployment](../installation/terraform.md) for infrastructure-as-code provisioning
- [Kubernetes manifests](../installation/kubernetes.md) for detailed manifest reference
