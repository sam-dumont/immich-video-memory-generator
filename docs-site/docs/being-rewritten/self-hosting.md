---
sidebar_position: 1
title: Self-hosting guide
sidebar_label: "Self-hosting: start here"
unlisted: true
---

:::note[Being rewritten]

This page is being split into the new docs. Its text moves to [Quick start](../get-started/quick-start.md), [The reader: config and measurements](../better/reader.md), [Caption server](../better/captions.md), [Troubleshooting](../reference/troubleshooting.md).

:::

# Self-hosting: from nothing to a first cut

Everything you stand up, in the order you stand it up. All of it runs on your hardware; nothing
calls a cloud API unless you point it at one.

First decide who reads the period. The **rules reader** needs nothing beyond the app and a NAS
can run it alone. A **model reader** needs a vision model with a 32k context; the graded one is
30B parameters at 4-bit, about 17 GB resident, on a Mac with 32 GB. What each choice costs and
loses is on [Running modes](./running-modes.md).

## The pieces

| Piece | What it does | Listens on | Resident |
|---|---|---|---|
| The app | Talks to Immich, prepares facts, renders, serves the web UI | `8080` | 2 to 4 GB |
| The reader (skip with `reader: rules`) | Reads the period as a story, weighs it, and is sent an 800 px tile of the few dozen candidates whose facts the edit asks about | wherever you serve it; oMLX defaults to `8000` | about 17 GB at 4-bit |
| The caption server (`full` tier only) | One 140-token description per picture, once, then banked | `8092` by default | 1 to 2 GB |

On the app's disk: the pinned 88 MB DINOv2-small ONNX encoder behind the eight context heads, and
two CPU detectors (about 400 MB). One command fetches all of it.

The reader must take images. Point a text-only model at it and you do not get a loud failure:
every picture request comes back empty, is banked as a failure, and the edit carries on with
`picture observations unavailable`. A finished video made without the evidence it asked for is
worse than a stop.

## Before you start

- Immich v2 or v3 and an API key (Account Settings, API Keys): read on assets, people, albums,
  timeline and search; add upload and album create/update for upload-back.
- Python 3.11 or later, or Docker, for the app.
- For a model reader, a machine that holds it for as long as its server is up.

## 1. Install the app

Docker:

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
docker compose up -d
```

Or natively, with the ONNX dependencies the heads and detectors need:

```bash
uv tool install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

The compose file publishes 8080 on loopback only. Before you reach it from another machine, turn
on [authentication](../run/authentication.mdx): the app holds an API key to your whole
library. Both routes are on the [Docker page](../run/docker.md).

## 2. Fetch the model files

Skip on `tier: metadata_only`.

```bash
immich-memories models fetch
```

That writes the encoder to `~/.immich-memories/models/triage/dinov2-small.onnx` after checking its
SHA-256 (`478164cd...`), the 22.5 MB sensitive-content detector to
`~/.immich-memories/models/detectors/nsfw-marqo-384.onnx`, and warms the document classifier's
snapshot into the Hugging Face cache (`~/.cache/huggingface` unless
`editorial.preparation.detector_cache_dir` names somewhere else, which in a container it should). With them cached,
`allow_model_downloads` stays `false` and means it. The encoder digest is checked at every run; no
other ONNX conversion passes, because ONNX exports are not byte-reproducible across torch versions.

## 3. Serve the reader

Skip with `reader: rules`.

