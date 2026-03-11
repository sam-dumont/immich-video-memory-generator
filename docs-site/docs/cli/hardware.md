---
sidebar_position: 5
title: hardware
---

# hardware

Shows what GPU hardware acceleration is available on your system. Useful for figuring out why encoding is slow (or fast).

```bash
immich-memories hardware
```

## What it detects

- GPU device name and VRAM
- H.264 and H.265 encode/decode support
- GPU scaling capability
- OpenCV CUDA availability

## Example output

With a supported GPU:

```
┌─────────────────────────────────────────────┐
│ Hardware Acceleration: NVIDIA               │
├──────────────┬──────────────────────────────┤
│ Feature      │ Status                       │
├──────────────┼──────────────────────────────┤
│ Device       │ NVIDIA GeForce RTX 3080      │
│ VRAM         │ 10240 MB                     │
│ H.264 Encode │ Yes                          │
│ H.265 Encode │ Yes                          │
│ H.264 Decode │ Yes                          │
│ H.265 Decode │ Yes                          │
│ GPU Scaling  │ Yes                          │
│ OpenCV CUDA  │ No                           │
└──────────────┴──────────────────────────────┘
```

Without hardware acceleration:

```
No hardware acceleration detected

Video encoding will use CPU (libx264).

To enable hardware acceleration:
  - NVIDIA: Install CUDA drivers and FFmpeg with NVENC support
  - Apple: Use macOS with VideoToolbox (built-in)
  - Intel: Install oneVPL/QSV drivers
  - AMD/Linux: Install VAAPI drivers
```

## Supported backends

| Backend | Platform | Notes |
|---------|----------|-------|
| NVIDIA (NVENC) | Linux, Windows | Requires CUDA drivers |
| Apple VideoToolbox | macOS | Built-in, works out of the box |
| Intel QSV | Linux, Windows | Requires oneVPL/QSV drivers |
| VAAPI | Linux | AMD and Intel on Linux |

The `hardware` section in your config file controls whether acceleration is used and which backend to prefer. See [Config File](/docs/configuration/config-file) for details.
