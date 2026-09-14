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

The card accelerates the **DINOv2 encoder and its six heads** and both detectors. All three are
ONNX graphs, and all three open on the provider the deployment chose, so the `-cuda` image moves
every producer onto the GPU rather than one of three. If the card turns a graph down, that seat
falls back to the CPU and logs a WARNING saying so.

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
`CPUExecutionProvider` on a GPU host means one of four things: the image is the CPU one, the device
reservation did not reach the container, `PROVIDER` names `cpu`, or the driver and CUDA runtime in
the image do not match. Check the tag first: it is the usual one.

Each producer holds its own session, so each gets its own answer:
`producers.heads.providers`, `producers.nsfw_marqo.providers` and `producers.doc_docling.providers`
say what actually took the graph, and stay empty until that producer has been loaded. A seat on the
CPU while the others are on the card is what a pegged CPU limit at 4% GPU looks like from outside
the pod. If ONNX Runtime turns a graph down, the service logs one WARNING naming the seat and the
provider it fell back to; it never serves CPU answers quietly.

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

### A cold cache volume

A fresh PVC is empty, and that is all right. Both overlays set `ALLOW_MODEL_DOWNLOADS=true`, and on
that setting the service fetches what it is missing the first time something asks for it: the
pinned DINOv2 export (88 MB), the pinned Marqo export (22.5 MB) and the Docling snapshot. The two
ONNX exports are checked against the same SHA-256 `immich-memories models fetch` pins; the Docling
snapshot is pinned by Hugging Face revision, not by digest. The first `/facts` call
after a cold start waits for the download. Nothing after it does.

To fill the volume yourself instead, `kubectl cp` the two ONNX exports into `/cache`, or run
`immich-memories models fetch` in a Job that mounts the same claim. With
`ALLOW_MODEL_DOWNLOADS=false` that is the only way in, and a request for a producer whose file is
absent answers 503 naming the file and the path it wants it at.

The pod's root filesystem is read-only, so everything written at runtime has to point at a volume.
The overlay sets `HF_HOME=/cache/huggingface` and `TMPDIR=/tmp` for that reason. Without them the
Hugging Face download has nowhere to put its temporary files, fails with `Read-only file system (os
error 30)`, and every `/facts` request answers 503 until the pod is restarted. Keep both if you
write your own manifests.

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
| `GET /health` | which producers are loaded, at which versions, on which provider each |
| `POST /facts` | one picture in: what do the frozen classifiers say about it |

```bash
python3 -c 'import base64,json,sys; print(json.dumps({"image": base64.b64encode(open(sys.argv[1],"rb").read()).decode(), "producers": ["heads"]}))' photo.jpg \
  | curl -s localhost:8092/facts -H 'content-type: application/json' --data-binary @-
```

(`base64 -i` is the macOS spelling and GNU `base64` wraps its output, so the shell one-liner that
looks obvious here is not portable.)

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
| `REQUEST_THREADS` | `4` | the thread pool in front of ONNX Runtime. The app's `facts_concurrency` is what fills it |
| `IDLE_UNLOAD_SECONDS` | `300` | drop idle weights; `0` holds them |
| `PRELOAD` | `false` | load every producer at boot instead of on first use |
| `DETECTOR_CACHE_DIR` | `/cache/huggingface` in the published image (`$HF_HOME` otherwise) | where the detector snapshots live |
| `ALLOW_MODEL_DOWNLOADS` | `false` | let a cold cache fetch the pinned exports and the Docling snapshot itself |
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

### How many pictures at once

`facts_concurrency` (default 8, 1 to 32) is how many `/facts` requests the app keeps in flight.

One at a time is what the client used to do, and it is slow for a reason that has nothing to do
with the card: measured on a cluster Job against a T1000 on `no_captions`, 3,709 pictures took 42.7
minutes, 0.69 s each, the same rate a 133-picture demo got. A rate that does not move with the size
of the scope is per-request latency, not throughput, and the service was sitting on
`REQUEST_THREADS` seats with nothing in them. A 13,552-picture month would have taken 2.6 hours of
facts alone, against 23 to 40 ms a picture for the same work computed in process on a Mac.

At the default of 8, that month was measured: **0.2445 s a picture, 87 % of a 64-minute
preparation**, with the classifiers on a T1000 behind the service. The card is most of that
difference. On the fixture month the same pod paid 0.6083 s a picture to a CPU-backed service and
0.1957 s to a GPU-backed one, while a pod computing its own facts in process managed 0.2555 s. The
gap between a CPU-backed service and a fast pod's own facts is about 2.4x in the pod's favour; the
gap between a CPU-backed and a GPU-backed service is about 3.1x the other way.

Raising it re-derives nothing and moves no row: the answers are banked in the order the pictures
were asked for, whatever order they come back in, and a fact's identity is still the artifact that
produced it. Match it to the service's `REQUEST_THREADS` and give the pod the CPU to go with them;
past that point the requests queue inside the service instead of on the wire, which buys nothing.

`prepare` says which number it ran at, and the summary's `remote_facts` row gains a
`service s/pic` column next to the wall clock:

```
producer        pending   s/picture   share    elapsed  service s/pic
previews           1440      0.0241    9.4%       35 s              —
remote_facts       1440      0.0921   36.0%        2 min         0.0308
```

The left number is what the app waited. The right one is what the service says it spent deciding
the picture, off the `X-Facts-Seconds` header it puts on every answer. A wide gap is the network,
the request rate or a queue inside the service; a narrow one means the classifiers are the cost and
only a faster device or a smaller scope will move it.

## Not yet

- The captioner is not in the image yet, so `/v1/chat/completions` is still your own caption server.
- One picture per request. `facts_concurrency` sends several at once, so the wire is busy, but
  the service still runs one ONNX graph per picture rather than one batch. A batched request and
  response shape, with a per-picture error path, is not built.

For an image check before a release, dispatch the Release workflow with `inference_only: true`. It builds commit-tagged CPU and CUDA images without creating a version or moving `latest`; the CUDA base account is reused at UID/GID 1000 so the cache volume has the same ownership as the CPU image.
