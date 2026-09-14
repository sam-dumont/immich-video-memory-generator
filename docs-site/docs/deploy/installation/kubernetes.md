---
sidebar_position: 3
title: Kubernetes
---

# Kubernetes

Kustomize manifests live in `deploy/kubernetes/`. The base requests no GPU; NVIDIA
scheduling is an overlay. Docker Compose is the primary path: the test suite pins the manifests'
contract (Secret applied, writable state volume, `/health/live` and `/health/ready` probes, no GPU
in the base) and renders both variants with `kubectl kustomize` where `kubectl` exists, but
nothing is applied to a live cluster in CI. Read the rendered output before you apply it.

```
deploy/kubernetes/
├── base/                    Namespace, Secret, PVCs, Deployment, Service, NetworkPolicy
│   ├── job.yaml             optional CLI Job + CronJobs (commented out in kustomization.yaml)
│   └── ingress.yaml.example optional Ingress, only after enabling authentication
├── overlays/gpu/            + runtimeClassName nvidia, nvidia.com/gpu, node selector, tolerations
├── overlays/inference/      the inference service alone: Deployment, Service on 8092, cache PVC
└── overlays/inference-cuda/ the same service on an NVIDIA card
```

## Prerequisites

1. A storage class for three `ReadWriteOnce` PVCs: cache and state 50Gi, output 50Gi, models 5Gi.
2. Immich reachable from the cluster, in-cluster (`http://immich-server.<ns>.svc.cluster.local:2283`)
   or external.
3. GPU overlay only: the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) for the
   `nvidia` RuntimeClass, `nvidia.com/gpu` resources and the `nvidia.com/gpu.present` label.

## Quick start

```bash
cd deploy/kubernetes
cp base/secret.yaml.example base/secret.yaml   # Immich URL + API key; every key becomes an env var
vim base/secret.yaml
kubectl apply -k base              # CPU only
kubectl apply -k overlays/gpu      # or, on NVIDIA nodes
```

`kubectl kustomize base` shows what will be applied. `base/kustomization.yaml` pins the image tag;
published tags carry no `v` (release `vX.Y.Z` is tag `X.Y.Z`, plus `latest`). Check the pin against
the [releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) before
you apply.

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

:::caution One private replica
Authentication is disabled by default. Do not add an Ingress or expose the Service until it is
enabled. The UI is single-user, single-replica because workflow state is kept in-process; leave
`replicas: 1` even with shared storage.
:::

Once auth is on (basic-auth keys in the Secret, or [OIDC](../configuration/authentication.mdx)):
`cp base/ingress.yaml.example base/ingress.yaml`, set the host, add `- ingress.yaml` to the
kustomization.

## How the pod is wired

The image runs as `immich`, UID/GID 1000, `HOME=/home/immich`; the manifests set `runAsUser` and
`fsGroup` 1000, drop all capabilities, use the `RuntimeDefault` seccomp profile and mount the root
filesystem read-only. The writable mounts are:

| Mount | Backed by | Holds |
|---|---|---|
| `/home/immich/.immich-memories` | PVC `immich-memories-cache` | `config.yaml`, `cache/annotations.sqlite` (every banked fact and reading), `cache.db` (run history, automation state), the video cache |
| `/app/output` | PVC `immich-memories-output` | generated videos |
| `/models` | PVC `immich-memories-models` | the pinned DINOv2 export (`IMMICH_MEMORIES_TRIAGE__ENCODER`) and both detectors, written by `immich-memories models fetch` |
| `/home/immich/.cache` | cache PVC, `library-cache` subPath | downloaded music, library caches and optional local music models |
| `/tmp` | cache PVC, `scratch` subPath | FFmpeg intermediates and temporary files |

The 50Gi state claim is a starting budget: the defaults allow 10 GB of thumbnails, 10 GB of
videos and 2 GB of clip previews. Annotation banks, attempts, optional music models and scratch
need additional space. Active files can temporarily exceed cache caps. A 50Gi output claim holds
finished videos separately; retain or expand it according to your output size and history.
The 5Gi model claim holds the pinned preparation artifacts, not a reader or caption server.

The storage driver must make volumes writable by `fsGroup: 1000`. The cache and scratch subPaths
share the state claim's capacity. Scratch survives restarts, so inspect and remove abandoned files
while all app and batch pods are stopped. Never clear the whole state claim to reclaim scratch.

