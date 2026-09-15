---
sidebar_position: 3
title: Kubernetes
---

# Kubernetes

Kustomize manifests live in `deploy/kubernetes/`. The base boots on any cluster, CPU only; NVIDIA
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
├── overlays/inference-cuda/ the same service on an NVIDIA card
├── overlays/inference-lan/  a second Service, type LoadBalancer, for callers outside the cluster
├── overlays/captioner/      llama.cpp under the alias `tier: full` wants, on its own PVC
└── overlays/captioner-cuda/ the same caption server on an NVIDIA card
```

## Prerequisites

1. A storage class for three `ReadWriteOnce` PVCs: `immich-memories-cache` 20Gi,
   `immich-memories-output` 50Gi, `immich-memories-models` 5Gi. A deployment made before the
   models claim existed has to add it.
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

`kubectl kustomize base` shows what will be applied.

:::caution Set the tag before you apply
`base/kustomization.yaml` and the two inference overlays each pin an image tag, and the checked-in
pins trail the current release by a long way: nothing bumps them on a release. Published tags carry
no `v` (release `vX.Y.Z` is image tag `X.Y.Z`, plus `latest`). Read the current one off the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) and set it:

```bash
cd deploy/kubernetes/base && kustomize edit set image \
  ghcr.io/sam-dumont/immich-video-memory-generator=:X.Y.Z
```

or edit `images: newTag` by hand. The entry rewrites the init container as well as the app
container, so both move together. `kubectl apply -f base/job.yaml` does not go through kustomize
at all and runs whatever the file names, which is `:latest`: uncomment `- job.yaml` in the
kustomization instead if you want the jobs on the same tag as the Deployment.
:::

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
filesystem read-only. Four writable paths:

| Mount | Backed by | Holds |
|---|---|---|
| `/home/immich/.immich-memories` | PVC `immich-memories-cache` | `config.yaml`, `cache/annotations.sqlite` (every banked fact and reading), `cache.db` (run history, automation state), the video cache |
| `/app/output` | PVC `immich-memories-output` | generated videos |
| `/models` | PVC `immich-memories-models` | the pinned DINOv2 export (`IMMICH_MEMORIES_TRIAGE__ENCODER`), the pinned sensitive-content export (`..._MARQO_ONNX`) and the detector cache (`..._DETECTOR_CACHE_DIR`), all written by `immich-memories models fetch` |
| `/tmp` | emptyDir 4Gi | FFmpeg intermediates; 8Gi for 4K |

Three claims, then: `immich-memories-cache`, `immich-memories-output` and `immich-memories-models`.
A deployment that predates the models claim has to add it before the next apply, or the pod stays
in `Pending` waiting for a volume that does not exist.

There is no ConfigMap. `IMMICH_URL` and `IMMICH_API_KEY` come from the Secret (`envFrom`), so any
secret setting (`IMMICH_MEMORIES_LLM__API_KEY`, `IMMICH_MEMORIES_STORAGE_SECRET`,
`IMMICH_MEMORIES_AUTH_PASSWORD`) can live there too. Everything else is an
`IMMICH_MEMORIES_<SECTION>__<KEY>` env var on the Deployment, which carries commented examples
for the reader and the in-pod daily automation. Settings saved from the UI go to `config.yaml` on
the PVC; env vars override them.

The NetworkPolicy allows egress to DNS, 80 and 443, Immich on 2283, a reader on 11434 (Ollama's
port; oMLX serves on 8000) and the caption server on 8092. Edit the ports if yours differ.

## The models the first cut needs

Every pod in `base/` runs a `fetch-models` init container first: the same image, the same
`immich-memories models fetch` a Docker user runs after `up`, writing the three pinned artifacts
onto the `/models` claim. There is nothing to run by hand. A fresh claim without it gave a pod
that came up fine and a first cut that stopped at prepare with `public heads need the pinned
DINOv2 ONNX export at /models/triage/dinov2-small.onnx`, `nsfw_marqo has no model` and
`doc_docling ... is not in /models/huggingface`.

The init step tests for all three files and exits without a download when they are there, so a
restart costs nothing and a nightly CronJob never goes back to the network. The root filesystem is
read-only, which is why the models live on the claim rather than in the image. `kubectl logs -n
immich-memories deploy/immich-memories -c fetch-models` shows what it did.

## Set the preparation tier

:::caution No manifest here pins a tier
`docker-compose.yml` pins `no_captions` so a first `up` finishes with one container. Nothing under
`deploy/kubernetes/` does, so a pod takes the code default, which is `full`, and `full` wants a
caption server. Without one the first cut stops at prepare: the description producer stays
outstanding and the failure names `caption_base_url`.

Pick one before you apply, on the Deployment (and on the Job and CronJobs if you use them):

```yaml
            - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER
              value: "no_captions"       # full | no_captions | metadata_only
            # On full, with overlays/captioner applied:
            # - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL
            #   value: "http://captioner:8092/v1"
            # Concurrency defaults to 1, which is what a CPU captioner wants.
            # On overlays/captioner-cuda, raise it:
            # - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_CONCURRENCY
            #   value: "4"
