---
sidebar_position: 5
title: AMD VAAPI
---

# AMD VAAPI

VAAPI (Video Acceleration API) provides hardware-accelerated video encoding on AMD GPUs under Linux. If you have a Radeon GPU and you're on Linux, this is your backend.

## What you get

- **VAAPI encoding**: h264_vaapi, hevc_vaapi. Hardware-accelerated encoding.
- **VAAPI scaling**: `scale_vaapi` resizes frames on the GPU.
- **Face detection**: falls back to CPU (OpenCV YuNet). AMD doesn't expose a GPU-accelerated face detection path.

## Requirements

- AMD GPU with VAAPI support
- Linux (VAAPI is Linux-only)
- Mesa VA drivers installed (`mesa-va-drivers` on Debian/Ubuntu, `libva-mesa-driver` on Arch)
- FFmpeg built with VAAPI support

**In the Docker image the drivers are already installed** (amd64 only), from `0.77.1` on. Images
older than that shipped FFmpeg with VAAPI compiled in and no VA-API driver at all, so
`vaInitialize` failed with `-542398533` and every run silently encoded in software. If you are on
one, upgrade.

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
  encoder_preset: "balanced"   # fast | balanced | quality
```

VAAPI is the last backend probed, so it is used when nothing else (NVIDIA, Apple, QSV) is
available. There is no explicit selector.

## In Docker

Pass the render device through **and** add the group that owns it. The container runs as uid 1000,
and `/dev/dri/renderD128` is typically `root:render` with `crw-rw----`, so without `group_add` the
device is visible and unopenable. Group names are resolved inside the container, where `render`
does not exist, so use the host's numeric GID:

```bash
stat -c '%g' /dev/dri/renderD128     # 104 on Debian/Ubuntu, 937 on Synology DSM
```

```yaml
services:
  immich-memories:
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "104"        # the GID printed above, as a quoted string
```

Then confirm the driver loaded:

```bash
docker compose exec immich-memories vainfo
```

## Limitations

- **Linux only**: VAAPI isn't available on macOS or Windows
- **No GPU face detection**: face-aware cropping uses CPU OpenCV, which is slower but still functional
- Encoding quality varies by GPU generation. Newer RDNA chips produce better output than older GCN cards at the same bitrate.
- A backend that encodes H.264 but not HEVC falls back to libx265 for the H.265 half only, and
  says so in the log. `vainfo` tells you which profiles have an encode entrypoint.

## Quality

VAAPI takes the configured CRF as `-rc_mode CQP -qp`. It is not a fixed offset: two measured
anchors are pinned per encoder family and the dial interpolates between them. VAAPI's pair is
QP 20 at reference CRF 18 and QP 22 at CRF 24 (`processing/rate_control.py`). `-qp` on its own is
ignored unless CQP is selected, which is why this needs both flags. Before 0.77.1 neither was
emitted and the driver's default decided quality.

Reaching software quality costs roughly 2.2x the bits, see
[the measured table](./overview.md#quality-one-dial-calibrated-per-encoder). That sweep was taken
through the iHD driver on an Intel J4125, not on an AMD card: the flags and the interpolation are
the same on Mesa, the bit cost on your GPU is not something anyone here has measured.

## Title rendering

GPU title rendering runs on Quadrants, which has wheels for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 on Python 3.11-3.13. On macOS x86_64 and on Python 3.14 there is none, and title screens fall back to the PIL renderer, which still animates its gradient but loses the kernel effects (bokeh particles, the slow-motion deblur of a content-backed card) and the SDF text path; `immich-memories preflight` says which you will get. See [Title kernels](./cpu-only.md#title-kernels).
