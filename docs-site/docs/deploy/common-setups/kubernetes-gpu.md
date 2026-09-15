---
sidebar_label: "Kubernetes + GPU"
---

# Kubernetes + GPU

You already run a cluster with NVIDIA GPU nodes and want this on it as a workload: a Deployment
for the UI, a Job for batch generation, NVENC and the GPU title kernels on the card. If you are
not already running K8s, start with [Docker](../installation/docker.md) instead.

## Architecture

![Kubernetes setup diagram](/img/diagrams/setup-k8s.png)

## Prerequisites

A storage class for the PersistentVolumeClaims, an Immich the cluster can reach (same namespace,
another namespace, or external), and the NVIDIA GPU Operator:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace
```

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

Don't commit a plain Secret. [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets), or
whatever your cluster already uses:

```bash
kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml
kubectl apply -f base/sealed-secret.yaml
```

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

Open [http://localhost:8080](http://localhost:8080). No Ingress is shipped: enable
[authentication](../configuration/authentication.mdx) first, then copy
`base/ingress.yaml.example` into place.

## GPU requests and node selection

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

The overlay schedules on nodes labelled `nvidia.com/gpu.present=true` (the GPU Operator sets that
one) and tolerates the `nvidia.com/gpu` taint. If your cluster labels GPU nodes differently,
change the `nodeSelector`:

```yaml
nodeSelector:
  nvidia.com/gpu.present: "true"
```

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
| Cache PVC | 20Gi | mounted at `/home/immich/.immich-memories`: `config.yaml`, `cache.db` (analysis scores), video cache, projects, automation history |
| Output PVC | 50Gi | mounted at `/app/output`: generated videos |
| Models PVC | 5Gi | mounted at `/models` as `immich-memories-models`: the pinned DINOv2 export, the pinned sensitive-content export and the detector Hugging Face cache, all written by the `fetch-models` init container every pod runs. Every pod binds it: skip it and nothing starts |

There is no ConfigMap: connection details come from the Secret, everything else from
`IMMICH_MEMORIES_*` env vars or the UI settings page (which writes `config.yaml` on the PVC).

`cache/annotations.sqlite` on the cache PVC holds every caption, head answer, detector verdict and
reading the editor has banked. That is the valuable data: losing it means re-reading your whole
library. Back up the PVC. Do not use `immich-memories cache backup` for this: it copies
`cache.db`, which holds run history and the retired scorer's table, not the banks.

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
always with HTTP `200` (`status: ok`), so it is useless as a probe: the manifests use
`/health/live` for liveness and `/health/ready` for readiness. Point Uptime Kuma, a Prometheus
blackbox exporter or whatever you run at `/health/ready` on port 8080.

## What the card does here

NVENC encoding and the GPU title kernels, same as [Linux + NVIDIA](./linux-nvidia.md), and nothing
else in this pod. The Kubernetes layer adds scheduling and PVC-backed storage, not scaling: the UI
is single-replica.

So do not size the cluster around the encoder. Once NVENC has the encode, what you wait for is
preparation and the editor's readings: a caption, six heads and two detectors per candidate
picture, then the text model over the period. None of that runs on this card unless you put the
picture facts behind the [inference service](../installation/inference-service.md), which is how
the measured cluster ran its classifiers on a GPU. What each host spent on a real month is on
[Running modes](../running-modes.md). Immich API throughput and reader latency are the numbers to
watch.

## Further reading

- [Terraform deployment](../installation/terraform.md) for infrastructure-as-code provisioning
- [Kubernetes manifests](../installation/kubernetes.md) for detailed manifest reference
