---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC move video encoding off the CPU and onto dedicated silicon. If you have a GTX 1050 or newer, you've got NVENC. The encode is not the phase that dominates a run, though: see [Encoding quality](#encoding-quality) for what the card actually buys you.

## What you get

- **NVENC encoding**: h264_nvenc, hevc_nvenc. Offloads encoding to dedicated hardware on the GPU.
- **NVDEC decoding**: hardware-accelerated decode, keeps the full pipeline on GPU.
- **CUDA scaling**: `scale_cuda` resizes frames on the GPU instead of pulling them back to CPU.
- **GPU title rendering**: the title kernels pick the CUDA backend (Vulkan second) for animated title screens. This is the phase that costs the most on a CPU-only box.

What the card does *not* get you: the Docker image installs the CPU build of PyTorch on purpose, on both published architectures. The two annotation detectors are ONNX graphs and want no torch at all (the only thing left in the image that does is local Demucs stem separation) so the CUDA wheels are pure weight: on arm64 they cost 3.3 GB of `nvidia` libraries plus 818 MB of triton, and the CUDA torch they come with still reports `cuda_available: False` inside the container. To run the ONNX seats against the CUDA execution provider, install `pip install "immich-memories[editorial-cuda]"` on the host instead of using the image; it replaces `editorial` rather than joining it.

GPU inference also has a separate [inference service image](../installation/inference-service.md). Its `-cuda` variant uses the same device extra; attach the GPU with the device reservation that ships commented out on the inference service in `docker-compose.yml`. The app image remains usable for NVENC without running model inference.

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
```

Nothing to select: NVIDIA is probed first, so if NVENC works it is used. On a multi-GPU host pick
the card with `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES`: there is no `device_index` in
the config.

`hardware.backend: nvidia` probes NVENC and nothing else. Leave it on `auto` for normal use. It is
there for a benchmark: detection treats software as no backend at all, so a box missing the `video`
driver capability would encode on the CPU and still look like a GPU run. Named, the miss is a
warning in the log and the timing can be thrown out rather than believed. The
[setup matrix](../../contribute/setup-matrix.md) pins it in the two cluster cells that render on a
named card.

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

## When the probe fails with -22

This one reads like a bad FFmpeg flag and is not one:

```
Hardware encoder probe failed for ['-c:v', 'h264_nvenc']: Terminating thread with return code -22 (Invalid argument)
No hardware acceleration detected, using software encoding
```

`-22` here means the container got the GPU for compute and not for video. Everything that is not
encoding keeps working, which is what makes it confusing: `nvidia-smi` lists the card, the title
kernels log `on the CUDA backend`, and only NVENC is missing.

Set `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` on the container, plus the `nvidia` runtime
class. In Docker that is the block under [In Docker](#in-docker); in Kubernetes,
`kubectl apply -k overlays/gpu`, which sets both. The usual way to hit this is a pod that never got
the overlay: a hand-written Job dropped onto a shared GPU node inherits the node's default
`compute,utility` and nothing else.

`immich-memories preflight` says the same thing on its Hardware row, before you spend a render
finding out.

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

Just don't buy the card for the encode alone. Encoding is the larger half of a CPU-only assembly again: title rendering was ~263 s of a ~339 s assembly at `--cpus=2` until the blur fix cut a 1080p title frame from 578 ms to 64 ms. An NVIDIA card takes work off both halves. It does not run the editor's models: see the [self-hosting guide](../self-hosting.md#one-machine-or-two) for where those go. See [CPU-Only Mode](./cpu-only.md#title-rendering-used-to-be-the-bottleneck) for the measured split.

The `editorial-cuda` extra pins ONNX Runtime GPU to the 1.26 series for CUDA 12 and cuDNN 9. Version 1.27 and newer require CUDA 13; see the [official compatibility table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html). Do not install the CPU `editorial` extra beside it.

## Title rendering

GPU title rendering runs on Quadrants, which has wheels for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 on Python 3.11-3.13. On macOS x86_64 and on Python 3.14 there is none, and title screens fall back to the PIL renderer, which still animates its gradient but loses the kernel effects (bokeh particles, the slow-motion deblur of a content-backed card) and the SDF text path; `immich-memories preflight` says which you will get. See [Title kernels](./cpu-only.md#title-kernels).
