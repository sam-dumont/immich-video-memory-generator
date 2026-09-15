---
sidebar_position: 6
title: Caption server
---

# The caption server

`tier: full` writes one sentence under every picture before any editing happens. It needs an
OpenAI-compatible endpoint that nothing in this project ships. The docs used to say "your own
caption server" and leave it there, which is how a first run on `full` stops at prepare with no
idea what to install. This page is the missing half.

## What it does

One 400 px JPEG tile per picture, one request, one 140-token answer, banked forever:

```json
{"description": "A stack of wrapped gifts on a wooden table.", "setting": "insufficient evidence"}
```

Two fields, both capped (120 and 32 characters), at temperature 0 with a repetition penalty of 1.1
and a JSON schema the server has to honour. The picture never leaves as a full preview: the tile is
400 px on its long edge, which is a privacy contract and a speed one at the same time. The same
picture sent whole costs 519 prompt tokens against the tile's 188, and takes three times as long.

Captions are banked per picture in the annotation store. A library is captioned once. A second
memory over the same month sends nothing.

## Why the alias

The app checks one thing before it sends a single picture: that `GET /models` advertises
`smolvlm2-500m-base-public`. Then it sends three synthetic control tiles, one red, one blue, one
grey, and requires a schema-valid answer to each. Only then does a library picture go on the wire.

The alias is a promise about behaviour, not a model name lookup. Any endpoint can claim it. What it
means is that the descriptions written under that name came from the SmolVLM2-500M generation with
this prompt and this schema, so a bank filled last month and a bank filled today are comparable. If
you point `caption_base_url` at a 30B vision model and alias it, the app will believe you, and the
bank will hold two different things under one name.

No commercial API advertises this alias, and none will. A hosted captioner means your own server
behind a URL.

## Accepted artifacts

| Format | Repository | Revision | Runs on |
|---|---|---|---|
| MLX | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` | `fa57db46815177fbdfd65cc85a2b3416a8332268` | Apple Silicon |
| GGUF | `ggml-org/SmolVLM2-500M-Video-Instruct-GGUF` | `ccd7aae53bcb1997355c2f094959e72b3642ce17` | Anything llama.cpp runs on |

Both are the same 500M model. Pick the one your hardware runs.

The GGUF files this page pins, with their digests:

| File | Size | SHA-256 |
|---|---|---|
| `SmolVLM2-500M-Video-Instruct-Q8_0.gguf` | 437 MB | `6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc` |
| `mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf` | 109 MB | `921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b` |

Q8_0 is the quantisation to run. Q4_K_M measured twice as slow and worse; f16 is eighteen times
slower for no gain.

## Apple Silicon, with mlxcel

This is the setup the project is developed against, and the numbers below came off it.

```bash
brew install lablup/tap/mlxcel
pip install huggingface-hub          # for the `hf` command, if you do not have it
hf download mlx-community/SmolVLM2-500M-Video-Instruct-mlx \
  --revision fa57db46815177fbdfd65cc85a2b3416a8332268
```

`hf download` prints the snapshot directory it wrote. Hand that path to the server:

```bash
mlxcel serve \
  --model ~/.cache/huggingface/hub/models--mlx-community--SmolVLM2-500M-Video-Instruct-mlx/snapshots/fa57db46815177fbdfd65cc85a2b3416a8332268 \
  --alias smolvlm2-500m-base-public \
  --port 8092
```

Then in your config:

```yaml
editorial:
  preparation:
    tier: full
    caption_base_url: http://localhost:8092/v1
```

That is the default value of `caption_base_url`, so on a Mac running the app locally there is
nothing to set but the tier.

:::note
oMLX cannot load SmolVLM2 at all. If you already run oMLX for the reader, the captioner still needs
its own process. They are different endpoints on different ports.
:::

## Docker and Linux, with llama.cpp

The compose file ships this as a profile. Two services: one downloads and verifies the weights,
one serves them.

```bash
docker compose --profile captioner up -d
curl -s localhost:8092/v1/models
```

The `inference` profile publishes the same host port, so running both means changing one of the two
left-hand sides in `docker-compose.yml`. Inside the compose network they are two service names on
one port number and nothing collides; only a `curl` from the host cares.

The first `up` pulls 546 MB and checks both digests before the server starts. Re-running it is
cheap: the digest check short-circuits and nothing downloads twice.

Then raise the tier and point the app at the service by name, both in `docker-compose.yml`:

```yaml
IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "full"
IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://immich-memories-captioner:8092/v1"
```

`caption_concurrency` defaults to 1, which is what this container wants. The section below says
when to raise it.

### On an NVIDIA host

Two halves, and neither works without the other. The tag, which carries CUDA:

```bash
export CAPTIONER_TAG=server-cuda
```

And the device. From a checkout, the overlay beside the inference service's:

```bash
docker compose -f docker-compose.yml -f docker/hwaccel.captioner.yml --profile captioner up -d
```

From a downloaded `docker-compose.yml`, which reads no file beside itself, the same two blocks are
in the captioner service commented out. Uncomment both:

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

99 is "all of them", and a 500M model has 32. The layer count is an environment variable rather
than a `--n-gpu-layers` flag because the flag would go in the service's `command:`, which is also
where the alias, the projector path and the context size live: llama-server reads
`LLAMA_ARG_N_GPU_LAYERS` for the same setting and that list stays written once.

On a GPU, raise the concurrency too. Four requests in flight is what the default was sized for
before the CPU measurement below moved it to one:

```yaml
IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_CONCURRENCY: "4"
```

To run it without compose, the same container by hand:

```bash
docker run -d --name captioner -p 127.0.0.1:8092:8092 \
  -v "$PWD/caption-models:/models" \
  ghcr.io/ggml-org/llama.cpp:server \
  --model /models/SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
  --mmproj /models/mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
  --alias smolvlm2-500m-base-public \
  --host 0.0.0.0 --port 8092 --jinja --ctx-size 8192 --threads 4
