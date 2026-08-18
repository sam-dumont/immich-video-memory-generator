---
sidebar_label: "Kubernetes + GPU"
---

# Kubernetes + GPU Setup

For Kubernetes clusters with GPU nodes. This is the most advanced setup: if you're not already running K8s, start with [Docker](../installation/docker.md) instead.

## Who this is for

You run a Kubernetes cluster with NVIDIA GPU nodes (on-prem, cloud, or hybrid). You want Immich Memories as a scheduled workload with GPU-accelerated encoding and optional music generation pods.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ Kubernetes Cluster                                  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ namespace: immich-memories                    │   │
│  │                                               │   │
│  │  ┌────────────┐  ┌────────────┐              │   │
│  │  │ Deployment  │  │ Job        │              │   │
│  │  │ (UI/API)    │  │ (batch     │              │   │
│  │  │ port 8080   │  │  generate) │              │   │
│  │  │ GPU: 1      │  │ GPU: 1     │              │   │
│  │  └────────────┘  └────────────┘              │   │
│  │                                               │   │
│  │  ConfigMap: config.yaml                       │   │
│  │  PVCs: cache (20Gi), output (50Gi)            │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌─────────────────┐                                │
│  │ GPU Operator     │  (manages nvidia.com/gpu)     │
│  └─────────────────┘                                │
└─────────────────────────────────────────────────────┘
         │
    ┌────┴─────────┐
    │ Immich server │ (same cluster or external)
    └──────────────┘
```

![Kubernetes setup diagram](/img/diagrams/setup-k8s.png)

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

The manifests live in `deploy/kubernetes/` in the repo:

:::warning Read the known gaps first
The shipped manifests need hand edits before they boot (Secret not in the kustomization,
UID/home and read-only config mount, stale LLM keys, `/health` probes, job durations in seconds,
an Ingress that is on by default). The list is in [Kubernetes deployment](../installation/kubernetes.md).
:::

```bash
cd deploy/kubernetes

# Create the secret
cp secret.yaml.example secret.yaml
# Edit with your Immich URL and API key
vim secret.yaml

# The secret is not part of kustomization.yaml — apply it first
kubectl apply -f namespace.yaml
kubectl apply -f secret.yaml

# Deploy the rest
kubectl apply -k .
```

Or apply individually:

```bash
kubectl apply -f namespace.yaml
kubectl apply -f secret.yaml
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

Open [http://localhost:8080](http://localhost:8080). `service.yaml` already contains an Ingress
(`memories.example.com`, nginx class) — edit the host or remove it, and do not expose the Service
until [authentication](../configuration/authentication.mdx) is enabled.

## GPU resource requests

The deployment requests 1 NVIDIA GPU. Adjust in the deployment manifest:

```yaml
resources:
  requests:
    nvidia.com/gpu: "1"
    memory: "2Gi"
    cpu: "1000m"
  limits:
    nvidia.com/gpu: "1"
    memory: "8Gi"
    cpu: "4000m"
```

## Node selection

Pods schedule on nodes with `nvidia.com/gpu.present=true` (set by the GPU Operator). If your cluster uses different labels:

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
kubectl apply -f job.yaml
kubectl logs -n immich-memories -f job/immich-memories-generate
```

`--duration` is in **seconds**. The example job passes `--duration 10` — change it to something
like `600` before applying, or you get a ten-second video.

## Storage

Default PVC sizes:

| Volume | Size | Purpose |
|-----|------|---------|
| ConfigMap `immich-memories-config` | – | `config.yaml` |
| Cache PVC | 20Gi | `cache.db` (analysis scores) + downloaded video cache — once mounted at `~/.immich-memories` (see the gaps list) |
| Output PVC | 50Gi | Generated videos (`/output`) |

`cache.db` holds the analysis scores from all previous runs. This is the most valuable data: losing it means re-analyzing your entire library. Back it up:

```bash
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache backup /output/cache-backup.db
```

## Secrets management

Don't commit plain secrets to git. Use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets) or your cluster's secret management:

```bash
kubeseal --format=yaml < secret.yaml > sealed-secret.yaml
kubectl apply -f sealed-secret.yaml
```

## Health monitoring

`/health/ready` returns `200` when config is present and Immich is reachable, `503` otherwise, with a JSON body like:

```json
{
  "status": "ready",
  "configuration": "configured",
  "immich_reachable": true,
  "last_successful_run": "2025-12-15T10:30:00",
  "version": "0.40.1"
}
```

`/health/live` only says the process is up. `/health` returns the same JSON as `/health/ready` but always with HTTP `200` (`status: ok`), so it is useless as a probe — the shipped manifest still points both probes at it; switch them to `/health/live` (liveness) and `/health/ready` (readiness).

Point your monitoring (Uptime Kuma, Prometheus blackbox exporter, etc.) at `/health/ready` on port 8080.

## What works / what doesn't

Same as the [Linux + NVIDIA](./linux-nvidia.md) setup: NVENC encoding, CUDA scene analysis, Taichi GPU titles (face detection is CPU on Linux). The Kubernetes layer adds scheduling and PVC-based storage — not scaling: the UI is single-replica.

## Performance

Same as bare-metal Linux + NVIDIA. Kubernetes overhead is negligible for this workload. The bottleneck is GPU encoding speed and Immich API download throughput, not container orchestration.

## Further reading

- [Terraform deployment](../installation/terraform.md) for infrastructure-as-code provisioning
- [Kubernetes manifests](../installation/kubernetes.md) for detailed manifest reference
