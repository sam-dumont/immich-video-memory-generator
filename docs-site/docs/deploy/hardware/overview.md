---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

The pipeline is designed around GPU acceleration for the best quality and speed: animated title screens, fast encoding, and (on Apple Silicon) GPU-accelerated face detection. However, **every feature has a CPU fallback**, so it works on any machine. See [CPU-Only Mode](./cpu-only.md) for details on running without a GPU.

Encoding video in software (libx264) works everywhere but it's slow. If you have a GPU or dedicated media engine, hardware acceleration can speed up encoding by 5-10x. The pipeline auto-detects your hardware and picks the best available backend; NVENC, Quick Sync and VAAPI are only selected after a one-frame test encode succeeds, so an FFmpeg build that merely lists them (Debian's does, including inside the Docker image) doesn't send a GPU-less box down the hardware path.

That 5-10x applies to a phase that is not where a run spends its time. Analysis and title rendering are — measured at `--cpus=2`, title rendering was ~263 s of a ~339 s assembly ([CPU-Only Mode](./cpu-only.md#title-rendering-is-the-bottleneck-not-encoding)), and in a measured end-to-end run analysis was 7.4 of 10.1 minutes ([NAS-Only](../common-setups/nas-only.md#performance-expectations)). Hardware acceleration shortens the last phase; a GPU earns its keep first on titles.

## Supported backends

| Backend | Platform | Encode | Decode | GPU Scaling | Face Detection |
|---------|----------|--------|--------|-------------|----------------|
| **NVIDIA NVENC** | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (OpenCV Haar cascades) |
| **Apple VideoToolbox** | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | - | Vision Framework (Neural Engine) |
| **Intel QSV** | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (OpenCV Haar cascades) |
| **AMD VAAPI** | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (OpenCV Haar cascades) |
| **Software** | Everywhere | libx264, libx265 | FFmpeg | swscale | CPU (OpenCV Haar cascades) |

Face detection runs on the GPU only on Apple Silicon (Vision Framework). Everywhere else it is
OpenCV Haar cascades on the CPU. On NVIDIA, CUDA is also used for scene analysis (frame
differencing) when OpenCV has CUDA support and `hardware.gpu_analysis` is on.

## Configuration

```yaml
hardware:
  enabled: true                # false = software encoding, no GPU probing
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true             # hardware decoding when the backend supports it
  gpu_analysis: true           # CUDA scene analysis on NVIDIA when available
```

The backend is probed automatically in the order NVIDIA → Apple → Intel QSV → VAAPI, and the first
one that works is used. There is no override to pick a specific backend; the only switch is
`hardware.enabled: false`, which forces software encoding (useful for testing or a broken driver).

## Checking your hardware

```bash
immich-memories hardware
```

This prints what backends are available, which one would be selected, and the specific encoders/decoders found. Run this first if you're not sure what you've got.

## Per-backend details

- [NVIDIA](./nvidia.md): NVENC/NVDEC, CUDA scaling and scene analysis
- [Apple Silicon](./apple-silicon.md): VideoToolbox, Vision Framework, mlx-vlm
- [Intel Quick Sync](./intel-qsv.md): QSV encoding and scaling
- [AMD VAAPI](./amd-vaapi.md): VAAPI encoding and scaling (Linux only)
- [CPU-Only Mode](./cpu-only.md): Running without any GPU
