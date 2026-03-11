---
sidebar_position: 5
title: AMD VAAPI
---

# AMD VAAPI

VAAPI (Video Acceleration API) provides hardware-accelerated video encoding on AMD GPUs under Linux. If you have a Radeon GPU and you're on Linux, this is your backend.

## What you get

- **VAAPI encoding** — h264_vaapi, hevc_vaapi. Hardware-accelerated encoding.
- **VAAPI scaling** — `scale_vaapi` resizes frames on the GPU.
- **Face detection** — falls back to CPU (OpenCV Haar cascades). AMD doesn't expose a GPU-accelerated face detection path.

## Requirements

- AMD GPU with VAAPI support
- Linux (VAAPI is Linux-only)
- Mesa VA drivers installed (`mesa-va-drivers` on Debian/Ubuntu, `libva-mesa-driver` on Arch)
- FFmpeg built with VAAPI support

Check availability:

```bash
immich-memories hardware
```

You can also verify VAAPI is working at the system level:

```bash
vainfo
```

This should list the available VA profiles and entrypoints for your GPU.

## Configuration

```yaml
hardware:
  enabled: true
  backend: "vaapi"   # or "auto"
```

## Limitations

- **Linux only** — VAAPI isn't available on macOS or Windows
- **No GPU face detection** — face-aware cropping uses CPU OpenCV, which is slower but still functional
- Encoding quality varies by GPU generation. Newer RDNA chips produce better output than older GCN cards at the same bitrate.
