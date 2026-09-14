---
sidebar_position: 5
title: AMD VAAPI
---

# AMD VAAPI

VAAPI (Video Acceleration API) provides hardware-accelerated video encoding on AMD GPUs under Linux. Support depends on the GPU, driver and codec.

## What you get

- **VAAPI encoding**: h264_vaapi, hevc_vaapi. Hardware-accelerated encoding.
- **VAAPI scaling**: `scale_vaapi` resizes frames on the GPU.

## Requirements

- AMD GPU with VAAPI support
- Linux (VAAPI is Linux-only)
- Mesa VA drivers installed (`mesa-va-drivers` on Debian/Ubuntu, `libva-mesa-driver` on Arch)
- FFmpeg built with VAAPI support

The amd64 Docker image includes Mesa VA-API drivers. Device access still needs the mount and
permissions below.

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
- Encoding support and quality vary by GPU and driver; test representative source media.
- With `codec_policy: prefer_hardware`, SDR output can switch to an available hardware codec.
  `strict` keeps the requested codec and falls back to software if needed. `vainfo` lists profiles.

## Quality

VAAPI uses `-rc_mode CQP -qp` with the calibrated quality mapping: reference CRF 18 gives
QP 20, and CRF 24 gives QP 22, interpolated between those points. It is not a fixed offset.
The recorded 2.2x bitrate comparison came from an Intel J4125 using VAAPI, not an AMD GPU;
measure your card before treating it as a size estimate. See
[the measured table](./overview.md#quality-one-dial-calibrated-per-encoder).

## Title rendering

Title rendering uses Quadrants on a supported GPU or CPU backend, with PIL as the fallback.
See [Title kernels](./cpu-only.md#title-kernels) for platform support and limitations.
