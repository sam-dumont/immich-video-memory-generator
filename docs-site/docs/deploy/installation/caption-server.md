---
sidebar_position: 6
title: Caption server
---

# The caption server

`tier: full` writes one sentence under every picture before any editing happens. That sentence
comes from an OpenAI-compatible endpoint nothing in this project ships, so you run one yourself:
Apple Silicon, Docker or Kubernetes, with or without a card.

## What it does

One 400 px JPEG tile per picture, one request, one 140-token answer, banked forever:

```json
{"description": "A stack of wrapped gifts on a wooden table.", "setting": "insufficient evidence"}
```

Two fields, both capped, at temperature 0 with a repetition penalty of 1.1 and a JSON schema the
server has to honour; the whole contract is on
[editorial annotation setup](../configuration/editorial-preparation.md#captions). The picture never
leaves as a full preview, which is a privacy contract and a speed one at once: sent whole it costs
519 prompt tokens against the tile's 188. A library is captioned once, so a second memory over the
same month sends nothing.

## Why the alias

Before a single library picture goes on the wire, the app checks that `GET /models` advertises
`smolvlm2-500m-base-public`, then sends three synthetic control tiles, red, blue and grey, and
requires a schema-valid answer to each.

The alias is a promise about behaviour, not a model name lookup: any endpoint can claim it. It
means the descriptions under that name came from the SmolVLM2-500M generation with this prompt and
this schema, so a bank filled last month and one filled today are comparable. Alias a 30B vision
model and the app will believe you, and the bank holds two things under one name. No commercial API
advertises this alias, so a hosted captioner means your own server behind a URL.

## Accepted artifacts

| Format | Repository | Revision | Runs on |
|---|---|---|---|
| MLX | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` | `fa57db46815177fbdfd65cc85a2b3416a8332268` | Apple Silicon |
| GGUF | `ggml-org/SmolVLM2-500M-Video-Instruct-GGUF` | `ccd7aae53bcb1997355c2f094959e72b3642ce17` | Anything llama.cpp runs on |

Same 500M model either way; pick what your hardware runs. The GGUF files this page pins:

| File | Size | SHA-256 |
|---|---|---|
| `SmolVLM2-500M-Video-Instruct-Q8_0.gguf` | 437 MB | `6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc` |
| `mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf` | 109 MB | `921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b` |

Q8_0 is the quantisation to run. Q4_K_M measured twice as slow and worse; f16 is eighteen times
slower for no gain.

## Apple Silicon, with mlxcel

The setup the project is developed against, and where the numbers below came from.

```bash
brew install lablup/tap/mlxcel
pip install huggingface-hub          # for the `hf` command, if you do not have it
SNAPSHOT=$(hf download mlx-community/SmolVLM2-500M-Video-Instruct-mlx \
  --revision fa57db46815177fbdfd65cc85a2b3416a8332268)
mlxcel serve --model "$SNAPSHOT" --alias smolvlm2-500m-base-public --host 0.0.0.0 --port 8092
```

`--host 0.0.0.0` because mlxcel binds `127.0.0.1` by default, which an app on a NAS or another host
cannot reach (`Caption endpoint unreachable`); nothing behind the port checks a credential, so keep
it on your LAN.

`hf download` prints the snapshot directory it wrote, which is what the server wants. Then in your
config:

```yaml
editorial:
  preparation:
    tier: full
    caption_base_url: http://localhost:8092/v1
```

That is the default value of `caption_base_url`, so on a Mac running the app locally there is
nothing to set but the tier. From the app in Docker Desktop on the same Mac, the address is
`http://host.docker.internal:8092/v1`; from a NAS, the Mac's LAN name or IP. oMLX cannot load
SmolVLM2 at all: if you already run oMLX for the reader, the captioner still needs its own process
on its own port.

## Docker and Linux, with llama.cpp

The compose file ships this as a profile: one service downloads and digest-checks the weights, one
serves them.


```bash
docker compose --profile captioner up -d
curl -s localhost:8094/v1/models
```

The first `up` pulls 546 MB; re-running it is cheap, the digest check short-circuits.
Compose publishes captions on host port **8094** and inference on **8092**, so both
profiles can run together. Inside the Compose network both services still use port 8092.

Then raise the tier and point the app at the service by name, both in `docker-compose.yml`:

```yaml
IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "full"
IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://immich-memories-captioner:8092/v1"
```

Running `ghcr.io/ggml-org/llama.cpp:server` by hand works the same way, with the weights
bind-mounted at `/models` and `--host 0.0.0.0 --ctx-size 8192 --threads 4`. Three of its flags
carry the contract, and each fails as something else:

| Flag | Leave it out and |
|---|---|
| `--alias smolvlm2-500m-base-public` | the server advertises the GGUF path instead, and preflight says the endpoint serves another model |
| `--mmproj …` | the model loads and answers, but it is blind: the control tiles fail and no library picture is sent |
| `--port 8092` | nothing answers where the app looks, and the row reads unreachable |

This recipe clears the alias and all three schema controls first attempt; 136 of 136 fixture
pictures validated. Do not reach for `--model-url` and `--mmproj-url` to skip the download: on
build b10920 they are accepted, ignored, and the server starts with zero models loaded, so
`/models` comes back empty and preflight reports the wrong problem.

### On an NVIDIA host

Two halves, neither of which works alone: the tag that carries CUDA
(`export CAPTIONER_TAG=server-cuda`) and the device. From a checkout the device comes from an
overlay:

```bash
docker compose -f docker-compose.yml -f docker/hwaccel.captioner.yml --profile captioner up -d
```

A downloaded `docker-compose.yml` reads no file beside itself, so the same two blocks ship in the
captioner service commented out. Uncomment both:

```yaml
    environment:
      LLAMA_ARG_N_GPU_LAYERS: "99"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities:
                - gpu
```

99 is "all of them", and a 500M model has 32. By hand that is `--gpus all`, the `server-cuda` image
and `--n-gpu-layers 99`. Raise the concurrency with it:

```yaml
IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_CONCURRENCY: "4"
```

### Speed, and what to set `caption_concurrency` to

Measured on 136 pictures, warm:

| Server | `caption_concurrency` | Per picture |
|---|---|---|
| llama.cpp Q8_0, CPU, 4 threads (Apple Silicon in Docker) | 1, the default | 0.27 s |
| llama.cpp Q8_0, CPU, 4 threads (Apple Silicon in Docker) | 4 | 3.39 s |
| mlxcel, MLX, same Mac, on the GPU | 1 | 0.08 to 0.23 s |
| llama.cpp Q8_0, CPU, 2-CPU Kubernetes pod | 1 | 3.5 s |
| llama.cpp Q8_0, CPU, Celeron J4125 NAS | 1 | 30.9 s |

It defaults to 1 because of the second row: four concurrent requests are twelve times slower on a
CPU captioner, and `--parallel 4` on the server side does not recover it, because four image
encodes share the threads of one. Raise it to 4 on a GPU, where it is worth about 1.5x.

The cluster row is what pays for a card: 133 pictures is 8 minutes on two cores, under 30 seconds
on a GPU, same weights. The NAS row is why the Synology and Celeron pages recommend `no_captions`:
thirteen thousand pictures at 30.9 s each is four days, against 32 minutes on a Mac with MLX.

## Kubernetes

```bash
kubectl create namespace immich-memories   # if you have not already
kubectl apply -k deploy/kubernetes/overlays/captioner        # CPU
kubectl apply -k deploy/kubernetes/overlays/captioner-cuda   # NVIDIA nodes
```

A Deployment, a ClusterIP Service on 8092, a NetworkPolicy and a 2 Gi PVC an init container fills
and digest-checks before the server starts. Neither overlay includes `base`, so both apply without
the Immich secret: the captioner holds no credential. It does receive a 400 px tile of every
picture in the library, so the Service stays ClusterIP and the NetworkPolicy allows ingress on 8092
only.

Point the app at it:

```yaml
editorial:
  preparation:
    tier: full
    caption_base_url: http://captioner:8092/v1
```

Across namespaces that is `captioner.immich-memories.svc.cluster.local:8092`.

The app's base manifest pins `no_captions`. Override that environment setting when
enabling this service; a config-file value cannot override it:

```bash
kubectl -n immich-memories set env deployment/immich-memories \
  IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=full \
  IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL=http://captioner:8092/v1
```

`captioner-cuda` is the same Deployment with the `server-cuda` image, `--n-gpu-layers 99` appended,
and the three things the GPU Operator wants: `runtimeClassName: nvidia`, the `nvidia.com/gpu.present`
node selector and the matching toleration. Nothing else changes, so pointing the app at it is the
block above plus `caption_concurrency: 4`.

It deliberately does not request `nvidia.com/gpu: 1`. Where one card is time-sliced per node that
resource has a single slot, the inference Deployment holds it, and a captioner asking for a second
stays Pending beside an idle card. Without the request it shares, which works because the weights
are 546 MB. With a card to spare, put the request back:

```yaml
resources:
  limits:
    nvidia.com/gpu: "1"
  requests:
    nvidia.com/gpu: "1"
```

Both overlays float their tag, `server` and `server-cuda`, and the two have to be one llama.cpp
build: pinning means `server-bNNNNN` and `server-cuda-bNNNNN`, both or neither.

## How preflight reports it

`Captions OK Serving smolvlm2-500m-base-public` is the row you want. Three ways it goes wrong:

| Row | What happened |
|---|---|
| `Caption endpoint unreachable` | nothing is listening, or it is not HTTP |
| `Caption endpoint serves another model` | a server answered and advertised something else |
| `Caption endpoint refused the request` | 401 or 403, so set `caption_api_key` |

On `no_captions` and `metadata_only` the row reads `SKIPPED`, and no endpoint is contacted.

## What a missing captioner costs

On `full`, prepare stops: the description producer stays outstanding and the failure names
`caption_base_url`. On the other two tiers nothing happens, because an absent description is not a
missing fact. Dropping to `no_captions` is a real loss though: the family-viewing gate has eight
findings only a description can name, so without captions it refuses what `full` refuses and can
never clear a unit. What each tier keeps is on [Running modes](../running-modes.md).

## Knowing which build wrote a caption

The alias is a contract, not a build identifier. Both recipes on this page advertise
`smolvlm2-500m-base-public` on port 8092, so neither the model name nor the URL can tell a bank
filled by mlx-serving from one filled by llama.cpp. New caption rows keep two things that came
from the weights instead:

- the served `/models` row, minus the `created` timestamp that llama.cpp answers with the current
  clock. llama.cpp reports `owned_by=llamacpp`, `meta.ftype=Q8_0` and `meta.n_params`; mlxcel
  reports `owned_by=user` and no `meta` at all.
- a 16-character digest of the three schema controls the probe already sends before any of your
  pictures. Greedy decoding makes it stable per build, and two builds that word a `setting`
  differently cannot produce the same digest.

An optional label of your own goes alongside them:

```yaml
advanced:
  editorial:
    preparation:
      caption_artifact_id: "SmolVLM2-Q8_0@your-weight-revision"
```

`prepare` prints one line per run naming every distinct captioner behind the bank it just read,
and the word `MIXED` when there is more than one:

```
caption origins: 2 distinct over 48689 captions MIXED [...]
```

All of this is a label for new rows. Changing the endpoint, the server or the artifact label does
**not** re-caption anything already banked, and rows written before origins were recorded stay
**unknown** rather than being credited to whatever is configured now.

`immich-memories runs why <asset-id> --run <run-id>` shows the origin saved with that run, not the
server configured today. Reader prompt text and bank identities are unchanged. For a deliberately
fresh bank, choose a separate `editorial.annotation_database`; preparing it recomputes all the
required facts, not only captions.

## Switching servers later

The bank keys on the producer name, `description:smolvlm2-500m-base-public@envelope-v3-compact`,
which carries no format, no quantisation and no weights digest. Swapping MLX for GGUF re-captions
nothing: every banked picture keeps the wording the old server gave it, and short of clearing the
description rows there is no way to ask for a re-caption.

That matters because the two builds word it differently. Same picture, same prompt, MLX against
GGUF Q8_0: `A bunch of balloons tied together with a string.` / `sky and clouds` against `A bunch
of balloons in the sky,` / `insufficient evidence`. `description` stays close, `setting` diverges,
and neither is graded better than the other. But a bank filled by both holds a mix with nothing
marking the seam, so if that matters for your library, pick one server and keep it.
