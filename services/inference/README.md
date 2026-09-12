# The inference service

The frozen pixel classifiers behind one HTTP port, modelled on
`immich-machine-learning`: one image per device variant, weights in a cache
volume, weights dropped when idle. Linux is the shipping path; the laptop keeps
the in-process path and needs no container.

```
GET  /ping     are you up
GET  /health   which producers are loaded, at which versions, on which provider
POST /facts    one picture in, what the frozen classifiers say about it
```

`POST /facts` takes `{"image": "<base64>", "producers": ["heads", "nsfw_marqo",
"doc_docling"]}` — `producers` omitted means all of them — and answers with one
entry per producer:

```json
{"producers": {"heads": {"encoder_key": "…", "facts": [
  {"head": "activity", "version": "public-v1", "label": "outdoors", "confidence": 0.71}]}}}
```

A fact on the wire is the bank row without its asset id, which is the point: the
client stores what it is handed, verbatim.

## The rule this service exists under

> Every producer key is content-addressed over the artifact, never over where it
> ran.

`encoder_key` stays `sha256(encoder_id + weights_sha256 + preprocess_version +
layout_version)`, computed here and returned on the wire. No URL, hostname,
device or execution-provider name enters it, so the same picture through the
`cpu` and the `cuda` image lands on one bank row. Facts are label-identical
across providers, not byte-identical — the pooled packs move by up to 5.5e-3
between the CoreML and CPU providers and every head label held, which is why the
parity test asserts labels, versions and keys rather than confidences.

Quantization is the other half of the same rule: an int8 encoder is a *different
artifact* and would need a different key, so it is not a provider setting.

## Settings

Every setting is read from the environment with the prefix
`IMMICH_MEMORIES_INFERENCE_`:

| variable | default | what it does |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8092` | where to listen; the image sets the host to `0.0.0.0` |
| `CACHE_DIR` | `/cache` | the model cache volume |
| `ENCODER` | `$CACHE_DIR/dinov2-small.onnx` | the pinned DINOv2 export, digest-verified on load |
| `BUNDLE` | the packaged public bundle | head bundle `.npz` |
| `PROVIDER` | `auto` | `auto`, `cpu`, `cuda` or `coreml` |
| `REQUEST_THREADS` | `4` | the thread pool in front of ONNX Runtime |
| `IDLE_UNLOAD_SECONDS` | `300` | drop idle weights; `0` holds them |
| `PRELOAD` | `false` | load every producer at boot |
| `DETECTOR_CACHE_DIR` | the Hugging Face cache | where the detector snapshots live |
| `ALLOW_MODEL_DOWNLOADS` | `false` | let a cold cache fetch the detector snapshots |
| `MAX_IMAGE_BYTES` | `16777216` | refuse anything larger |

Idle unload drops the weights and **keeps the process**. immich-ml sends itself
SIGINT after the same TTL; on a NAS a restart loop costs more than a resident
idle process, so that half is deliberately not copied.

## Running it

```bash
# from the repository, against a fetched encoder
PYTHONPATH=services/inference \
IMMICH_MEMORIES_INFERENCE_ENCODER=~/.immich-memories/models/triage/dinov2-small.onnx \
  uv run python -m immich_memories_inference

# the container, either variant
docker build -f docker/Dockerfile.inference --build-arg DEVICE=cpu \
  --build-arg APP_VERSION=0+local -t immich-memories-inference:cpu .
docker compose --profile inference up -d immich-memories-inference
curl -s localhost:8092/health
```

The service is not a distribution. It imports the application package — the same
triage engine and the same detector module preparation uses — which is what
makes a fact computed here indistinguishable from one computed in process. Its
web stack (fastapi, uvicorn) comes from the application's own dependency set.
