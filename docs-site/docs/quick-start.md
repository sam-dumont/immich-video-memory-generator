---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Three steps. Should take about 2 minutes.

## 1. Install

The fastest way: no clone needed:

```bash
uvx immich-memories --help
```

Or clone and install:

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync
```

See [Installation](./installation/uv.md) for pip, Docker, and Kubernetes options.

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

Get your API key from Immich: **Account Settings > API Keys > New API Key**.

## 3. Launch

**Web UI** (recommended for first run):

```bash
immich-memories ui
# Opens at http://localhost:8080
```

**CLI** (for scripts and automation):

```bash
immich-memories generate --period "last 30 days" --duration 5
```

That's it. The UI will walk you through the rest: picking people, time ranges, music, and output settings.
