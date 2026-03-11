---
sidebar_position: 1
title: Your First Video
---

# Your First Video

This assumes you have Immich running and some videos in your library. If not, go set that up first — this tool is useless without it.

## 1. Install

```bash
uvx immich-memories --help
```

If that prints the help text, you're good. See [Installation](../installation/uv.md) for other methods.

## 2. Configure

Create `~/.immich-memories/config.yaml`:

```yaml
immich:
  url: https://photos.example.com
  api_key: your-api-key-here
```

Get your API key from Immich: **Account Settings > API Keys > New API Key**.

## 3. Launch the UI

```bash
immich-memories ui
```

Opens at `http://localhost:8080`.

## 4. Step 1 — Configuration

- Your Immich connection should already be filled in from the config file.
- Pick a person from the dropdown (or skip it to use all videos).
- Choose a time period. Start with a single year — it keeps the first run fast.

## 5. Step 2 — Clip Review

The tool downloads and analyzes your videos. This takes a while on the first run (roughly 1-2 minutes per video on CPU, much faster with GPU).

Once analysis finishes, you'll see a grid of detected scenes. The best clips are pre-selected. Deselect anything you don't want.

## 6. Step 3 — Generation Options

For your first video, keep it simple:

- **Orientation**: Landscape
- **Resolution**: 1080p
- **Music**: None (unless you've already set up a music backend)

## 7. Step 4 — Generate

Click generate. Wait. Download your video when it's done.

The whole process for a 5-minute memory video from ~50 source videos takes roughly 10-15 minutes on a modern machine. Subsequent runs are faster because analysis results are cached.