```

With a card, that is `--gpus all`, the `server-cuda` image and one more flag:

```bash
docker run -d --name captioner --gpus all -p 127.0.0.1:8092:8092 \
  -v "$PWD/caption-models:/models" \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  --model /models/SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
  --mmproj /models/mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
  --alias smolvlm2-500m-base-public \
  --host 0.0.0.0 --port 8092 --jinja --ctx-size 8192 --n-gpu-layers 99
```

Three flags carry the contract, and each one fails as something else:

| Flag | Leave it out and |
|---|---|
| `--alias smolvlm2-500m-base-public` | the server advertises the GGUF file path instead, and preflight says the endpoint serves another model |
| `--mmproj …` | the model loads and answers, but it is blind. The three control tiles fail and no library picture is sent |
| `--port 8092` | nothing answers on the port the app defaults to, and the row reads unreachable |

`--jinja` is in the recipes above and does nothing on build b10920, where it is already the
default. It is there so the recipe keeps working against a build where it is not. `--no-jinja`
passed the controls too, so this one is belt and braces rather than a requirement.

Do not reach for `--model-url` and `--mmproj-url` to skip the download step. On build b10920 they are
accepted, ignored, and the server starts in router mode with zero models loaded, so `/models` comes
back empty and preflight reports the wrong problem.

### What it passes

Run against the app's own check, the llama.cpp recipe clears the alias and all three synthetic
schema controls, first attempt, no changes to the request the app sends. The non-standard
`repetition_penalty` field is accepted rather than rejected. Grammar-constrained decoding honours
the schema's length caps. On 136 CC0 fixture pictures, 136 of 136 answers validated.

### Speed, and the one setting that matters

Measured on the same 136 pictures, same server, warm:

| Server | `caption_concurrency` | Per picture |
|---|---|---|
| llama.cpp Q8_0, CPU, 4 threads (Apple Silicon in Docker) | 1, the default | 0.27 s |
| llama.cpp Q8_0, CPU, 4 threads (Apple Silicon in Docker) | 4 | 3.39 s |
| mlxcel, MLX, same Mac, on the GPU | 1 | 0.08 to 0.23 s |
| llama.cpp Q8_0, CPU, 2-CPU Kubernetes pod | 1 | 3.5 s |
| llama.cpp Q8_0, CPU, Celeron J4125 NAS | 1 | 30.9 s |

`caption_concurrency` defaults to 1 because of the second row. Four concurrent requests are twelve
times slower on a CPU captioner, and `--parallel 4` on the server side does not recover it: four
image encodes share the threads of one and none of them finishes sooner. Raise it to 4 when the
captioner is on a GPU, where it is worth about 1.5x.

The cluster row is the one that pays for a card. One month of the fixture library is 133 pictures,
which is 8 minutes on two cores and under 30 seconds on a GPU. Same weights either way, so it costs
a tag and a device: the `server-cuda` compose recipe above, or the `captioner-cuda` overlay below.

The NAS row is why the Synology and Celeron pages recommend `no_captions`. Thirteen thousand
pictures at 30 s each is four days. The same month on an Apple Silicon Mac with the MLX server is
32 minutes.

### The wording is not identical

Same model generation, different numeric path, different sentences. Same picture, same prompt:

| | MLX | GGUF Q8_0 |
|---|---|---|
| description | `A bunch of balloons tied together with a string.` | `A bunch of balloons in the sky,` |
| setting | `sky and clouds` | `insufficient evidence` |

`description` stays close. `setting` diverges more: the GGUF build hedges where MLX commits, and
occasionally hands back text it read in the picture. Both are within what the editor expects from
this seat, and neither is graded better than the other. It matters for the section below.

## Kubernetes

```bash
kubectl create namespace immich-memories   # if you have not already
kubectl apply -k deploy/kubernetes/overlays/captioner
```

A Deployment, a ClusterIP Service on 8092, a NetworkPolicy and a 2 Gi PVC for the weights. An init
container downloads the two pinned files onto the claim and verifies both digests before the server
container starts; on a warm claim it exits in a second. The pod runs as UID 1000 with a read-only
root filesystem, which llama.cpp is happy with since everything it reads is the mounted claim.

Point the app at it:

```yaml
editorial:
  preparation:
    tier: full
    caption_base_url: http://captioner:8092/v1