```

What each tier runs and gives up is on [Running modes](../running-modes.md).
:::

## Check it from outside the pod

The `docker compose exec` lines elsewhere in these docs are `kubectl exec` here:

```bash
kubectl exec -n immich-memories deploy/immich-memories -- immich-memories config test
kubectl exec -n immich-memories deploy/immich-memories -- immich-memories preflight
```

Preflight follows the reader and tier the Deployment sets, so run it after the change above and
not before.

## GPU

`overlays/gpu/deployment-gpu.yaml` patches the Deployment with `runtimeClassName: nvidia`, one
`nvidia.com/gpu`, `NVIDIA_VISIBLE_DEVICES` and `NVIDIA_DRIVER_CAPABILITIES`, a node selector on
`nvidia.com/gpu.present=true` and a toleration for the `nvidia.com/gpu` taint. The app uses the
card for NVENC encoding and GPU title rendering, nothing else: the editor's models are separate
services, and the [inference service](./inference-service.md) has its own CUDA image.

## Inference service

`overlays/inference` deploys the [inference service](./inference-service.md) on its own: a
Deployment, a ClusterIP Service named `inference` on 8092, a 10Gi cache PVC and its own
NetworkPolicy. It does not include `base/`, so it builds with no Secret at all.

```bash
kubectl apply -k deploy/kubernetes/overlays/inference        # CPU
kubectl apply -k deploy/kubernetes/overlays/inference-cuda   # NVIDIA nodes
```

Point the app at it with `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL`, two underscores, set to
`http://inference:8092` in the same namespace or
`http://inference.immich-memories.svc.cluster.local:8092` from another. The base NetworkPolicy
already allows egress on 8092.

`overlays/inference-lan` adds a second Service of type LoadBalancer on the same pods, for callers
that are not in the cluster; nothing behind that port checks a credential. What each overlay
patches, how a cold cache volume fills and how to read the execution provider back are on
[the inference service](./inference-service.md).

## Caption server

`tier: full` wants an endpoint advertising `smolvlm2-500m-base-public`, and `overlays/captioner`
is one: llama.cpp serving the pinned SmolVLM2-500M GGUF, a ClusterIP Service on 8092, a
NetworkPolicy and a 2Gi PVC that an init container fills and digest-checks before the server
starts.

```bash
kubectl apply -k deploy/kubernetes/overlays/captioner        # CPU
kubectl apply -k deploy/kubernetes/overlays/captioner-cuda   # NVIDIA nodes
```

`captioner-cuda` is the same Deployment with the `server-cuda` image and `--n-gpu-layers 99`
appended, plus the `nvidia` RuntimeClass, the node selector and the toleration. Neither overlay
includes `base/`, so both apply with no Immich secret.

Point the app at `http://captioner:8092/v1`. `caption_concurrency` defaults to 1, which is what a
CPU captioner wants; raise it to 4 on a card. The measured cost per picture, and why the CUDA
overlay deliberately requests no `nvidia.com/gpu`, are on [Caption server](./caption-server.md).

## Batch jobs

`base/job.yaml` holds a one-off `generate` Job and two CronJobs (monthly highlights on the 1st,
`auto run` daily). Uncomment `- job.yaml` in the kustomization or apply it directly. The jobs mount
the same PVCs; with `ReadWriteOnce` storage the job pod must land on the node that holds them, so
use `ReadWriteMany` or scale the Deployment to 0 first. For scheduled memories alone,
`IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment does it in-process, no Job needed.

## Backups

`cache/annotations.sqlite` on the cache PVC is the expensive part: losing it means re-reading the
library. Back up the PVC. `immich-memories cache backup|export` move the retired scorer's table,
not the banks; do not rely on them.

For secrets in git, use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets):
`kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml`.

## Probes

`/health/live` (always `200` while the process is up) is the liveness probe; `/health/ready`
(`200` only with config present and Immich reachable, else `503`) is the readiness probe every
15 s and keeps the pod out of the Service while Immich is down. `/health` always returns `200` and
is not used as a probe.
