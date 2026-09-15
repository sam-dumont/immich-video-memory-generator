---
sidebar_position: 3
title: Kubernetes
---

# Kubernetes

Kustomize manifests live in `deploy/kubernetes/`. The base boots on any cluster, CPU only; NVIDIA
scheduling is an overlay. Docker Compose is the primary path, and CI renders these manifests
without applying them to a live cluster, so read the rendered output before you apply it.

The base starts at `no_captions`, which needs no caption server. Releases attach an
`immich-memories-deploy-X.Y.Z.tar.gz` bundle after the app and inference images finish
publishing. Its three image pins match that release. Download it from the release page,
extract it, then use the `deploy/kubernetes/` directory inside it.

```
deploy/kubernetes/
├── base/                    Namespace, Secret, PVCs, Deployment, Service, NetworkPolicy
│   ├── job.yaml             optional CLI Job + CronJobs (commented out in kustomization.yaml)
│   └── ingress.yaml.example optional Ingress, only after enabling authentication
├── overlays/gpu/            the app on an NVIDIA node
├── overlays/inference/      the inference service alone (+ -cuda, + -lan for outside callers)
└── overlays/captioner/      llama.cpp under the alias `tier: full` wants (+ -cuda)
```

## Prerequisites

1. A storage class for three `ReadWriteOnce` PVCs: `immich-memories-cache` 20Gi,
   `immich-memories-output` 50Gi, `immich-memories-models` 5Gi.
2. Immich reachable from the cluster (`http://immich-server.<ns>.svc.cluster.local:2283`, or
   external).
3. GPU overlay only: the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator), for the
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
`base/kustomization.yaml` and the two inference overlays each pin an image tag, and nothing bumps
those pins on a release, so they trail. Tags carry no `v`: release `vX.Y.Z` is image tag `X.Y.Z`.
Read the current one off the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) and set it:

```bash
cd deploy/kubernetes/base && kustomize edit set image \
  ghcr.io/sam-dumont/immich-video-memory-generator=:X.Y.Z
```

That rewrites the init container with the app container. `kubectl apply -f base/job.yaml` skips
kustomize entirely and runs `:latest`, so uncomment `- job.yaml` in the kustomization if the jobs
should follow the Deployment's tag.
:::

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

:::caution One private replica
Authentication is disabled by default, so do not add an Ingress or expose the Service until it is
on. The UI is single-user, single-replica because workflow state is kept in-process: leave
`replicas: 1` even with shared storage.
:::

Once auth is on (basic-auth keys in the Secret, or [OIDC](../configuration/authentication.mdx)),
copy `base/ingress.yaml.example` to `base/ingress.yaml`, set the host and add it to the
kustomization.

## How the pod is wired

The image runs as `immich`, UID/GID 1000, `HOME=/home/immich`. The manifests set `runAsUser` and
`fsGroup` 1000, drop all capabilities, use the `RuntimeDefault` seccomp profile and mount the root
read-only. Four writable paths:

| Mount | Backed by | Holds |
|---|---|---|
| `/home/immich/.immich-memories` | PVC `immich-memories-cache` | `config.yaml`, `cache/annotations.sqlite` (banked facts and readings), `cache.db` (run history, automation state), video cache |
| `/app/output` | PVC `immich-memories-output` | generated videos |
| `/models` | PVC `immich-memories-models` | the three artifacts `immich-memories models fetch` writes, at `IMMICH_MEMORIES_TRIAGE__ENCODER`, `..._MARQO_ONNX` and `..._DETECTOR_CACHE_DIR` |
| `/tmp` | emptyDir 4Gi | FFmpeg intermediates; 8Gi for 4K |

A deployment that predates the models claim has to add it before the next apply, or the pod stays
`Pending` waiting for a volume that does not exist.

There is no ConfigMap. `IMMICH_URL`, `IMMICH_API_KEY` and any other secret setting come from the
Secret (`envFrom`); everything else is an `IMMICH_MEMORIES_<SECTION>__<KEY>` env var on the
Deployment, which carries commented examples for the reader and the daily automation. Settings
saved from the UI go to `config.yaml` on the PVC; env vars override them.

The NetworkPolicy allows egress to DNS, 80 and 443, Immich on 2283, a reader on 11434 (Ollama's
port; oMLX serves on 8000) and the caption server on 8092. Edit the ports if yours differ.

## The models the first cut needs

Every pod in `base/` runs a `fetch-models` init container first, writing the three pinned artifacts
onto the `/models` claim, so there is nothing to run by hand. It exits without a download when all
three are there, so a restart costs nothing and a nightly CronJob never goes back to the network.
`kubectl logs -n immich-memories deploy/immich-memories -c fetch-models` shows what it did.

## Set the preparation tier

:::caution No manifest here pins a tier
Nothing here pins one, so a pod takes the code default, `full`, which wants a caption server.
Without one the first cut stops at prepare naming `caption_base_url`. Pick a tier before you apply,
on the Deployment and on the Job and CronJobs if you use them:

```yaml
            - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER
              value: "no_captions"       # full | no_captions | metadata_only
            # On full, with overlays/captioner applied:
            # - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER
            #   value: "full"
            # - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL
            #   value: "http://captioner:8092/v1"
```

What each tier runs and gives up is on [Running modes](../running-modes.md).
:::

## Check it from outside the pod

The `docker compose exec` lines elsewhere in these docs are `kubectl exec` here:

```bash
kubectl exec -n immich-memories deploy/immich-memories -- immich-memories config test
kubectl exec -n immich-memories deploy/immich-memories -- immich-memories preflight
```

Preflight follows the reader and tier the Deployment sets, so run it after the change above.

## GPU

`overlays/gpu/deployment-gpu.yaml` patches the Deployment with `runtimeClassName: nvidia`, one
`nvidia.com/gpu`, the two `NVIDIA_*` env vars, the `nvidia.com/gpu.present=true` node selector and
the matching toleration. The app uses that card for NVENC encoding and GPU title rendering and
nothing else: the editor's models are separate services, each with its own CUDA image.

## The two model services

Both apply on their own, with no Secret and no `base/`:

```bash
kubectl apply -k deploy/kubernetes/overlays/inference    # heads and detectors, -cuda for a card
kubectl apply -k deploy/kubernetes/overlays/captioner    # the caption server tier: full wants
```

Point the app at them with `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL=http://inference:8092` (two
underscores) and `caption_base_url: http://captioner:8092/v1`; the base NetworkPolicy already
allows egress on 8092. What each overlay patches, and what a card is worth per picture, are on
[the inference service](./inference-service.md) and [Caption server](./caption-server.md).

## Batch jobs

`base/job.yaml` holds a one-off `generate` Job and two CronJobs (monthly highlights on the 1st,
`auto run` daily). Uncomment `- job.yaml` in the kustomization. The jobs mount the same PVCs, so on
`ReadWriteOnce` storage the job pod has to land on the node holding them: use `ReadWriteMany` or
scale the Deployment to 0 first. For scheduled memories alone,
`IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment does it in-process.

## Backups

Back up the cache PVC: `cache/annotations.sqlite` on it is the expensive part, and losing it means
re-reading the library. `immich-memories cache backup|export` move the retired scorer's table, not
the banks. For secrets in git, use
[sealed-secrets](https://github.com/bitnami-labs/sealed-secrets):
`kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml`.

## Probes

`/health/live` (always `200` while the process is up) is the liveness probe. `/health/ready`
(`200` only with config present and Immich reachable, else `503`) is the readiness probe, every
15 s, and keeps the pod out of the Service while Immich is down. `/health` always returns `200` and
is not a probe.