```

Across namespaces that is `captioner.immich-memories.svc.cluster.local:8092`.

The overlay does not include `base`, so it applies on its own without the Immich secret. The
captioner holds no credential and never talks to Immich. What it does receive is a 400 px tile of
every picture in the library, so the Service stays ClusterIP and the NetworkPolicy allows ingress
on 8092 only.

### Knowing which build wrote a caption

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

### On a GPU node

```bash
kubectl apply -k deploy/kubernetes/overlays/captioner-cuda
```

The same Deployment with the `server-cuda` image, `--n-gpu-layers 99` appended to the args, and the
three things the GPU Operator wants: `runtimeClassName: nvidia`, the `nvidia.com/gpu.present` node
selector and the matching toleration. The alias, the weights, the claim and the Service do not
change, so pointing the app at it is the same block as above with `caption_concurrency: 4`.

What it deliberately does not do is request `nvidia.com/gpu: 1`. On a cluster that time-slices one
card per node, that resource has a single allocatable slot and the inference Deployment holds it, so
a captioner asking for a second one stays Pending next to a card that is idle between facts
requests. Without the request it shares, which works because the weights are 546 MB. If your cluster
has a card to spare, or does not time-slice, put the request back and let the scheduler keep the two
apart:

```yaml
resources:
  limits:
    nvidia.com/gpu: "1"
  requests:
    nvidia.com/gpu: "1"
```

The tag floats on `server-cuda` because the CPU overlay floats on `server`, and the two have to be
one llama.cpp build. ggml-org publishes them as a pair per build, so pinning means
`server-bNNNNN` there and `server-cuda-bNNNNN` here: both or neither.

## How preflight reports it

```
$ immich-memories preflight
...
Captions        OK       Serving smolvlm2-500m-base-public
```

Three ways that row goes wrong, and what each one means:

| Row | What happened |
|---|---|
| `Caption endpoint unreachable` | nothing is listening, or it is not HTTP |
| `Caption endpoint serves another model` | a server answered and advertised something else |
| `Caption endpoint refused the request` | 401 or 403, so set `caption_api_key` |

The first two now name this page in their details. The third names the key, because the URL is fine
and repointing it is not the fix.

On `no_captions` and `metadata_only` the row reads `SKIPPED`, and no endpoint is contacted.

## What a missing captioner costs, per tier

| Tier | No caption server means |
|---|---|
| `full` | prepare stops. The description producer stays outstanding, the failure names `caption_base_url`, and the run does not reach editing |
| `no_captions` | nothing. The endpoint is never contacted, and an absent description is not a missing fact |
| `metadata_only` | nothing, and the models are not fetched either |

Dropping from `full` to `no_captions` is a real loss and worth knowing before you make it. The
family-viewing gate has eight findings that only a description can name, so without captions the
gate can refuse a unit but can never clear one. It matches `full` on what it refuses and holds the
rest. Details are on [Editorial annotation setup](../configuration/editorial-preparation.md).

## Switching servers later

The bank keys on the producer name, `description:smolvlm2-500m-base-public@envelope-v3-compact`,
and that string carries no format, no quantisation and no weights digest. The app records nothing
about which server answered.

So swapping MLX for GGUF, or the reverse, re-captions nothing. Every picture already banked stays
banked with the wording the old server gave it, and only new pictures get the new one. There is no
way to ask for a re-caption short of clearing the description rows, and the app will not refuse the
second artifact, because it never learned about the first.

That is a deliberate trade, and the cost is the section above: the two builds word `setting`
differently, and a bank filled by both holds a mix with nothing marking the seam. If that matters
for your library, pick one server and keep it. For most people it does not: the descriptions feed
an editor that reads them as evidence, not a catalogue anyone diffs.

## Related

- [Editorial annotation setup](../configuration/editorial-preparation.md) for the whole caption
  contract, the API key, and what the tiers change
- [Running modes](../running-modes.md) for measured timings per tier
- [The inference service](./inference-service.md), which is a different service on the same port
  number and does not caption
