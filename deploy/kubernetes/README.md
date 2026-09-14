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
overlays/inference/    the inference service alone: Deployment, Service on 8092, cache PVC, policy
overlays/inference-cuda/ the same service on an NVIDIA card (patch + `-cuda` image tag)
overlays/inference-lan/  a second Service, type LoadBalancer, for callers outside the cluster
overlays/captioner/    the caption server `tier: full` needs: llama.cpp, SmolVLM2, weights PVC
overlays/captioner-cuda/ the same server with its layers on an NVIDIA card
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
prefix — release `vX.Y.Z` is image tag `X.Y.Z` — plus `latest`. The checked-in pin trails the
current release, so check it against the
[releases page](https://github.com/sam-dumont/immich-video-memory-generator/releases) before you
apply, and bump it when you upgrade.

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

`NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` in that patch is required, not decoration.
Asking for `nvidia.com/gpu` gets you `compute,utility`, which is enough for CUDA and not for the
encode chip: without `video` the NVENC probe dies with `-22 (Invalid argument)` and the render
falls back to the CPU. Pods that skip the overlay hit this, so copy the `runtimeClassName` and the
two `NVIDIA_*` env vars into any `base/job.yaml` pod you schedule on a GPU node.

## Inference service

`overlays/inference` is the encoder, the six heads and the two detectors behind one HTTP port, as
a separate Deployment: a ClusterIP Service named `inference` on 8092, a 10Gi model-cache PVC and
its own NetworkPolicy. It deliberately does not list `../../base` in its resources. The base
refuses to build without a hand-made `secret.yaml`, and this service holds no credential and never
talks to Immich. The namespace object still comes from the base, so on a cluster running the
service alone, `kubectl create namespace immich-memories` first.

```bash
kubectl apply -k overlays/inference        # CPU
kubectl apply -k overlays/inference-cuda   # NVIDIA nodes
```

`overlays/inference-cuda` is the same overlay plus `deployment-cuda.yaml`: `runtimeClassName:
nvidia`, one `nvidia.com/gpu`, `NVIDIA_VISIBLE_DEVICES` / `NVIDIA_DRIVER_CAPABILITIES`, the
`nvidia.com/gpu.present` node selector, the `nvidia.com/gpu` toleration, and the `-cuda` image
tag. It is a sibling directory rather than `overlays/inference/cuda` because kustomize reads an
overlay nested inside its own base as a cycle and refuses to build it.

Each overlay pins its own tag (`images: newTag`), the CUDA one with `-cuda` on the end. Bump them
together. Settings are `IMMICH_MEMORIES_INFERENCE_*` env vars with one underscore, not the app's
two; the image already sets host, port and the two cache directories.

To use it, set `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` (two underscores, app side) on the app
Deployment to `http://inference:8092`, or
`http://inference.immich-memories.svc.cluster.local:8092` from another namespace. The base
NetworkPolicy already allows egress on 8092. The encoder and Marqo ONNX exports have to be on the
cache PVC; `ALLOW_MODEL_DOWNLOADS=true` in the overlay only covers the detector snapshots.

`overlays/captioner` is the caption server `tier: full` refuses to run without: llama.cpp serving
the pinned SmolVLM2-500M GGUF under the alias the app checks for, a ClusterIP Service named
`captioner` on 8092, and a 2Gi claim an init container fills with the two digest-pinned files.
`overlays/captioner-cuda` is the same thing with `--n-gpu-layers 99` appended and the `server-cuda`
image, which is worth 3.5 s a picture against tenths of a second. It asks for no `nvidia.com/gpu`
resource on purpose, because a time-sliced card has one allocatable slot and the inference
Deployment holds it: see docs/deploy/installation/caption-server.md for when to put the request
back.

`overlays/inference-lan` is for callers that are not in the cluster: a NAS, a laptop, the setup
matrix. It adds a second Service, `inference-lan`, type LoadBalancer, on the same pods and port,
and leaves the ClusterIP one alone, so nothing in-cluster changes and either device overlay
composes with it. It needs a load-balancer controller; without one the Service stays `<pending>`.
Nothing here checks a credential, so do not give it a routable address.

```bash
kubectl apply -k overlays/inference-lan
kubectl -n immich-memories get service inference-lan
kubectl delete -k overlays/inference-lan
```

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
