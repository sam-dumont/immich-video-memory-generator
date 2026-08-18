# Kubernetes Deployment

Kustomize manifests for Immich Memories. The base runs on any cluster (CPU only);
NVIDIA GPU scheduling is an overlay.

Authentication is disabled by default. Do not expose the Service until auth is enabled. The UI is
single-user, single-replica because active workflow state is in-process; keep `replicas: 1`.

These manifests get less exercise than Docker Compose. They are validated with
`kubectl kustomize` in the test suite, not applied to a live cluster on every release — read the
rendered output before you apply it.

```
base/                  CPU-only: Namespace, Secret, PVCs, Deployment, Service, NetworkPolicy
  job.yaml             optional CLI Job + CronJobs (commented out in kustomization.yaml)
  ingress.yaml.example optional Ingress — only after enabling authentication
overlays/gpu/          adds runtimeClassName nvidia, nvidia.com/gpu, node selector, tolerations
```

## Prerequisites

1. A storage class for two `ReadWriteOnce` PVCs (cache/state 20Gi, output 50Gi)
2. Immich reachable from the cluster (in-cluster or external, port 2283 by default)
3. GPU overlay only: the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator)
   (RuntimeClass `nvidia`, `nvidia.com/gpu` resources, `nvidia.com/gpu.present` node label)

## Quick Start

```bash
cd deploy/kubernetes

# 1. Secret: Immich URL + API key (every key becomes an env var in the pod)
cp base/secret.yaml.example base/secret.yaml
vim base/secret.yaml

# 2. Deploy — CPU only
kubectl apply -k base
#    or on NVIDIA nodes
kubectl apply -k overlays/gpu

# 3. Open the UI
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
# http://localhost:8080
```

`base/kustomization.yaml` pins the image tag (`images: newTag`). Published tags carry no `v`
prefix (`0.41.0`, `latest`); bump it when you upgrade.

## How the pod is wired

The image runs as user `immich`, UID/GID 1000, `HOME=/home/immich`.

| Mount | Backed by | Holds |
|-------|-----------|-------|
| `/home/immich/.immich-memories` | PVC `immich-memories-cache` (writable) | `config.yaml`, `cache.db` (analysis scores), video cache, projects, automation history |
| `/app/output` | PVC `immich-memories-output` | generated videos (`IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`) |
| `/tmp` | emptyDir 4Gi | FFmpeg intermediates (use 8Gi for 4K) |

There is no ConfigMap. `IMMICH_URL` / `IMMICH_API_KEY` come from the Secret; anything else is an
`IMMICH_MEMORIES_<SECTION>__<KEY>` env var on the Deployment (commented examples for LLM analysis
and daily automation are in `base/deployment.yaml`), and the UI settings page writes
`config.yaml` on the PVC.

Probes: liveness `/health/live` (process up), readiness `/health/ready` (`200` only when config
is present and Immich answers, otherwise `503`). `/health` always returns `200` and is not used.

## Batch Jobs

`base/job.yaml` holds a one-off `generate` Job and two CronJobs (monthly highlights, `auto run`).
Uncomment `- job.yaml` in the kustomization or apply it directly:

```bash
kubectl apply -f base/job.yaml
kubectl logs -n immich-memories -f job/immich-memories-generate
kubectl exec -n immich-memories deployment/immich-memories -- ls -la /app/output/
```

`--duration` is seconds (`600` = 10 minutes). The jobs mount the same two PVCs as the
Deployment; with `ReadWriteOnce` storage the job pod has to land on the same node, so use
`ReadWriteMany` storage or scale the Deployment to 0 first. If you only want scheduled memories,
`IMMICH_MEMORIES_AUTOMATION__ENABLED=true` on the Deployment does that in-process without a job.

## GPU

`overlays/gpu/deployment-gpu.yaml` is a strategic-merge patch on the Deployment: `runtimeClassName:
nvidia`, one `nvidia.com/gpu`, `NVIDIA_*` env, `nodeSelector` on `nvidia.com/gpu.present=true`
and a toleration for the `nvidia.com/gpu` taint. Edit the label or GPU count there. The app
auto-detects the GPU (NVENC encoding, CUDA analysis, GPU title rendering).

## Ingress

Not shipped by default because auth is off. Enable auth first (basic auth keys in the Secret, or
OIDC), then `cp base/ingress.yaml.example base/ingress.yaml`, set the host, and add
`- ingress.yaml` to `base/kustomization.yaml`.

## Sealed Secrets

```bash
brew install kubeseal
cp base/secret.yaml.example base/secret.yaml   # fill in, then seal
kubeseal --format=yaml < base/secret.yaml > base/sealed-secret.yaml
kubectl apply -f base/sealed-secret.yaml
```

## Backups

`cache.db` is the expensive part (losing it means re-analyzing the library):

```bash
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache backup /app/output/cache-backup.db
```
