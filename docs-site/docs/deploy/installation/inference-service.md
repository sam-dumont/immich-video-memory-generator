---
sidebar_position: 5
title: Inference service
---

# The inference service

The encoder, the six public heads and the two detectors behind one HTTP port, in their own
container, with their own device variant. It is modelled on `immich-machine-learning` and reads
the same way: one image per backend, weights in a cache volume, weights dropped when idle.

It is **optional**. Leave it out and the app runs the same producers in process, which is what a
laptop wants. Run it when the models belong somewhere other than the box running the app: a GPU
machine, a Kubernetes node, or simply a container you can restart without restarting the app.

:::note
The port is `8092`, the same port `caption_base_url` already defaults to. The captioner moves into
this service later; today it serves `/facts` only.
:::

## The two images

| Tag | Platforms | Provider |
|---|---|---|
| `:X.Y.Z` | `linux/amd64`, `linux/arm64` | CPU |
| `:X.Y.Z-cuda` | `linux/amd64` | CUDA, falling back to CPU |

`openvino`, `armnn` and `rocm` are not shipped. Quick Sync, VAAPI and NVENC decode, scale and
encode: they do not run inference, and that stays true on every page here.

Both are published by the release, so you pull rather than build:

```bash
docker pull ghcr.io/sam-dumont/immich-video-memory-generator/inference:latest
docker pull ghcr.io/sam-dumont/immich-video-memory-generator/inference:latest-cuda
```

To build either variant from a checkout instead:

```bash
docker build -f docker/Dockerfile.inference --build-arg DEVICE=cpu \
  --build-arg APP_VERSION=0+local -t immich-memories-inference:cpu .

docker build -f docker/Dockerfile.inference --build-arg DEVICE=cuda \
  --build-arg APP_VERSION=0+local -t immich-memories-inference:cuda .
```

## Running it with compose

The service ships in `docker-compose.yml` behind a profile, so `docker compose up -d` stays one
container until you ask for it:

```bash
docker compose --profile inference up -d
curl -s localhost:8092/health
```

To put it on a GPU, change the backend it extends in `docker-compose.yml` and the image tag with
it:

```yaml
    extends:
      file: docker/hwaccel.inference.yml
      service: cuda
```

```bash
INFERENCE_TAG=latest-cuda docker compose --profile inference up -d
curl -s localhost:8092/health | grep CUDAExecutionProvider
```

`/health` names the provider a session is on, or the one it would open on if nothing is loaded yet.
If it says `CPUExecutionProvider` on a GPU host, the reservation did not reach the container or the
image is the CPU one: those are the only two causes.

## On Kubernetes

`deploy/kubernetes/overlays/inference` is the service on its own: a Deployment, a ClusterIP
Service on 8092, a 10Gi model-cache PVC and a NetworkPolicy. It does not pull in `base/`, so it
needs no Secret and no Immich, and it runs in a cluster where the app itself does not.

```bash
kubectl create namespace immich-memories                     # base/ creates it too
kubectl apply -k deploy/kubernetes/overlays/inference        # CPU
kubectl apply -k deploy/kubernetes/overlays/inference-cuda   # NVIDIA nodes
```

`inference-cuda` is the same overlay plus one patch: `runtimeClassName: nvidia`, one
`nvidia.com/gpu`, the two `NVIDIA_*` env vars, a node selector on `nvidia.com/gpu.present=true`, a
toleration for the `nvidia.com/gpu` taint, and the `-cuda` image tag. It sits in a sibling
directory because kustomize reads an overlay nested inside its own base as a cycle.

Each overlay pins its tag in an `images:` entry, the CUDA one with `-cuda` on the end. Bump both
together, and check the pin against the releases page first: it trails the current release.

Port-forward and read the provider back:

```bash
kubectl -n immich-memories port-forward svc/inference 8092:8092
curl -s localhost:8092/health
```

`CUDAExecutionProvider` on the CUDA overlay, `CPUExecutionProvider` on the other. If the CUDA one
says CPU, the card did not reach the pod or the tag is not the `-cuda` one.

Then point the app at it, in `config.yaml` or as an env var on the app Deployment:

```yaml
advanced:
  inference:
    facts_base_url: http://inference.immich-memories.svc.cluster.local:8092
```

In the same namespace `http://inference:8092` does. As an env var it is
`IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL`, two underscores: that one is the app's setting, while
the service's own settings above take one. The base NetworkPolicy already allows the app egress on
8092, so there is nothing to open.

The encoder and Marqo exports still have to reach `/cache` on the PVC: `kubectl cp` them, or run
`immich-memories models fetch` in a Job that mounts the same claim. The overlay's
`ALLOW_MODEL_DOWNLOADS=true` covers the detector snapshots only.

### Reach the service from outside the cluster

`http://inference:8092` only resolves inside the cluster. A NAS, a laptop or anything else on the
LAN needs an address of its own, which is what `overlays/inference-lan` asks for:

```bash
kubectl apply -k deploy/kubernetes/overlays/inference-lan
kubectl -n immich-memories get service inference-lan
```

