---
sidebar_position: 3
title: Apple Silicon
---

# Apple Silicon

Apple Silicon Macs (M1, M2, M3, M4, M5) are probably the best platform for this tool: video encoding, GPU title rendering and local model inference all accelerate, and unified memory means the reader's 17 GB of weights and the render share one pool instead of copying between two.

## What you get

- **VideoToolbox encoding**: uses the dedicated media engine on the chip instead of the CPU cores. Measured on an M5 Max, a 55-second monthly film rendered in 81 s, against 362 s for the same length on a Kubernetes pod encoding in software and 1,483 s on a four-core Celeron NAS.
- **Unified memory**: no CPU/GPU transfer overhead. Frames stay in the same memory pool whether the CPU, GPU, or Neural Engine is working on them.
- **A place to put the editor's models**: the graded reader and the caption server both run here,
  on Metal, which is why this is the only single-machine layout anyone has run end to end. The
  graded stack is oMLX serving `Qwen3-VL-30B-A3B-Instruct-4bit`; mlx-vlm is the other MLX server
  people use, and its Qwen support may not reach that model. No API costs, no data leaving your
  machine. Standing both up is [step 3 and step 4 of the self-hosting
  guide](../self-hosting.md#3-serve-the-reader), and this page does not replace it.

## Installation

```bash
uv tool install "immich-memories[all-mac]"
```

`all-mac` is the one that can actually cut: it brings the inference dependencies the six context
heads and the two detectors need, on top of everything below. The `mac` extra on its own is the
pyobjc bindings (Quartz, Metal, Vision) and nothing else, so a `mac`-only install stops at the
heads stage on the first cut.

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

The media engine and Neural Engine get faster with each generation, and a base M1 renders
comfortably. The models are the constraint, not the chip: the reader's weights alone are around
17 GB resident, so a single-machine Mac wants 32 GB. An 8 or 16 GB M1 is an app host that needs a
second box for the models.

## Title rendering

GPU title rendering runs on Quadrants, which has wheels for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 on Python 3.11-3.13. On macOS x86_64 and on Python 3.14 there is none, and title screens fall back to the PIL renderer, which still animates its gradient but loses the kernel effects (bokeh particles, the slow-motion deblur of a content-backed card) and the SDF text path; `immich-memories preflight` says which you will get. See [Title kernels](./cpu-only.md#title-kernels). Apple Silicon is one of the four platforms with a wheel; an Intel Mac is the one that is not.
