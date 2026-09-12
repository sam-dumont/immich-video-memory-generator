---
sidebar_position: 1
title: Self-hosting guide
sidebar_label: "Self-hosting: start here"
---

# Self-hosting: from nothing to a first cut

Everything you have to stand up, in the order you have to stand it up, on one page. All of it
runs on your own hardware; none of it calls a cloud API.

**It is heavy machinery.** Two of the three services are models, one of them wants 17 GB of memory
to itself, and the app refuses to cut rather than guess without them. The cheapest thing that works
is one Apple Silicon Mac with 32 GB; the next cheapest is the app wherever you like plus one box
that can hold the models. Nothing here runs on a NAS alone.

## What you are standing up

Three services and two files on disk.

| Piece | What it does | Listens on | Resident memory |
|---|---|---|---|
| **The app** | Talks to Immich, prepares facts, renders the video, serves the web UI | `8080` | 2–4 GB |
| **The reader** | Groups the period's days into stories, weighs them, picks the pictures, and *looks* at some of them: it is sent an 800 px tile of the candidates whose facts the edit demands, a few dozen per memory | wherever you serve it (oMLX defaults to `8000`) | ~17 GB at 4-bit |
| **The caption server** | One 140-token description and setting per picture, once, then it is banked forever | `8092` by default | 1–2 GB |

On the app's disk: the pinned **DINOv2-small ONNX export** (88 MB) behind the six context heads,
and two **CPU detector** snapshots (~400 MB). One command fetches both. The head bundle itself is
in the wheel.

The reader is one model doing two jobs, and it needs vision. Point a text-only model at it and
you do not get a loud failure: every picture request comes back empty, gets banked as a
completion failure, and the edit reads `picture observations unavailable` on those assets and
carries on. You get a finished video made without the evidence it asked for, which is worse than
a stop. The only blank this seat refuses outright is an empty `llm.model`.

## Before you start

- **Immich v2 or v3** and an API key (Account Settings → API Keys). Read access to assets, people,
  albums, timeline and search; add asset upload and album create/update if you want upload-back.