It adds a second Service, `inference-lan`, type LoadBalancer, on the same pods and the same port.
The ClusterIP Service is untouched, so `facts_base_url: http://inference:8092` goes on meaning what
it meant and no in-cluster caller starts riding an external address. It composes with either device
overlay, which a patch on the one Service would not: the CUDA variant would need its own copy.

The address comes from the cluster's load-balancer controller. Without one the Service sits at
`<pending>` forever and there is nothing to point at. Read it back and use it:

```yaml
advanced:
  inference:
    facts_base_url: http://<the address>:8092
```

Nothing here checks a credential: whatever reaches port 8092 gets an answer. On a home LAN behind
a router that is the same exposure your NAS and your desktop already have to each other. Do not
give it a routable address, and take it back with
`kubectl delete -k deploy/kubernetes/overlays/inference-lan` when you are done.

The setup matrix does this on its own: the NAS cells apply the overlay, wait for the address, use
it and delete it. See [Setup matrix](../../contribute/setup-matrix.md).

## What it answers

| Endpoint | Question |
|---|---|
| `GET /ping` | are you up |
| `GET /health` | which producers are loaded, at which versions, on which provider |
| `POST /facts` | one picture in: what do the frozen classifiers say about it |

```bash
curl -s localhost:8092/facts -H 'content-type: application/json' \
  -d "{\"image\": \"$(base64 -i photo.jpg)\", \"producers\": [\"heads\"]}"
```

```json
{"producers": {"heads": {"encoder_key": "…", "facts": [
  {"head": "activity", "version": "public-v1", "label": "outdoors", "confidence": 0.71}]}}}
```

A fact on the wire is the bank row without its asset id. The client stores what it is handed,
verbatim, which is the whole point: **a fact's identity is the artifact that produced it, never the
machine that ran it.** The same picture through the `cpu` and the `cuda` image lands on one row:
no URL, hostname, device or execution-provider name enters any key. Facts are label-identical
across providers rather than byte-identical; confidences move in the last few decimal places.

## Settings

Every setting is an environment variable prefixed `IMMICH_MEMORIES_INFERENCE_`:

| Variable | Default | What it does |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8092` | where to listen. The image sets the host to `0.0.0.0` |
| `CACHE_DIR` | `/cache` | the model cache volume |
| `ENCODER` | `$CACHE_DIR/dinov2-small.onnx` | the pinned DINOv2 export, digest-verified on load |
| `MARQO_ONNX` | `$CACHE_DIR/nsfw-marqo-384.onnx` | the pinned sensitive-content ONNX export |
| `BUNDLE` | the packaged public bundle | head bundle `.npz` |
| `PROVIDER` | `auto` | `auto`, `cpu`, `cuda` or `coreml`. `auto` takes CUDA where the provider is present and CPU otherwise |
| `REQUEST_THREADS` | `4` | the thread pool in front of ONNX Runtime |
| `IDLE_UNLOAD_SECONDS` | `300` | drop idle weights; `0` holds them |
| `PRELOAD` | `false` | load every producer at boot instead of on first use |
| `DETECTOR_CACHE_DIR` | the Hugging Face cache | where the detector snapshots live |
| `ALLOW_MODEL_DOWNLOADS` | `false` | let a cold cache fetch the Docling snapshot |
| `MAX_IMAGE_BYTES` | `16777216` | refuse anything larger |

Idle unload drops the weights and **keeps the process**: the next request reloads them.
`immich-machine-learning` sends itself SIGINT after the same idle period; on a NAS a restart loop
costs more than a resident idle process, so that half is deliberately not copied.

## Why `auto` does not take CoreML

Measured on this export, ONNX Runtime's CoreML provider claims 274 of its 513 nodes and splits the
graph into 87 partitions, so a tensor crosses the accelerator boundary dozens of times per image:
6–8× slower than the CPU provider and 9× the resident memory, getting *worse* as the batch grows.
Naming `coreml` still selects it, so the measurement can be redone when the provider improves.
Provider choice re-keys nothing, so this costs no re-derivation.

## Point the app at it

```yaml
advanced:
  inference:
    facts_base_url: http://inference:8092
```

That is the whole switch (`IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` in the environment). From
then on `prepare` and `generate` send each picture's preview to `/facts` once, ask for every
producer the tier demands that is still missing, and bank the answer verbatim. `producers:` limits
what goes over the wire (`[heads]` keeps the two detectors local), `timeout_seconds` bounds one
request, and `fallback_to_local` says what happens when the service is down: the failure is named
against the endpoint either way, and with the fallback on the app's own producers take over. The
keys are in the [config reference](../../reference/config-reference.md#inference-service).

## Not yet

- The captioner is not in the image yet, so `/v1/chat/completions` is still your own caption server.
- The encoder and Marqo exports must already exist. Point `ENCODER` and `MARQO_ONNX` at the digest-pinned files from the app's `models fetch`, or place them at the cache paths above. Docling can fetch its snapshot into `/cache/huggingface` when `ALLOW_MODEL_DOWNLOADS` is on.

For an image check before a release, dispatch the Release workflow with `inference_only: true`. It builds commit-tagged CPU and CUDA images without creating a version or moving `latest`; the CUDA base account is reused at UID/GID 1000 so the cache volume has the same ownership as the CPU image.