There is no ConfigMap. `IMMICH_URL` and `IMMICH_API_KEY` come from the Secret (`envFrom`), so any
secret setting (`IMMICH_MEMORIES_LLM__API_KEY`, `IMMICH_MEMORIES_STORAGE_SECRET`,
`IMMICH_MEMORIES_AUTH_PASSWORD`) can live there too. Everything else is an
`IMMICH_MEMORIES_<SECTION>__<KEY>` env var on the Deployment, which carries commented examples
for the reader and the in-pod daily automation. Settings saved from the UI go to `config.yaml` on
the PVC; env vars override them.

The NetworkPolicy allows egress to DNS, 80 and 443, Immich on 2283, a reader on 11434 (Ollama's
port; oMLX serves on 8000) and the caption server on 8092. Edit the ports if yours differ. Run
`immich-memories models fetch` once (a one-off Job, or `kubectl exec` into the pod) before the
first cut. The encoder and both detectors live on `/models`; `HF_HOME` also keeps Hugging Face's
auxiliary caches there. Set the preparation tier and caption endpoint before cutting: the code
default is `full`, which requires a caption server. Use `no_captions` for the app alone.

```bash
kubectl exec -n immich-memories deployment/immich-memories -- immich-memories models fetch
```

## GPU

`overlays/gpu/deployment-gpu.yaml` patches the Deployment with `runtimeClassName: nvidia`, one
`nvidia.com/gpu`, `NVIDIA_VISIBLE_DEVICES` and `NVIDIA_DRIVER_CAPABILITIES`, a node selector on
`nvidia.com/gpu.present=true` and a toleration for the `nvidia.com/gpu` taint. The app uses the
card for NVENC encoding and GPU title rendering. The shipped app image uses CPU ONNX Runtime;
the [inference service](./inference-service.md) has its own CUDA image.

## Inference service

`overlays/inference` deploys the [inference service](./inference-service.md) on its own: the
encoder, the six heads and the two detectors behind one port, a ClusterIP Service named
`inference` on 8092, a 10Gi cache PVC and its own NetworkPolicy. Its `/cache` and `/tmp` (the `scratch` subPath) share
that claim. It stays separate from the app's claims so `ReadWriteOnce` does not tie both services
to one node. It does not include `base/`, so
it builds with no Secret at all. If deploying only inference, create the `immich-memories`
namespace first. The two overlays are applied separately:

```bash
kubectl apply -k deploy/kubernetes/overlays/inference        # CPU
kubectl apply -k deploy/kubernetes/overlays/inference-cuda   # NVIDIA nodes
```

`inference-cuda` adds `runtimeClassName: nvidia`, one `nvidia.com/gpu`, the `NVIDIA_*` env, the
node selector and the toleration, and pins the `-cuda` image tag.

Point the app at it with `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL`, two underscores, set to
`http://inference:8092` in the same namespace or
`http://inference.immich-memories.svc.cluster.local:8092` from another. The base NetworkPolicy
already allows egress on 8092. `curl /health` through a port-forward names the execution provider
the service opened.

## Batch jobs

`base/job.yaml` holds a one-off `generate` Job and two CronJobs (monthly highlights on the 1st,
`auto run` daily). Uncomment `- job.yaml` in the kustomization or apply it directly. The jobs mount
the same PVCs; with `ReadWriteOnce` storage the job pod must land on the node that holds them, so
use `ReadWriteMany` or scale the Deployment to 0 first. For scheduled memories alone,
`IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment does it in-process, no Job needed.

## Backups

`cache/annotations.sqlite` on the cache PVC is the expensive part: losing it means re-reading the
library. Stop app and batch writers before copying or snapshotting the state PVC, and retain
the output PVC too. `immich-memories cache backup` backs up `cache.db` only; it does not include
`cache/annotations.sqlite`. See [upgrading](../maintenance/upgrading.md) for a complete backup.

For secrets in git, use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets):
`kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml`.

## Probes

`/health/live` (always `200` while the process is up) is the liveness probe; `/health/ready`
(`200` only with config present and Immich reachable, else `503`) is the readiness probe every
15 s and keeps the pod out of the Service while Immich is down. `/health` always returns `200` and
is not used as a probe.
