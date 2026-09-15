---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVENC moves the encode onto dedicated silicon on the card: `h264_nvenc` and `hevc_nvenc`, NVDEC on
the way in, `scale_cuda` for the resizes, and the title kernels on the CUDA backend. A GTX 1050 or
newer has NVENC; you need the CUDA drivers and an FFmpeg built with NVENC support, which most
distro packages include. NVIDIA is probed first, so if NVENC opens, it is used.

The encode is not the phase that dominates a run. It is worth about 15 % of the render, against a
download phase the card does nothing for: [what the card is actually
worth](./overview.md#what-the-card-is-actually-worth).

## The image does not do CUDA inference

The Docker image installs the CPU build of PyTorch on purpose, on both published architectures. The
two annotation detectors are ONNX graphs and want no torch at all (the only thing left in the image
that does is local Demucs stem separation), so the CUDA wheels are pure weight: on arm64 they cost
3.3 GB of `nvidia` libraries plus 818 MB of triton, and the CUDA torch they come with still reports
`cuda_available: False` inside the container.

To run the ONNX seats against the CUDA execution provider, install
`pip install "immich-memories[editorial-cuda]"` on the host instead of using the image. It replaces
`editorial` rather than joining it, so do not install the CPU extra beside it, and it pins ONNX
Runtime GPU to the 1.26 series for CUDA 12 and cuDNN 9 (1.27 and newer require CUDA 13, see the
[official compatibility table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)).

GPU inference also has a separate [inference service image](../installation/inference-service.md).
Its `-cuda` variant uses the same device extra; attach the GPU with the device reservation that
ships commented out on the inference service in `docker-compose.yml`. The app image remains usable
for NVENC without running model inference.

## Configuration

Nothing to select. On a multi-GPU host pick the card with `CUDA_VISIBLE_DEVICES` /
`NVIDIA_VISIBLE_DEVICES`: there is no `device_index` in the config.

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
`compute,utility` and nothing else. `immich-memories preflight` says the same thing on its Hardware
row, before you spend a render finding out.

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

Just don't buy the card for the encode alone. Preparation moves further than the render does: put
the ONNX encoder, the six heads and both detectors behind the
[inference service](../installation/inference-service.md) on CUDA and the same cluster pod went
from 0.6083 s a picture to 0.1957 s on the fixture month. What the card will not run is the
editor's reader, which lives on its own box or its own server: see the
[self-hosting guide](../self-hosting.md#one-machine-or-two).

## Title rendering

CUDA, Vulkan second. What happens on a machine with neither is on
[Title kernels](./cpu-only.md#title-kernels).