Any OpenAI-compatible `/chat/completions` endpoint that takes images, honours
`response_format: json_schema` and has at least a 32k-token context. The graded configuration is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on [oMLX](https://github.com/jundot/omlx), Apple
Silicon:

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install jundot/omlx/omlx
omlx start        # serves on port 8000
```

Pull the model from the dashboard at `http://localhost:8000/admin/chat`. Anything else is expected
to work and ungraded (see [what has been tested](#what-has-been-tested)). Ten cells were timed and
priced on one real month, three of which stopped before producing a cut: [Readers](../better/reader.md).

## 4. Serve the captions

`full` tier only. The endpoint must advertise the alias `smolvlm2-500m-base-public` at `/models`;
the client checks the inventory and three synthetic schema controls before it sends a single
preview. Two artifacts are accepted, the MLX build of SmolVLM2-500M on Apple Silicon and the GGUF
build of the same model under llama.cpp everywhere else; the app enforces the alias and the schema
controls, not the revision. Copy-paste recipes for both, a compose profile and a Kubernetes
overlay are on
[Caption server](../better/captions.md); the full contract is on
[Editorial annotation setup](./editorial-preparation.md).

## 5. Point the app at them

```yaml
# ~/.immich-memories/config.yaml
immich:
  url: "https://photos.example.com"
  api_key: "your-api-key-here"

advanced:
  llm:
    provider: "openai-compatible"
    base_url: "http://localhost:8000/v1"
    model: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
  editorial:
    preparation:
      tier: full                                   # or no_captions, metadata_only
      caption_base_url: "http://localhost:8092/v1"
```

`llm.model` must be the string the server reports at `GET /v1/models`. `llm.base_url` defaults to
`http://localhost:8080/v1`, the app's own port: set it. If the reader answers `401`, give it its
token in `llm.api_key` (`IMMICH_MEMORIES_LLM__API_KEY`). In a container, `localhost` is the
container, so both endpoints need real hostnames: `host.docker.internal` for servers on the Docker
host itself, which Linux needs one line for
([Docker](../run/docker.md#reaching-a-model-server)).

Every key has an env var (`IMMICH_MEMORIES_LLM__BASE_URL`,
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER`, and so on), and the env var wins over the YAML.
The shipped `docker-compose.yml` sets the tier that way, so editing `tier:` in `config.yaml` inside
a container changes nothing until you edit the compose file too:
[Environment variables](../run/environment-variables.md).

## 6. Check before you cut

```bash
immich-memories config test      # credentials and the detected Immich contract, read-only
immich-memories preflight        # Immich, the reader, both model digests, the caption alias, hardware
```

Preflight follows the reader and tier you chose: rules skip the reader check, `no_captions` skips
the caption alias, `metadata_only` skips the model files.

## 7. The first cut

One month, not a year:

```bash
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

The cold pass runs every producer the tier asks for over every eligible picture and banks the
answers by producer and exact input; the second cut of that month is mostly the render. Measured
on a four-core Celeron NAS: 1.23 s per picture for every producer except the caption (1.44 s on a
second run a fifth later), 30.9 s for the caption. That is the whole reason
`editorial.preparation.tier` exists. What a first run costs on each host, end to end, is on
[Running modes](./running-modes.md#what-a-first-run-costs-end-to-end).

## One machine, or two

| Layout | Status |
|---|---|
| One Apple Silicon Mac, 32 GB or more: app, reader, captions, render, all local | **Graded.** The only end-to-end configuration anyone has judged |
| App on a NAS or mini-PC, the reader on a Mac or a 24 GB GPU box | **Selection measured** on a NAS with a LAN reader. Render throughput on that layout unmeasured |
| One amd64 box, CPU only, a small reader | **Expected to work, slowly.** Quality unmeasured |
| A NAS alone: `reader: rules`, `tier: metadata_only` | **Selection measured**: 279 s cold, 11.1 s warm on a DS423+. Simpler cut; review it |

Two things bite on the split layout: `localhost` inside a container is the container, so
`caption_base_url` and `llm.base_url` need real hostnames, and a server on another machine has to
listen on more than loopback (mlxcel needs `--host 0.0.0.0`); and the shipped Kubernetes
NetworkPolicy opens egress to DNS, 80, 443, 2283, 11434 and 8092 (11434 is Ollama's port, not
oMLX's 8000), so edit it for anything else. Hardware encoders (VAAPI, Quick Sync, NVENC) decode,
scale and encode; none of them runs inference.

## What has been tested

| Seat | Configuration | Status |
|---|---|---|
| Reader | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX, Apple Silicon | **Graded**: the matrix ran on this |
| Reader | Six others, local and hosted | **Measured** for time, tokens and list price on one real month, not for quality: [Readers](../better/reader.md) |
| Reader | Any other OpenAI-compatible vision model, 32k context, strict JSON | **Expected to work.** Quality unknown. Three cells stopped on that month; [Readers](../better/reader.md#the-three-that-stopped) says what stopped them |
| Reader | Text-only models | **Unsupported.** The picture pass posts images |
| Captions | `SmolVLM2-500M-Video-Instruct-mlx@fa57db46` | **Accepted**: the digest the banked descriptions came from |
| Captions | `ggml-org/SmolVLM2-500M-Video-Instruct-GGUF` Q8_0 under llama.cpp | **Accepted**: passes the alias and all three schema controls, wording differs |
| Captions | The same generation under vLLM | **Untested** |
| Encoder | The pinned DINOv2-small ONNX export | **Required, exact** |
| Detectors | The pinned sensitive-content ONNX export and Docling at its pinned revision | **Tested**, ONNX Runtime on the CPU provider |

## When it stops

| What you see | What it means |
|---|---|
| `editorial runtime needs a nonblank LLM model` | `llm.model` is empty with `reader: model`. Step 5 |
| `caption endpoint must advertise smolvlm2-500m-base-public` | Right weights, wrong name. Alias it |
| `caption endpoint failed the compact-v3 schema control` | It does not honour the JSON schema, or it is the wrong model |
| `public heads need the pinned DINOv2 ONNX export at …` | Step 2, or `triage.encoder` points at the wrong path |
| `nsfw_marqo has no model: …` or `doc_docling has no model: …` | Run `models fetch`; the message names the model and the fix |
| `Story-first selection needs prepared annotations at …` | The annotation store moved. Point `editorial.annotation_database` at it |

## Next

- [Your first memory](../get-started/first-film.mdx): the same thing through the web UI
- [Editorial annotation setup](./editorial-preparation.md): every pin and contract
