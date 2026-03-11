---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

Encoding video in software (libx264) works everywhere but it's slow. If you have a GPU or dedicated media engine, hardware acceleration can speed up encoding by 5-10x. The pipeline auto-detects your hardware and picks the best available backend.

## Supported backends

| Backend | Platform | Encode | Decode | GPU Scaling | Face Detection |
|---------|----------|--------|--------|-------------|----------------|
| **NVIDIA NVENC** | Linux, Windows | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | OpenCV CUDA |
| **Apple VideoToolbox** | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | - | Vision Framework (Neural Engine) |
| **Intel QSV** | Linux, Windows | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU fallback |
| **AMD VAAPI** | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU fallback |
| **Software** | Everywhere | libx264, libx265 | FFmpeg | swscale | OpenCV CPU |

## Configuration

```yaml
hardware:
  enabled: true       # turn hardware acceleration on/off
  backend: "auto"     # auto | nvidia | apple | vaapi | qsv | none
```

`auto` is the default and the right choice for most people. It probes for available backends in order of preference and picks the first one that works. Set a specific backend only if auto-detection picks the wrong one (rare) or you want to force CPU encoding for testing.

## Checking your hardware

```bash
immich-memories hardware
```

This prints what backends are available, which one would be selected, and the specific encoders/decoders found. Run this first if you're not sure what you've got.

## Per-backend details

- [NVIDIA](./nvidia.md): NVENC/NVDEC, CUDA scaling and face detection
- [Apple Silicon](./apple-silicon.md): VideoToolbox, Vision Framework, mlx-vlm
- [Intel Quick Sync](./intel-qsv.md): QSV encoding and scaling
- [AMD VAAPI](./amd-vaapi.md): VAAPI encoding and scaling (Linux only)
