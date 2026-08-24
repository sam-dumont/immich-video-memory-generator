---
sidebar_position: 3
title: Kubernetes
---

# Kubernetes Deployment

Kustomize manifests live in `deploy/kubernetes/`. The base boots on any cluster (CPU only); NVIDIA
GPU scheduling is an overlay.

:::note Less travelled than Docker Compose
Docker Compose is the primary self-hosting path. What the test suite pins on every run is the
manifests' contract: Secret applied, writable state volume, `/health/live` + `/health/ready`
probes, no GPU requirement in the base. A separate test renders base and GPU overlay with
`kubectl kustomize`, but it skips wherever `kubectl` is not installed — which includes CI — and
nothing is applied to a live cluster. Read the rendered output before you apply it, and open an
issue if something does not boot.
:::

```
deploy/kubernetes/
├── base/                    Namespace, Secret, PVCs, Deployment, Service, NetworkPolicy
│   ├── job.yaml             optional CLI Job + CronJobs (commented out in kustomization.yaml)
│   └── ingress.yaml.example optional Ingress — only after enabling authentication
└── overlays/gpu/            + runtimeClassName nvidia, nvidia.com/gpu, node selector, tolerations
```

## Prerequisites

1. A storage class for two `ReadWriteOnce` PVCs (cache/state 20Gi, output 50Gi)
2. Immich reachable from the cluster — in-cluster (`http://immich-server.<ns>.svc.cluster.local:2283`)
   or external
3. GPU overlay only: the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator), which
   provides the `nvidia` RuntimeClass, `nvidia.com/gpu` resources and the
   `nvidia.com/gpu.present` node label

## Quick Start

```bash
cd deploy/kubernetes

# Secret: Immich URL + API key. Every key becomes an env var in the pod.
cp base/secret.yaml.example base/secret.yaml
vim base/secret.yaml

# CPU only
kubectl apply -k base
# ...or on NVIDIA nodes
kubectl apply -k overlays/gpu
```

`kubectl kustomize base` shows what will be applied. `base/kustomization.yaml` pins the image tag
(`images: newTag`). Published tags carry no `v` prefix — release `vX.Y.Z` is image tag `X.Y.Z` —
plus `latest`. The checked-in pin trails the current release, so check it against the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) before you
apply, and bump it when you upgrade.

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
# Open http://localhost:8080
```

:::caution One private replica
Authentication is disabled by default. Do not add an Ingress or otherwise expose the Service until
authentication is enabled. The UI is single-user, single-replica because active workflow state is
kept in-process; leave `replicas: 1` even when using shared storage.
:::

Once auth is on (basic-auth keys in the Secret, or [OIDC](../configuration/authentication.mdx)):
`cp base/ingress.yaml.example base/ingress.yaml`, set the host, and add `- ingress.yaml` to
`base/kustomization.yaml`.

## How the pod is wired

The image runs as user `immich`, UID/GID 1000, `HOME=/home/immich` — the manifests set
`runAsUser`/`fsGroup` 1000, drop all capabilities, use the `RuntimeDefault` seccomp profile and
mount the root filesystem read-only. The three mounts below are the only writable paths.

| Mount | Backed by | Holds |
|-------|-----------|-------|
| `/home/immich/.immich-memories` | PVC `immich-memories-cache` (writable) | `config.yaml`, `cache.db` (analysis scores), video cache, projects, automation history |
| `/app/output` | PVC `immich-memories-output` | generated videos (`IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`) |
| `/tmp` | emptyDir 4Gi | FFmpeg intermediates — 8Gi for 4K |

There is no ConfigMap. `IMMICH_URL` / `IMMICH_API_KEY` come from the Secret (`envFrom`), so any
secret setting — `IMMICH_MEMORIES_LLM__API_KEY`, `IMMICH_MEMORIES_STORAGE_SECRET`,
`IMMICH_MEMORIES_AUTH_PASSWORD` — can live there too. Everything else is an
`IMMICH_MEMORIES_<SECTION>__<KEY>` env var on the Deployment; `base/deployment.yaml` carries
commented examples for LLM clip analysis and the in-pod daily automation. Settings saved from the
UI go to `config.yaml` on the PVC; env vars override them.

The NetworkPolicy allows egress to DNS, 80/443, Immich on 2283 and an optional local LLM on 11434.
Edit it if your Immich listens elsewhere.

## GPU

`overlays/gpu/deployment-gpu.yaml` is a strategic-merge patch on the Deployment: `runtimeClassName:
nvidia`, one `nvidia.com/gpu` request/limit, `NVIDIA_VISIBLE_DEVICES` / `NVIDIA_DRIVER_CAPABILITIES`,
a `nodeSelector` on `nvidia.com/gpu.present=true` and a toleration for the `nvidia.com/gpu` taint.
Change the label or GPU count there. The app auto-detects the GPU (NVENC encoding, CUDA analysis,
GPU title rendering); no config change is needed.

## Batch Jobs

`base/job.yaml` holds a one-off `generate` Job (10-minute person spotlight) and two CronJobs
(monthly highlights on the 1st, `auto run` daily). Uncomment `- job.yaml` in the kustomization or
apply it directly:

```bash
kubectl apply -f base/job.yaml
kubectl logs -n immich-memories -f job/immich-memories-generate
kubectl exec -n immich-memories deployment/immich-memories -- ls -la /app/output/
```

`--duration` is seconds. The jobs mount the same two PVCs as the Deployment; with
`ReadWriteOnce` storage the job pod has to land on the node that holds them, so use
`ReadWriteMany` storage or scale the Deployment to 0 first. If you only want scheduled memories,
`IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment does that in-process — no job needed.
CPU by default; copy the fields from the GPU patch into the pod spec to run them on GPU nodes.

## Storage and backups

Adjust the PVC sizes in `base/pvc.yaml`. `cache.db` is the expensive part: losing it means
re-analyzing your entire library.

```bash
# Backup cache from the running pod
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache backup /app/output/cache-backup.db

# Or export as portable JSON
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache export /app/output/scores.json
```

## Sealed Secrets

For production, don't commit plain secrets. Use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets):

```bash
brew install kubeseal
cp base/secret.yaml.example base/secret.yaml   # fill in your values, then seal
kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml
kubectl apply -f base/sealed-secret.yaml
```

## Monitoring

Three endpoints on port 8080:

- `/health/live` — process is up; always `200`. The liveness probe.
- `/health/ready` — `200` only when config is present and Immich is reachable, else `503`. The
  readiness probe (every 15s), which also keeps the pod out of the Service while Immich is down.
- `/health` — the same JSON as `/health/ready` (`status`, `immich_reachable`, `last_successful_run`,
  `version`, automation state) but always HTTP `200`. Compatibility endpoint; not used as a probe.

For monitoring tools like Uptime Kuma, use `/health/ready`.
