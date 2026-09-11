---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Three steps to a running app. Should take about 2 minutes; the first cut takes longer, because
the editor has to read your pictures before it can cut them.

## 1. Install

The fastest way: no clone needed:

```bash
uvx immich-memories --help
```

Or clone and install:

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

Get your API key from Immich: **Account Settings > API Keys > New API Key**. When Immich asks which permissions to grant, pick **All**. For a minimal key: read access to assets, people, albums, timeline and search, plus **asset upload** and **album create/update** if you turn on upload-back to Immich. This tool never deletes or modifies existing assets.

Then set up the three things the editor reads with (a caption endpoint, the pinned encoder, two
detectors), following the [self-hosting guide](../deploy/self-hosting.md), which walks the whole
thing in order. A cut with one of them missing stops and says which.

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
