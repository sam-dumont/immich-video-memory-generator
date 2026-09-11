---
sidebar_position: 3
title: Apple Silicon
---

# Apple Silicon

Apple Silicon Macs (M1, M2, M3, M4, M5) are probably the best platform for this tool: video encoding, Taichi title rendering and local model inference all accelerate, and unified memory means the reader's 17 GB of weights and the render share one pool instead of copying between two.

## What you get

- **VideoToolbox encoding**: uses the dedicated media engine on the chip instead of the CPU cores.
- **Unified memory**: no CPU/GPU transfer overhead. Frames stay in the same memory pool whether the CPU, GPU, or Neural Engine is working on them.
- **mlx-vlm for local LLMs**: run the editor's model locally with Metal acceleration ([LLM titles and mood](../../create/pipeline/llm-content-analysis.md), [Editorial annotation setup](../configuration/editorial-preparation.md)). No API costs, no data leaving your machine.

## Installation

```bash
uv sync --extra mac
```

The `mac` extra installs the Apple-specific dependencies (pyobjc bindings for Quartz, Metal and Vision).

## Configuration

```yaml
hardware:
  enabled: true
  encoder_preset: "balanced"   # fast turns on VideoToolbox's speed-priority mode
```

Nothing to select: VideoToolbox is found automatically on macOS.

## Supported chips

All Apple Silicon chips are supported:

- M1, M1 Pro, M1 Max, M1 Ultra
- M2, M2 Pro, M2 Max, M2 Ultra
- M3, M3 Pro, M3 Max, M3 Ultra
- M4, M4 Pro, M4 Max, M4 Ultra
- M5 and newer

The media engine and Neural Engine get faster with each generation, but even a base M1 runs the full pipeline comfortably.
