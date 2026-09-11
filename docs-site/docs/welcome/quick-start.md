---
sidebar_position: 2
title: Quick Start
---

# Quick Start

The app installs in a couple of minutes. Standing up what it reads with takes considerably longer:
two model services on hardware you own, plus two model files on disk. Step 2 below is where that
lives, and the [self-hosting guide](../deploy/self-hosting.md) is the honest version of it, in
order, on one page.

## 1. Install

The fastest way: no clone needed:

```bash
uv tool install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
immich-memories --help
```

The extra is not optional in practice: without it the context heads and the detectors have no
runtime and the first cut stops. Or clone and install:

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

Then stand up what the editor reads with: three services (this app, a vision reader, a caption
server) and two model files on disk (the pinned ONNX encoder and two detector snapshots, both
fetched by `immich-memories models fetch`). The [self-hosting guide](../deploy/self-hosting.md)
walks all of it in order. A cut with one of them missing stops and says which.

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
