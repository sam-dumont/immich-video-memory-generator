---
title: Inference on a GPU box
---

# Inference on a GPU box

Reader: power user.

On a plain NAS the app runs the DINOv2 encoder, its eight heads and the two detectors in its own
process, on the CPU, once per picture. The inference service moves that work to another machine:
a GPU box, a Kubernetes node, or just a container you can restart on its own. The facts are the
same rows either way, so you can add it, move it or drop it without re-deriving anything.

It is modelled on `immich-machine-learning`: one image per backend, weights in a cache volume,
weights dropped when idle. It pays on a slow box. On a four-core Celeron NAS, sending the facts to
a service on a cluster took preparation from about 1.5 s to 0.45 s a picture; on a Mac, which
computes them in process in tens of milliseconds, it buys nothing. It does nothing for the render:
for that, see [Render on a GPU box](./gpu-render.md).

It answers on port `8092`, which is also where `caption_base_url` looks for the
[caption server](./captions.md). Two services, one default port: on one host, move one (the
compose file publishes captions on 8094 for that reason).

What leaves the app: one preview of each picture, once, to the URL you set. Nothing behind the port
checks a credential, so keep it on your LAN.

## The two images

| Tag | Platforms | Provider |
|---|---|---|
| `:X.Y.Z` | `linux/amd64`, `linux/arm64` | CPU |
| `:X.Y.Z-cuda` | `linux/amd64` | CUDA, falling back to CPU |

`openvino`, `armnn` and `rocm` are not shipped. Quick Sync, VAAPI and NVENC decode, scale and
encode; they run no inference.

The card accelerates the DINOv2 encoder, its eight heads and both detectors. All three are ONNX
graphs and all three open on the provider the deployment chose, so the `-cuda` image moves every
producer onto the GPU. A graph the card turns down falls back to the CPU.

Both images are published by the release, so you pull rather than build:

```bash
docker pull ghcr.io/sam-dumont/immich-video-memory-generator/inference:latest
docker pull ghcr.io/sam-dumont/immich-video-memory-generator/inference:latest-cuda
```

From a checkout, `docker/Dockerfile.inference` builds either one: `--build-arg DEVICE=cpu` or
`DEVICE=cuda`, with `--build-arg APP_VERSION=0+local`.

## Running it with compose

The service ships in `docker-compose.yml` behind a profile, so a plain `up -d` stays one container:

```bash
docker compose --profile inference up -d
curl -s localhost:8092/health
```

