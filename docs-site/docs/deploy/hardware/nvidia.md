---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC provide hardware-accelerated video encoding that's 5-10x faster than software encoding. If you have a GTX 1050 or newer, you've got NVENC. The encode is not the phase that dominates a run, though — see [Encoding quality](#encoding-quality) for what the card actually buys you.

## What you get

- **NVENC encoding**: h264_nvenc, hevc_nvenc. Offloads encoding to dedicated hardware on the GPU.
- **NVDEC decoding**: hardware-accelerated decode, keeps the full pipeline on GPU.
- **CUDA scaling**: `scale_cuda` resizes frames on the GPU instead of pulling them back to CPU.
- **CUDA scene analysis**: when OpenCV has CUDA support and `hardware.gpu_analysis` is on, frame differencing for scene detection runs on the GPU. Face detection stays on the CPU (OpenCV Haar cascades) — there is no CUDA face path.
- **Taichi title rendering**: with the `gpu` extra installed, Taichi picks the CUDA backend (Vulkan second) for animated title screens. This is the phase that costs the most on a CPU-only box.

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

NVENC quality is slightly below software libx264 at the same bitrate, but for memory videos the difference is invisible, so take the speed.

Just don't buy the card for the encode. Encoding is the smaller half of a CPU-only run: title rendering was ~263 s of a ~339 s assembly at `--cpus=2`, and analysis was 7.4 of 10.1 minutes end to end. The bigger wins from this GPU are Taichi title rendering and CUDA scene analysis. See [CPU-Only Mode](./cpu-only.md#title-rendering-is-the-bottleneck-not-encoding) for the measured split.
