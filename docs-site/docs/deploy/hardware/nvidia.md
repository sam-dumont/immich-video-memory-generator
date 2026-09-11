---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC move video encoding off the CPU and onto dedicated silicon. If you have a GTX 1050 or newer, you've got NVENC. The encode is not the phase that dominates a run, though — see [Encoding quality](#encoding-quality) for what the card actually buys you.

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

## In Kubernetes

The same capability is the thing people miss, because requesting the GPU does not imply it:

```yaml
spec:
  runtimeClassName: nvidia
  containers:
    - name: immich-memories
      env:
        - name: NVIDIA_DRIVER_CAPABILITIES
          value: compute,video,utility
      resources:
        limits:
          nvidia.com/gpu: 1
```

Without `video` the encode library is absent and nothing on the pod explains why — the encoders
still list, detection still looks plausible, and the render falls back to the CPU.

## When the image is newer than the driver

An FFmpeg built against a newer NVENC SDK than the installed driver provides refuses to open the
encoder at render time, not at detection. A third-party build against SDK 13.1 fails on a 570
driver (which provides 13.0) with "The minimum required Nvidia driver for nvenc is 610.00 or
newer". This project's own image works on 570.

The one-frame probe catches this, so the run falls back to software rather than dying, and the log
names the cause. If you see it, either update the driver or use an image built against an older
SDK.

## Encoding quality

Measured on a T1000 (Turing, driver 570.144), 20 s of 1080p60, SSIM against the source:

| encode | bitrate | SSIM |
|---|---|---|
| `libx264 -crf 18` | 7.0 Mbps | 0.99011 |
| `h264_nvenc -rc constqp -qp 20` | 8.5 Mbps | 0.98944 |
| `h264_nvenc -qp 22` | 6.2 Mbps | 0.98731 |
| `h264_nvenc -qp 24` | 4.5 Mbps | 0.98433 |

Matching libx264 costs about **1.2x the bits** — the cheapest of the three hardware backends, next
to Intel's 2.2x and Apple's 2.9x. The configured CRF is translated onto NVENC's quantiser scale
automatically; see [the overview](./overview.md#what-hardware-encoding-actually-costs).

Just don't buy the card for the encode. Encoding is the smaller half of a CPU-only run: title rendering was ~263 s of a ~339 s assembly at `--cpus=2`, and analysis was 7.4 of 10.1 minutes end to end. The bigger wins from this GPU are Taichi title rendering and CUDA scene analysis. See [CPU-Only Mode](./cpu-only.md#title-rendering-is-the-bottleneck-not-encoding) for the measured split.
