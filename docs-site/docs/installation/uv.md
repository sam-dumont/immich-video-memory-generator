---
sidebar_position: 1
title: uv (Recommended)
---

# Install with uv

[uv](https://docs.astral.sh/uv/) is 10-100x faster than pip. If you're not using it yet, you should be.

## One-Liner (No Install Required)

Run directly without installing anything:

```bash
uvx immich-memories --help
```

`uvx` creates an isolated environment, runs the command, done. Great for trying things out.

## Clone and Install

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync
```

## Platform Extras

Install optional features depending on your setup:

```bash
# macOS — Apple Vision framework for face detection + GPU rendering
uv sync --extra mac

# Face recognition (any platform)
uv sync --extra face

# AI music generation features
uv sync --extra audio

# ML-based audio analysis (laughter/speech detection)
uv sync --extra audio-ml

# GPU-accelerated title rendering (Metal, CUDA, Vulkan)
uv sync --extra gpu

# Everything (cross-platform)
uv sync --extra all

# Everything on macOS
uv sync --extra all-mac
```

## Optional System Dependencies

These are **not required** but improve specific features:

| Tool | What it does | Install |
|------|-------------|---------|
| [exiftool](https://exiftool.org/) | Fallback for HDR headroom extraction from Apple HEIC photos | `brew install exiftool` (macOS) / `apt install libimage-exiftool-perl` (Debian) |

The primary HDR headroom parser is pure Python — exiftool is only called if the built-in parser fails on an unusual HEIC file.

## Install uv

If you don't have uv yet:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via Homebrew
brew install uv
```
