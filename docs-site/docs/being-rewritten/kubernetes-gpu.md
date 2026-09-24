---
sidebar_label: "Kubernetes + GPU"
unlisted: true
---

:::note[Being rewritten]

This page is being split into the new docs. Its text moves to [Inference service](../better/inference.md).

:::

# Kubernetes + GPU

A cluster with NVIDIA GPU nodes, running this as a workload: a Deployment for the UI, a Job for
batch generation, NVENC and the GPU title kernels on the card. If you are not already running K8s,
start with [Docker](../run/docker.md) instead.

![Kubernetes setup diagram](/img/diagrams/setup-k8s.png)

## Prerequisites

A storage class for the PersistentVolumeClaims, an Immich the cluster can reach, and the NVIDIA
GPU Operator:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace
```

## Deploy with Kustomize

The manifests live in `deploy/kubernetes/` in the repo: a CPU-only `base/` and an `overlays/gpu/`
patch that adds the NVIDIA bits. Field-by-field reference:
[Kubernetes deployment](../run/kubernetes.md).

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
image tag (no `v` prefix: release `vX.Y.Z` is tag `X.Y.Z`); the checked-in pin is only as current
as the last bump, so check the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) first.
Everything lands in the `immich-memories` namespace; to use another,
[set it in each kustomization root](../run/kubernetes.md#another-namespace) before you
apply. The pods pin `tier: no_captions` in their env, so a first cut needs no caption server.

Don't commit a plain Secret. Use
[sealed-secrets](https://github.com/bitnami-labs/sealed-secrets), or whatever your cluster has:

```bash
kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml
kubectl apply -f base/sealed-secret.yaml
```

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

No Ingress is shipped, because authentication is disabled by default and the app holds an API key
to your whole library: turn on [authentication](../run/authentication.mdx) first, then
copy `base/ingress.yaml.example` into place. The UI is single-user, single-replica; workflow state
lives in the process, so do not scale the Deployment.

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

The base Deployment keeps `2Gi/1000m` requests and `8Gi/4000m` limits. The overlay schedules on
nodes labelled `nvidia.com/gpu.present=true` (set by the GPU Operator) and tolerates the
`nvidia.com/gpu` taint; change the `nodeSelector` if your cluster labels them differently.

## Batch jobs

Run one-off generation without the UI:

```bash
kubectl apply -f base/job.yaml
kubectl logs -n immich-memories -f job/immich-memories-generate
```

`--duration` is in **seconds** (the example job uses `600`). The jobs are CPU-only as shipped; copy
the fields from `overlays/gpu/deployment-gpu.yaml` into the pod spec for GPU nodes. They share the
Deployment's `ReadWriteOnce` PVCs, so the job pod has to land on the same node. The alternative is
to skip the CronJobs and set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment.

## Storage

| Volume | Size | Mounted at |
|-----|------|---------|
| Cache PVC | 20Gi | `/home/immich/.immich-memories`: `config.yaml`, `cache.db`, video cache, projects, automation history |
| Output PVC | 50Gi | `/app/output`: generated videos |
| Models PVC | 5Gi | `/models`: the two pinned ONNX exports and the detector Hugging Face cache, written by the `fetch-models` init container. Every pod binds it: skip it and nothing starts |

Connection details come from the Secret, everything else from `IMMICH_MEMORIES_*` env vars or the
UI settings page, which writes `config.yaml` onto the cache PVC.

Back up the cache PVC. `cache/annotations.sqlite` on it holds every caption, head answer, detector
verdict and reading the editor has banked, and losing it means re-reading your whole library.
`immich-memories cache backup` does not cover it: that copies `cache.db`, not the banks.

## Health monitoring

`/health/ready` returns `200` when config is present and Immich is reachable and `503` otherwise.
The manifests use `/health/live` for liveness and `/health/ready` for readiness; point Uptime Kuma
or a Prometheus blackbox exporter at `/health/ready` on port 8080. Do not probe `/health`: it
returns the same JSON but always with HTTP `200`.

## What the card does here

NVENC encoding and the GPU title kernels, same as [Linux + NVIDIA](./linux-nvidia.md), and nothing
else in this pod. The title kernels try CUDA first. If the card cannot start them, the pod does not
fail: one warning line names the backend and the reason (`Title rendering on CPU: CUDA: ...`) and the
same kernels draw the titles on the processor, slower. `preflight` shows the same thing in its
`Title rendering` row, so check it once after the first deploy.

The kernels keep their compile cache in `~/.immich-memories/cache/kernels`, which is the `data`
volume here. That matters on this root filesystem: it is read-only, and the kernel library used to
write its cache under `~/.cache` and abort (`sigabrt` in preflight) when it could not, which left
every pod on PIL titles whatever card it had. So do not size the cluster around the encoder: once NVENC has the encode, what
you wait for is preparation and the editor's readings, and neither touches this card unless you put
the picture facts behind the [inference service](../better/inference.md). That is how
the measured cluster ran its classifiers on a GPU: [Running modes](./running-modes.md).
