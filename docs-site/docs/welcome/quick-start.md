---
sidebar_position: 2
title: Quick Start
---

# Quick Start

The app installs in a couple of minutes. How much longer the rest takes is a choice you make in
step 2. The rules reader on the `metadata_only` tier needs nothing but the app and cuts the ten
standard memory types. The model editor needs two model services on hardware you own plus three
model files on disk, and that is the long road: [Running modes](../deploy/running-modes.md) has
what each choice costs and loses, and the [self-hosting guide](../deploy/self-hosting.md) is the
honest version of the long one, in order, on one page.

## 1. Install

The fastest way: no clone needed:

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

Get your API key from Immich: **Account Settings > API Keys > New API Key**. When Immich asks which permissions to grant, pick **All**. For a minimal key: read access to assets, people, albums, timeline and search, plus **asset upload**, **album create/update** and **asset delete** if you turn on upload-back to Immich. Your originals are never touched; the delete permission is for one narrow case, where a re-render of the same memory trashes the copy it replaces in its own album.

Then pick the mode, because that is what decides whether you stand anything else up at all:

```yaml
advanced:
  editorial:
    reader: rules          # rules | model | auto
    preparation:
      tier: metadata_only  # metadata_only | no_captions | full
```

Those two values need nothing but the app: no vision reader, no caption server, nothing to fetch.
It is the degraded mode, so custom free-text subjects are refused and the cut is simpler, but the
ten standard memory types all come out. Set neither key and you get `reader: auto`, which is the
rules reader while `llm.model` is blank, on `tier: full`, which does want a caption server. The
shipped `docker-compose.yml` pins `no_captions` instead, so the Docker path already needs no second
service; it only wants `immich-memories models fetch` run once inside the container.

Each step up costs a piece: `tier: no_captions` wants three model files on the app's disk (the
pinned ONNX encoder, the pinned sensitive-content ONNX export and the document classifier's
snapshot, all fetched by `immich-memories models fetch`), `tier: full` adds a caption server, and
`reader: model` adds a vision reader. The [self-hosting guide](../deploy/self-hosting.md) walks all
of it in order. A cut with a configured piece missing stops and says which.

## 3. Launch

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
