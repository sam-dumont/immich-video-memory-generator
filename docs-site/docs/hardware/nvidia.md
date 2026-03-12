---
sidebar_position: 2
title: NVIDIA
---

# NVIDIA

NVIDIA GPUs with NVENC provide hardware-accelerated video encoding that's significantly faster than software encoding. If you have a GTX 1050 or newer, you've got NVENC.

## What you get

- **NVENC encoding**: h264_nvenc, hevc_nvenc. Offloads encoding to dedicated hardware on the GPU.
- **NVDEC decoding**: hardware-accelerated decode, keeps the full pipeline on GPU.
- **CUDA scaling**: `scale_cuda` resizes frames on the GPU instead of pulling them back to CPU.
- **OpenCV CUDA face detection**: face-aware cropping runs on the GPU. Faster than CPU, though not as fast as Apple's Neural Engine.

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
  backend: "nvidia"   # or "auto" — auto will find it
```

You usually don't need to set `backend: "nvidia"` explicitly. `auto` detects NVIDIA GPUs fine. The only reason to force it is if you have multiple acceleration options and want to ensure NVIDIA is used.

## Encoding quality

NVENC quality is slightly below software libx264 at the same bitrate, but for memory videos the difference is invisible. The speed gain (5-10x) is worth it. If you're encoding a 2-minute compilation, NVENC finishes in seconds instead of minutes.
