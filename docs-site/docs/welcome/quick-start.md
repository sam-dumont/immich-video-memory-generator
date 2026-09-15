---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Install the app, point it at Immich, cut one month. Step 2 decides how much else you stand up: the
rules reader on `tier: metadata_only` needs nothing but the app and cuts the ten standard memory
types, while everything richer wants model files on disk and one or two model services you host.
[Running modes](../deploy/running-modes.md) has what each choice costs and loses, and the
[self-hosting guide](../deploy/self-hosting.md) stands the pieces up in order.

## 1. Install

No clone needed:

```bash
uv tool install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
immich-memories --help
```

Take the extra unless you already know you want `tier: metadata_only`, which runs no models at
all: on every other tier the context heads and the detectors have no runtime without it, and the
first cut stops. Or clone and install:

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync --extra editorial
```

See [Installation](../deploy/installation/uv-pip.md) for pip, Docker, and Kubernetes options.

## 2. Configure

Set your Immich connection. Either environment variables:

```bash
export IMMICH_URL=https://photos.example.com
export IMMICH_API_KEY=your-api-key-here
```

Or create `~/.immich-memories/config.yaml`:

```yaml
immich:
  url: https://photos.example.com
  api_key: your-api-key-here
```

Get your API key from Immich: **Account Settings > API Keys > New API Key**, and pick **All** when it asks for permissions. A minimal key needs read on assets, people, albums, timeline and search, plus **asset upload**, **album create/update** and **asset delete** if you turn on upload-back. Your originals are never touched; the delete right covers one narrow case, where a re-render of the same memory trashes the copy it replaces in its own album.

Then pick the mode:

```yaml
advanced:
  editorial:
    reader: rules          # rules | model | auto
    preparation:
      tier: metadata_only  # metadata_only | no_captions | full
```

Those two values need nothing but the app: no vision reader, no caption server, nothing to fetch.
It is the degraded mode, so custom free-text subjects are refused and the cut is simpler, but the
ten standard memory types all come out. Each step up costs a piece: `tier: no_captions` wants
three model files on the app's disk, `tier: full` adds a caption server, `reader: model` adds a
vision reader.

Set neither key and you get `reader: auto`, which is the rules reader while `llm.model` is blank,
on `tier: full`, which does want that caption server. The shipped `docker-compose.yml` pins
`no_captions` instead, so the Docker path needs no second service; it only wants
`immich-memories models fetch` run once inside the container. A cut with a configured piece
missing stops and says which.

## 3. Fetch and check

Skip the first line on `tier: metadata_only`, which downloads nothing.

```bash
immich-memories models fetch   # the pinned encoder and both detectors, about 500 MB, once
immich-memories preflight      # Immich, the reader, the model digests, the caption alias, hardware
```

Preflight follows the reader and tier you set in step 2: rules skips the reader row, `no_captions`
skips the caption alias, `metadata_only` skips the model files. Every row that fails names what
to fix. In Docker, put `docker compose exec immich-memories` in front of both.

## 4. Launch

**Web UI** (recommended for first run):

```bash
immich-memories ui
# Opens at http://localhost:8080
```

The page that opens is the brief: pick a memory type, leave the duration on Auto, press **Cut**.
When the story appears, press **Export**.

**CLI** (for scripts and automation):

```bash
# One month, the type's own one-minute default
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6

# Or just a full year
immich-memories generate --year 2024
```

That's it. Everything after the cut (title, music, output settings) has a sane default and a
page to change it on.