For a GPU, install the NVIDIA container toolkit from
[NVIDIA's guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
and check that a container sees the card:

```bash
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Then uncomment the device reservation on that service and change the tag with it:

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

The published file carries that block inline because it is downloaded on its own; from a checkout,
`docker/hwaccel.inference.yml` holds both backends as `extends:` targets instead.

`/health` names the provider per producer, in `producers.heads.providers`,
`producers.nsfw_marqo.providers` and `producers.doc_docling.providers`, each empty until that
producer has loaded. `CPUExecutionProvider` on a GPU host means the image is the CPU one, the
device reservation did not reach the container, `PROVIDER` names `cpu`, or the driver and the CUDA
runtime in the image do not match. Check the tag first: it is the usual one. A graph ONNX Runtime
turns down gets one WARNING naming the seat and the fallback, so the service never serves CPU
answers quietly.

## On Kubernetes

`deploy/kubernetes/overlays/inference` is the service on its own: a Deployment, a ClusterIP Service
on 8092, a 10Gi model-cache PVC and a NetworkPolicy. It does not pull in `base/`, so it needs no
Secret and no Immich, and runs in a cluster where the app does not.

```bash
kubectl create namespace immich-memories                     # base/ creates it too
kubectl apply -k deploy/kubernetes/overlays/inference        # CPU
kubectl apply -k deploy/kubernetes/overlays/inference-cuda   # NVIDIA nodes
```

`inference-cuda` is the same overlay plus one patch: `runtimeClassName: nvidia`, one
`nvidia.com/gpu`, the two `NVIDIA_*` env vars, the `nvidia.com/gpu.present=true` node selector, the
matching toleration and the `-cuda` tag. Each overlay pins its own tag in an `images:` entry; bump
both together, and check the pin against the releases page first, it trails the current release.

Port-forward and read the provider back:

```bash
kubectl -n immich-memories port-forward svc/inference 8092:8092
curl -s localhost:8092/health
```

Then [point the app at it](#point-the-app-at-it): `http://inference:8092` in the same namespace,
`http://inference.immich-memories.svc.cluster.local:8092` across namespaces. The base NetworkPolicy
already allows the app egress on 8092.

### A cold cache volume

The CUDA image bundles DINOv2, the context heads, Marqo, Docling, Laya ONNX and the SmolVLM2
caption model and projector. Its weights live in `/opt/immich-models`, outside the writable
cache mount. Downloads are unnecessary at startup; the image sets `HF_HUB_OFFLINE=1`.
The app's CLI can use the same image, with the bundled paths already configured.

The caption server is a second process using the same image and layers:

```bash
image=ghcr.io/sam-dumont/immich-video-memory-generator/inference:latest-cuda
docker run --rm --gpus all -p 127.0.0.1:8094:8092 \
  -v immich-memories-model-cache:/cache \
  "$image" immich-memories-captioner
```

Point `advanced.editorial.preparation.caption_base_url` at `http://localhost:8094/v1` when
running the app on the host. Between containers, use the caption container's hostname and
port 8092. The caption runtime is pinned by image digest; older NVIDIA cards may compile
kernels on their first request. Its bounded JIT cache stays on `/cache` across restarts.
Laya runs in the app process; the `/facts` service serves the image classifiers.

The CPU image keeps its smaller download. A fresh PVC is empty, which is fine. Both overlays
set `ALLOW_MODEL_DOWNLOADS=true`, and the CPU
service then fetches what it is missing on first use: the pinned DINOv2 export (88 MB), the pinned
Marqo export (22.5 MB) and the Docling snapshot. The ONNX exports are checked against the same
SHA-256 `immich-memories models fetch` pins, the Docling snapshot by Hugging Face revision. Only
the first `/facts` call after a cold start waits for it.

To fill the volume yourself, `kubectl cp` the two ONNX exports into `/cache`, or run
`immich-memories models fetch` in a Job that mounts the same claim. With
`ALLOW_MODEL_DOWNLOADS=false` that is the only way in, and a request for a missing file answers 503
naming the file and the path it wants it at.

The pod's root filesystem is read-only, so the overlay sets `HF_HOME=/cache/huggingface` and
`TMPDIR=/tmp`. Without them the download has nowhere to put its temporary files, fails with
`Read-only file system (os error 30)`, and every `/facts` request answers 503 until the pod
restarts. Keep both if you write your own manifests.

### Reach the service from outside the cluster

`http://inference:8092` only resolves inside the cluster. For a NAS or a laptop on the LAN,
`overlays/inference-lan` adds a second Service, type LoadBalancer, on the same pods and port. The
ClusterIP Service is untouched, so no in-cluster caller starts riding an external address, and it
composes with either device overlay.

```bash
kubectl apply -k deploy/kubernetes/overlays/inference-lan
kubectl -n immich-memories get service inference-lan
```

Put what that prints in `facts_base_url`. The address comes from the cluster's load-balancer
controller: without one the Service sits at `<pending>` forever. Nothing behind port 8092 checks a
credential, so do not give it a routable address, and
`kubectl delete -k deploy/kubernetes/overlays/inference-lan` when you are done.

## What it answers

| Endpoint | Question |
|---|---|
| `GET /ping` | are you up |
| `GET /health` | which producers are loaded, at which versions, on which provider each |
| `POST /facts` | one picture in: what do the frozen classifiers say about it |

```bash
python3 -c 'import base64,json,sys; print(json.dumps({"image": base64.b64encode(open(sys.argv[1],"rb").read()).decode(), "producers": ["heads"]}))' photo.jpg \
  | curl -s localhost:8092/facts -H 'content-type: application/json' --data-binary @-
```

```json
{"producers": {"heads": {"encoder_key": "…", "facts": [
  {"head": "activity", "version": "public-v1", "label": "outdoors", "confidence": 0.71}]}}}
```

When a producer cannot load, `/facts` answers 503 with the reason in `detail`: which producer,
which artifact, which path. The app repeats that as `Remote classifiers returned HTTP 503:
<detail>`, and the service logs it once per distinct message rather than once per picture. A failed
load is retried on a later request, backing off from 10 s to 2 minutes, so fixing the cause needs
no restart.

A fact on the wire is the bank row without its asset id, and the client stores it verbatim:
**a fact's identity is the artifact that produced it, never the machine that ran it.** No URL,
hostname, device or provider name enters any key, so the same picture through the `cpu` and the
`cuda` image lands on one row, label-identical rather than byte-identical.

## Settings

Every setting is an environment variable prefixed `IMMICH_MEMORIES_INFERENCE_`:

| Variable | Default | What it does |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8092` | where to listen. The image sets the host to `0.0.0.0` |
| `CACHE_DIR` | `/cache` | the model cache volume |
| `ENCODER` | `$CACHE_DIR/dinov2-small.onnx` | the pinned DINOv2 export, digest-verified on load |
| `MARQO_ONNX` | `$CACHE_DIR/nsfw-marqo-384.onnx` | the pinned sensitive-content ONNX export |
| `BUNDLE` | the packaged public bundle | head bundle `.npz` |
| `PROVIDER` | `auto` | `auto`, `cpu`, `cuda` or `coreml`. `auto` takes CUDA where the provider is present and CPU otherwise. CoreML is selectable but measured 6 to 8 times slower than the CPU provider on this export, at 9 times the resident memory |
| `REQUEST_THREADS` | `4` | the thread pool in front of ONNX Runtime. The app's `facts_concurrency` is what fills it |
| `IDLE_UNLOAD_SECONDS` | `300` | drop idle weights; `0` holds them |
| `PRELOAD` | `false` | load every producer at boot instead of on first use |
| `DETECTOR_CACHE_DIR` | `/cache/huggingface` in the published image (`$HF_HOME` otherwise) | where the detector snapshots live |
| `ALLOW_MODEL_DOWNLOADS` | `false` | let a cold cache fetch the pinned exports and the Docling snapshot itself |
| `MAX_IMAGE_BYTES` | `16777216` | refuse anything larger |

Idle unload drops the weights and **keeps the process**: the next request reloads them. Changing
the provider re-keys nothing, so any of this can be retried without re-deriving a fact.

## Point the app at it

```yaml
advanced:
  inference:
    facts_base_url: http://inference:8092
```

That is the whole switch (`IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` in the environment, two
underscores; the service's own settings take one). `prepare` and `generate` then send each
picture's preview to `/facts` once, asking for every producer the tier still needs. The other four
keys, `producers`, `timeout_seconds`, `facts_concurrency` and `fallback_to_local`, are in the
[config reference](../reference/config-reference.md#inference-service).

`facts_concurrency` (default 8, 1 to 32) decides whether the card behind the service is worth
anything: one request at a time measured 0.69 s a picture whatever the card was doing, because the
round trip and not the classifier was the cost. Match it to the service's `REQUEST_THREADS` and
give the pod the CPU to go with them; past that the requests queue inside the service. Raising it
re-derives nothing. What a GPU-backed service, a CPU-backed one and a pod computing its own facts
each measure is on [Measured](./measured.md).

The summary's `remote_facts` row carries a `service s/pic` column beside the wall clock, off the
`X-Facts-Seconds` header. A wide gap between the two is the network or a queue inside the service;
a narrow one means the classifiers are the cost, and only a faster device or a smaller scope moves
it.
