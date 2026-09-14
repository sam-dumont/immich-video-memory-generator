---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC move video encoding off the CPU and onto dedicated silicon. Support depends on the GPU, codec, driver and FFmpeg build. Run the hardware check below before relying on it.

## What you get

- **NVENC encoding**: h264_nvenc, hevc_nvenc. Offloads encoding to dedicated hardware on the GPU.
- **NVDEC decoding**: hardware-accelerated decode; CPU filters can still require frame transfers.
- **CUDA scaling**: `scale_cuda` resizes frames on the GPU instead of pulling them back to CPU.
- **GPU title rendering**: the title kernels pick the CUDA backend (Vulkan second) for animated title screens. Title cost depends on duration and resolution.

The app image uses CPU ONNX Runtime and CPU PyTorch. Installing the NVIDIA runtime does not
turn those libraries into CUDA builds. A native CUDA preparation environment uses
`immich-memories[editorial-cuda]` instead of `editorial` for the DINOv2 encoder; both detectors
still select the CPU provider.

GPU inference also has a separate [inference service image](../installation/inference-service.md). Its `-cuda` variant uses the same device extra; attach the GPU with the device reservation that ships commented out on the inference service in `docker-compose.yml`. The app image remains usable for NVENC without running model inference.

## Requirements

- NVIDIA GPU with an NVENC encoder for the requested codec
- A compatible NVIDIA driver
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
```

Nothing to select: NVIDIA is probed first, so if NVENC works it is used. On a multi-GPU host pick
the card with `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES`: there is no `device_index` in
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

Without `video` the encode library is absent and nothing on the pod explains why: the encoders
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

Matching libx264 costs about **1.2x the bits**, the cheapest of the three hardware backends, next
to Intel's 2.2x and Apple's 2.9x. The configured CRF is translated onto NVENC's quantiser scale
automatically; see [the overview](./overview.md#quality-one-dial-calibrated-per-encoder).

These measurements cover one encoder and clip. They do not predict whole-run speed; preparation,
reader calls, titles and encoding have different costs. See [Running modes](../running-modes.md).

The `editorial-cuda` extra pins ONNX Runtime GPU to the 1.26 series for CUDA 12 and cuDNN 9. Version 1.27 and newer require CUDA 13; see the [official compatibility table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html). Do not install the CPU `editorial` extra beside it.

## Title rendering

Title rendering uses Quadrants on a supported GPU or CPU backend, with PIL as the fallback.
See [Title kernels](./cpu-only.md#title-kernels) for platform support and limitations.
