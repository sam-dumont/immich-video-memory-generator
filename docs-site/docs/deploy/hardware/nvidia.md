---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC provide hardware-accelerated video encoding that's 5-10x faster than software encoding. If you have a GTX 1050 or newer, you've got NVENC.

## What you get

- **NVENC encoding**: h264_nvenc, hevc_nvenc. Offloads encoding to dedicated hardware on the GPU.
- **NVDEC decoding**: hardware-accelerated decode, keeps the full pipeline on GPU.
- **CUDA scaling**: `scale_cuda` resizes frames on the GPU instead of pulling them back to CPU.
- **CUDA scene analysis**: when OpenCV has CUDA support and `hardware.gpu_analysis` is on, frame differencing for scene detection runs on the GPU. Face detection stays on the CPU (OpenCV Haar cascades) — there is no CUDA face path.

## Requirements

- NVIDIA GPU (GTX 1050+ / any RTX)
- CUDA drivers installed
- FFmpeg built with NVENC support (most distro packages include this)

Check if everything's working:

```bash
immich-memories hardware
```

If NVENC is available, you'll see it listed with the specific encoders found.

## Configuration

```yaml
hardware:
  enabled: true
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true
  gpu_analysis: true
```

Nothing to select: NVIDIA is probed first, so if NVENC works it is used. On a multi-GPU host pick
the card with `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES` — there is no `device_index` in
the config.

## In Docker

The image does not bundle drivers; the NVIDIA Container Toolkit injects them. NVENC needs the
`video` capability on top of the default `compute,utility`:

```yaml
services:
  immich-memories:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu, video]
    environment:
      NVIDIA_DRIVER_CAPABILITIES: compute,video,utility
```

Then `docker compose exec immich-memories immich-memories hardware` should list `h264_nvenc`.
See [Linux + NVIDIA](../common-setups/linux-nvidia.md) for a full compose file.

## Encoding quality

NVENC quality is slightly below software libx264 at the same bitrate, but for memory videos the difference is invisible. The speed gain (5-10x) is worth it. If you're encoding a 2-minute compilation, NVENC finishes in seconds instead of minutes.
