---
sidebar_position: 3
title: Apple Silicon
---

# Apple Silicon

An M-series Mac is the best platform for this tool. VideoToolbox takes the encode, the title
kernels run on Metal, face detection runs on the Neural Engine through the Vision framework, and
unified memory means the reader's 17 GB of weights and the render share one pool instead of copying
between two.

The models are why it matters. The graded reader and the caption server both run here, on Metal,
which is what makes this the only single-machine layout anyone has run end to end. The graded stack
is oMLX serving `Qwen3-VL-30B-A3B-Instruct-4bit`; mlx-vlm is the other MLX server people use, and
its Qwen support may not reach that model. No API costs, no data leaving your machine. Standing both
up is [step 3 and step 4 of the self-hosting guide](../self-hosting.md#3-serve-the-reader), and this
page does not replace it.

## Installation

```bash
uv tool install "immich-memories[all-mac]"
```

`all-mac` is the one that can actually cut: it brings the inference dependencies the six context
heads and the two detectors need, on top of everything below. The `mac` extra on its own is the
pyobjc bindings (Quartz, Metal, Vision) and nothing else, so a `mac`-only install stops at the
heads stage on the first cut.

## Which Mac

Every Apple Silicon chip is supported, M1 to M5 and whatever comes next, and the media engine and
Neural Engine get faster with each generation. A base M1 renders comfortably. The models are the
constraint, not the chip: the reader's weights alone are around 17 GB resident, so a single-machine
Mac wants 32 GB. An 8 or 16 GB M1 is an app host that needs a second box for the models.

## Encoding

Nothing to select: VideoToolbox is found automatically on macOS, and `hardware.encoder_preset: fast`
turns on its speed-priority mode. Measured on an M5 Max, a 55-second monthly film rendered in 81 s,
against 362 s for the same length on a Kubernetes pod encoding in software and 1,483 s on a
four-core Celeron NAS ([Running modes](../running-modes.md#what-to-expect-on-a-first-run)).

## Title rendering

Metal, and Apple Silicon is one of the four platforms Quadrants publishes a wheel for. An Intel Mac
is the one that is not: [Title kernels](./cpu-only.md#title-kernels).
