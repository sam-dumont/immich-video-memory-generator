---
sidebar_position: 3
title: Apple Silicon
---

# Apple Silicon

Apple Silicon Macs (M1, M2, M3, M4, M5) are probably the best platform for this tool. Everything accelerates: video encoding, face detection, and even local LLM inference. Unified memory means no copying data between CPU and GPU.

## What you get

- **VideoToolbox encoding**: 5-10x faster than software libx264. Uses the dedicated media engine on the chip.
- **Vision Framework face detection**: runs on the Neural Engine, ~10x faster than OpenCV CPU. More accurate too, especially with small or partially occluded faces.
- **Unified memory**: no CPU/GPU transfer overhead. Frames stay in the same memory pool whether the CPU, GPU, or Neural Engine is working on them.
- **mlx-vlm for local LLMs**: run vision models locally with Metal acceleration for [LLM content analysis](../features/llm-analysis.md). No API costs, no data leaving your machine.

## Installation

```bash
uv sync --extra mac
```

The `mac` extra installs the Apple-specific dependencies (pyobjc bindings for Vision Framework, etc.).

## Configuration

```yaml
hardware:
  enabled: true
  backend: "apple"   # or "auto" — auto detects it
```

`auto` works perfectly on macOS. It'll find VideoToolbox and Vision Framework without any manual config.

## Supported chips

All Apple Silicon chips are supported:

- M1, M1 Pro, M1 Max, M1 Ultra
- M2, M2 Pro, M2 Max, M2 Ultra
- M3, M3 Pro, M3 Max, M3 Ultra
- M4, M4 Pro, M4 Max, M4 Ultra
- M5 and newer

The media engine and Neural Engine get faster with each generation, but even a base M1 runs the full pipeline comfortably.