- **A machine that can hold the reader.** The graded model is 30B parameters at 4 bits, so
  roughly 17 GB of weights stay resident for as long as the server is up. That figure is the
  arithmetic, not a measurement. See [one machine or two](#one-machine-or-two) before you pick
  where things run.
- **Python 3.11+ or Docker** for the app itself.

## 1. Install the app

Docker:

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
docker compose up -d
```

Or natively, with the inference dependencies the heads and detectors need:

```bash
uv tool install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

The compose file publishes 8080 on loopback only. Before you reach it from another machine, turn
on [authentication](./configuration/authentication.mdx): the app holds an API key to your whole
library. Details on both routes are on the [Docker page](./installation/docker.md).

## 2. Fetch the model files

```bash
immich-memories models fetch
```

That writes the encoder to `~/.immich-memories/models/triage/dinov2-small.onnx`, verifying its
SHA-256 (`478164cd…`) before it writes, and warms the two pinned detector snapshots into the
Hugging Face cache. With them cached, `allow_model_downloads` stays `false` and means it.

The export is digest-checked at every run and no other ONNX conversion will pass: ONNX exports
are not byte-reproducible across torch versions, so exporting your own is not a workaround.

:::note
`models fetch` arrives with the release that publishes the export. On a build that predates it
there is no other source for the encoder, and preparation stops at the heads stage.
:::

## 3. Serve the reader

Any OpenAI-compatible `/chat/completions` endpoint that takes images, honours
`response_format: json_schema` and has **at least a 32k-token context**. Every request is bounded
before it is sent: episode reads at 24,000 characters and 90 assets a page, story synthesis at
32,000, and the period account (the call that weighs the whole period against itself) at 96,000
characters, split into leaf pages and merged when one page cannot hold it. So ~35 kB bodies are
normal and ~96 kB is the ceiling.

The graded configuration (the one whose output has actually been approved) is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on [oMLX](https://github.com/jundot/omlx), Apple
Silicon:

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install jundot/omlx/omlx
omlx start        # serves on port 8000
```

Pull the model from the dashboard at `http://localhost:8000/admin/chat`. Anything else is
[expected to work, ungraded](#what-has-actually-been-tested).

## 4. Serve the captions

The caption endpoint must advertise the alias **`smolvlm2-500m-base-public`** at `/models`: the
client checks the inventory and three synthetic schema controls before it sends a single library
preview. Serve the accepted weights under that name:

| Artifact | Value |
|---|---|
| Repository | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` |
| Revision | `fa57db46815177fbdfd65cc85a2b3416a8332268` |
| Weights SHA-256 | `a9839c8f79ecc93e54a00dc73cc0e68ba477debcd065d50c1c289fbb1075f981` |

The app does not check that revision or that digest. They record what the banked descriptions
were produced from, so that a future comparison has something to compare against. What it does
enforce is the alias and the schema controls.

It has to accept the compact description/setting JSON schema at temperature zero, repetition
penalty 1.1 and a 140-token output cap. Captions are sent as 400 px JPEG tiles at quality 90.

Those weights are MLX, so this service is Apple Silicon today. Serving the same SmolVLM2
generation under vLLM or llama.cpp is plausible and nobody has compared the descriptions yet; if
you do it, the contract to hit is on
[Editorial annotation setup](./configuration/editorial-preparation.md).

## 5. Point the app at all three

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
  triage:
    encoder: "~/.immich-memories/models/triage/dinov2-small.onnx"
  editorial:
    preparation:
      caption_base_url: "http://localhost:8092/v1"
```

`llm.model` has to be the string the server reports at `GET /v1/models`, not the name you typed
somewhere else. `llm.base_url` defaults to `http://localhost:8080/v1`, which is the app's own
port. Set it.

Every key has an environment variable: `IMMICH_MEMORIES_LLM__BASE_URL`,
`IMMICH_MEMORIES_TRIAGE__ENCODER`,
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL`. Full list on
[Environment variables](./configuration/environment-variables.md).

## 6. Check it before you cut

```bash
immich-memories config test      # credentials and the detected Immich contract
immich-memories preflight        # Immich, the reader, titles, the encoder digest,
                                 # the caption alias, notifications, hardware
```

`preflight` does **not** check the detector snapshots. The first cut does, and it stops with a
count per missing producer rather than quietly cutting a worse film.

## 7. The first cut

Start with one month, not a year:

```bash
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

The cold pass describes every eligible picture once, runs six context heads and two detectors over
it, and reads the month's stories. All of it is banked by producer and exact input, so the second
cut of that month is mostly the render. **Measured on a four-core Celeron NAS: 1.23 s per picture
for every producer except the caption, and 30.9 s for the caption** — which is the whole reason
[`editorial.preparation.tier`](../reference/config-reference.md#preparation-tiers) exists. The one published end-to-end
figure (10 min 08 s for a 14-clip monthly on 4 arm64 cores, `preset: fast`) is from the retired
per-clip scorer and only tells you about the render.

## One machine, or two

| Layout | What it looks like | Verdict |
|---|---|---|
| One Apple Silicon box, 32 GB+ unified memory | App, reader, caption server, render, all local | **Tested.** The only end-to-end configuration anyone has graded |
| App on a NAS or mini-PC + a second box (24 GB GPU, or a Mac) for the reader | The app and the CPU producers are cheap; the reader is not | **Expected to work.** Two machines. On the `no_captions` tier the second box serves the reader only |
| One amd64 box, CPU only, small reader | A 4B-class reader will answer; quality unmeasured | **Expected to work, slowly.** Fine for a first look, not for a verdict on the editor |
| A NAS alone, no second machine, no hosted key | n/a | **Unsupported.** A smaller model would be shipping ungraded output to the tier least able to judge it |

Two things bite people on the split layout: `localhost` inside a container means the container, so
`caption_base_url` and `llm.base_url` need real hostnames; and the shipped Kubernetes
NetworkPolicy opens egress to DNS, 80, 443, 2283, 11434 and 8092 (11434 being Ollama's port, not
oMLX's 8000). Serve the reader anywhere else and you edit
[`deploy/kubernetes/base/networkpolicy.yaml`](./installation/kubernetes.md) first.

**VAAPI, Quick Sync and NVENC are media accelerators.** They decode, scale and encode. They do not
run inference, and no amount of them removes the reader.

## What has actually been tested

| Seat | Configuration | Status |
|---|---|---|
| Reader | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX, Apple Silicon | **Tested**: the graded matrix ran on this |
| Reader | Qwen3.6-27B / Qwen3.6-35B-A3B on Ollama or vLLM | **Untested on this route.** Exercised against the retired per-clip scorer only |
| Reader | Any other OpenAI-compatible vision model, ≥32k context, strict JSON | **Expected to work.** Quality unknown |
| Reader | Text-only models | **Unsupported.** The picture pass posts images to this endpoint |
| Captions | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx@fa57db46` | **Accepted**: the digest the banked descriptions came from |
| Captions | The same generation served by vLLM or llama.cpp | **Untested** |
| Encoder | The pinned DINOv2-small ONNX export | **Required, exact** |
| Detectors | The pinned sensitive-content ONNX export (`det-v2`) + Docling at its pinned revision | **Tested.** ONNX Runtime on the CPU provider, both |

## When it stops

| What you see | What it means |
|---|---|
| `editorial runtime needs a nonblank LLM model` | `llm.model` is empty. Step 5 |
| `caption endpoint must advertise smolvlm2-500m-base-public` | The caption server serves the right weights under the wrong name. Alias it |
| `caption endpoint failed the compact-v3 schema control` | It does not honour the JSON schema, or it is the wrong model |
| `public heads need the pinned DINOv2 ONNX export at …` | Step 2, or `triage.encoder` points at the wrong path |
| `nsfw_marqo has no model: …` or `doc_docling has no model: …` | A missing pinned export or a cold Hugging Face cache. Run `models fetch`; the message names the model and the fix |
| `Story-first selection needs prepared annotations at …` | The annotation store moved. Point `editorial.annotation_database` at it |

## Next

- [Your first memory](../create/first-memory.mdx): the same thing through the web UI
- [Editorial annotation setup](./configuration/editorial-preparation.md): every pin, digest and
  contract in full
- [CPU-only mode](./hardware/cpu-only.md): why the title screens, not the encoder, decide your
  render time
