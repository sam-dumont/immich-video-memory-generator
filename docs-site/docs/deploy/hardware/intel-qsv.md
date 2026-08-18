---
sidebar_position: 4
title: Intel Quick Sync
---

# Intel Quick Sync

Intel Quick Sync Video (QSV) is built into most Intel CPUs with integrated graphics (6th gen Skylake and newer). If you're running on an Intel NUC, a mini PC, or a server with an Intel CPU, you probably have it.

## What you get

- **QSV encoding**: h264_qsv, hevc_qsv. Hardware-accelerated encoding on the integrated GPU.
- **QSV scaling**: `scale_qsv` resizes frames on the GPU.
- **Face detection**: falls back to CPU (OpenCV Haar cascades). Intel GPUs don't have a dedicated neural accelerator exposed for this.

## Requirements

- Intel CPU with integrated graphics (6th gen+)
- Intel media drivers installed
- FFmpeg built with QSV support

On Linux, you'll need the `intel-media-va-driver` (or `intel-media-va-driver-non-free` for newer chips) and `libmfx` or `libvpl`.

Check availability:

```bash
immich-memories hardware
```

## Configuration

```yaml
hardware:
  enabled: true
  encoder_preset: "balanced"   # fast | balanced | quality
```

QSV is picked automatically when no NVIDIA GPU is present and the QSV encoders work.

## In Docker

Pass the render device through and make sure the container user can open it (it is usually owned
by the `render` group):

```yaml
services:
  immich-memories:
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "video"
      - "render"     # use the numeric GID from `getent group render` if the name is not resolvable
```

## Good for headless servers

QSV is common in home server setups: Intel NUCs, older desktops repurposed as media servers. The encoding speed bump is nice (3-5x over software), and since it uses the integrated GPU, it doesn't require a discrete graphics card.
