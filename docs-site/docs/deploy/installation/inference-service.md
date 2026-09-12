---
sidebar_position: 5
title: Inference service
---

# The inference service

The encoder, the six public heads and the two detectors behind one HTTP port, in their own
container, with their own device variant. It is modelled on `immich-machine-learning` and reads
the same way: one image per backend, weights in a cache volume, weights dropped when idle.

It is **optional**. Leave it out and the app runs the same producers in process, which is what a
laptop wants. Run it when the models belong somewhere other than the box running the app — a GPU
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
encode — they do not run inference, and that stays true on every page here.

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
image is the CPU one — those are the only two causes.

## What it answers

| Endpoint | Question |
|---|---|
| `GET /ping` | are you up |
| `GET /health` | which producers are loaded, at which versions, on which provider |
| `POST /facts` | one picture in — what do the frozen classifiers say about it |

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
machine that ran it.** The same picture through the `cpu` and the `cuda` image lands on one row —
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

## Not yet

- The app has no setting to point at this service; preparation still runs in process. That switch,
  and storing what the service returned verbatim, is the next item.
- The captioner is not in the image yet, so `/v1/chat/completions` is still your own caption server.
- The encoder and Marqo exports must already exist. Point `ENCODER` and `MARQO_ONNX` at the digest-pinned files from the app's `models fetch`, or place them at the cache paths above. Docling can fetch its snapshot into `/cache/huggingface` when `ALLOW_MODEL_DOWNLOADS` is on.

For an image check before a release, dispatch the Release workflow with `inference_only: true`. It builds commit-tagged CPU and CUDA images without creating a version or moving `latest`; the CUDA base account is reused at UID/GID 1000 so the cache volume has the same ownership as the CPU image.
