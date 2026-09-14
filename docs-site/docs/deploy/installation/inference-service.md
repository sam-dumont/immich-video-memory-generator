---
sidebar_position: 5
title: Inference service
---

# The inference service

The encoder, the six public heads and the two detectors behind one HTTP port, in their own
container, with their own device variant. Weights live in a persistent cache and unload after the configured idle period.

It is **optional**. Leave it out and the app runs the same producers in process, which is what a
laptop wants. Run it when the models belong somewhere other than the box running the app: a GPU
machine, a Kubernetes node, or simply a container you can restart without restarting the app.

:::note
The port is `8092`, the same port `caption_base_url` already defaults to. It does not serve captions. If both run on the same host, give them different published ports;
`facts_base_url` takes this service's URL and `caption_base_url` takes the caption server's URL.
:::

## The two images

| Tag | Platforms | Provider |
|---|---|---|
| `:X.Y.Z` | `linux/amd64`, `linux/arm64` | CPU |
| `:X.Y.Z-cuda` | `linux/amd64` | CUDA encoder, falling back to CPU; detectors stay on CPU |

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

To put it on a GPU, uncomment the device reservation that ships on the inference service in
`docker-compose.yml`, and change the image tag with it:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities:
                - gpu
```

```bash
INFERENCE_TAG=latest-cuda docker compose --profile inference up -d
curl -s localhost:8092/health | grep CUDAExecutionProvider
```

The published file names no `extends:`, because it is downloaded on its own and compose resolves
an `extends:` when the file loads, whatever profiles are on. The CPU backend reserves nothing, so
there is nothing to write for it, and the CUDA one is the block above. From a checkout you can use
`docker/hwaccel.inference.yml` instead, which holds both as `extends:` targets.

`/health` names the provider a session is on, or the one it would open on if nothing is loaded yet.
If it says `CPUExecutionProvider` on a GPU host, check the image tag, GPU reservation, explicit
`PROVIDER` setting and driver/runtime availability. A GPU reservation alone does not establish
that ONNX Runtime opened a CUDA session.

## On Kubernetes

`deploy/kubernetes/overlays/inference` is the service on its own: a Deployment, a ClusterIP
Service on 8092, a 10Gi model-cache PVC and a NetworkPolicy. It does not pull in `base/`, so it
needs no Secret and no Immich. The PVC holds both `/cache` and `/tmp` through a `scratch`
subPath. Keep it separate from the app's model PVC so `ReadWriteOnce` does not tie them to one
node. Model download, extraction and scratch share its 10Gi budget; monitor free space and clean
abandoned scratch with the service stopped. The storage driver must grant `fsGroup: 1000` write access.

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
together, and check the pin against the release you intend to deploy.

Port-forward and read the provider back:

```bash
kubectl -n immich-memories port-forward svc/inference 8092:8092
curl -s localhost:8092/health
```

`CUDAExecutionProvider` on the CUDA overlay, `CPUExecutionProvider` on the other. If the CUDA one
says CPU, check its tag, device access, configured provider and driver/runtime logs.

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

### A cold cache volume

A fresh PVC is empty, and that is all right. Both overlays set `ALLOW_MODEL_DOWNLOADS=true`, and on
that setting the service fetches what it is missing the first time something asks for it: the
pinned DINOv2 export (88 MB), the pinned Marqo export (22.5 MB) and the Docling snapshot, The two exports are digest-checked; the Docling snapshot is revision-pinned. The first request
for each missing producer waits for its download. Later requests reuse the files while they
remain available.

To pre-seed an offline service, copy the two pinned exports to the service paths listed below
and populate its detector cache. A Job running the app's `models fetch` must explicitly use
those same encoder, Marqo and detector paths; the app defaults are different. With downloads
disabled, a request for an absent producer returns 503 naming the missing file.

The pod's root filesystem is read-only, so everything written at runtime has to point at a volume.
The overlay sets `HF_HOME=/cache/huggingface` and `TMPDIR=/tmp` for that reason. Without them the
Hugging Face download has nowhere to put its temporary files, fails with `Read-only file system (os
error 30)`. Failed loads retry with backoff after the underlying path is fixed. Keep both
variables if you write your own manifests.

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

Nothing here checks a credential: whatever reaches port 8092 gets an answer. Restrict it to trusted
callers on a private network, and take it back with
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
python -c 'import base64,json,sys; print(json.dumps({"image": base64.b64encode(open(sys.argv[1], "rb").read()).decode(), "producers": ["heads"]}))' photo.jpg | \
  curl -s http://localhost:8092/facts -H 'content-type: application/json' --data-binary @-
```

```json
{"producers": {"heads": {"encoder_key": "…", "facts": [
  {"head": "activity", "version": "public-v1", "label": "outdoors", "confidence": 0.71}]}}}
```

When a producer cannot load, `/facts` answers 503 with the reason in `detail`: which producer, which
artifact, which path. The service logs that line once per distinct message, so a run against a
broken producer is one line worth reading rather than one per picture, and the app repeats it as
`Remote classifiers returned HTTP 503: <detail>` rather than sending you looking for logs. A load
that failed is retried on a later request, backing off from 10 s to 2 minutes, so fixing the cause
does not need a restart.

A fact on the wire is the bank row without its asset id. The client stores what it is handed,
verbatim. Fact identity comes from the producing artifact and input, not the machine. The same picture through the `cpu` and the `cuda` image uses the same fact identity:
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
| `PROVIDER` | `auto` | `auto`, `cpu`, `cuda` or `coreml`. `auto` takes CUDA where available for the encoder, otherwise CPU. Both detectors stay on CPU |
| `REQUEST_THREADS` | `4` | the thread pool in front of ONNX Runtime |
| `IDLE_UNLOAD_SECONDS` | `300` | drop idle weights; `0` holds them |
| `PRELOAD` | `false` | load every producer at boot instead of on first use |
| `DETECTOR_CACHE_DIR` | the Hugging Face cache | where the detector snapshots live |
| `ALLOW_MODEL_DOWNLOADS` | `false` | let a cold cache fetch the pinned exports and the Docling snapshot itself |
| `MAX_IMAGE_BYTES` | `16777216` | refuse anything larger |

Idle unload drops the weights and keeps the process running. The next request reloads them.
Compose uses separate named disk volumes for the service's model cache and scratch.

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

## Captions

This image has no captioner or `/v1/chat/completions` route. The `full` preparation tier still
needs a separate [caption server](../configuration/editorial-preparation.md#captions).
