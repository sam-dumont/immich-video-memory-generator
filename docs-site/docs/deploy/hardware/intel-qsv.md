---
sidebar_position: 4
title: Intel Quick Sync
---

# Intel Quick Sync

Quick Sync uses the Intel integrated GPU for video encoding. Check the exact CPU, enabled
render device and codec support; an Intel CPU alone does not guarantee an encoder.

## What you get

- **QSV encoding**: h264_qsv, hevc_qsv. Hardware-accelerated encoding on the integrated GPU.
- **QSV scaling**: `scale_qsv` resizes frames on the GPU.
- **Face detection**: not a thing this app does. Photo pans use Immich's own face boxes.

## Requirements

- Intel CPU with integrated graphics (6th gen+)
- Intel media drivers installed
- FFmpeg built with QSV support

On Linux, you'll need the `intel-media-va-driver` (or `intel-media-va-driver-non-free` for newer chips) and `libmfx` or `libvpl`.

The amd64 Docker image includes Intel VA-API drivers. Pass through the render device and its
owning group as shown below.

Check availability:

```bash
immich-memories hardware
```

Inside the container, `vainfo` shows what the driver found; a working Intel setup names the iHD
or i965 driver and lists `VAEntrypointEncSlice` / `VAEntrypointEncSliceLP` profiles:

```bash
docker compose exec immich-memories vainfo
```

### Not every chip encodes every codec

`vainfo` lists profiles per codec. On Gemini Lake (J4125 and friends, the Synology/mini-PC chip),
H.264 can have an encode entrypoint where HEVC does not. The default
`output.codec_policy: prefer_hardware` can choose H.264 for SDR output. With `strict`, an
unavailable hardware codec falls back to software. HDR does not substitute H.264.

## Configuration

```yaml
hardware:
  enabled: true
  encoder_preset: "balanced"   # fast | balanced | quality
```

QSV is picked automatically when no NVIDIA GPU is present and the QSV encoders work.

## In Docker

Pass the render device through **and** add the group that owns it. The container runs as uid 1000,
and `/dev/dri/renderD128` is typically `root:render` with `crw-rw----` (no world access), so
without `group_add` the device is visible and unopenable. Group *names* are resolved inside the
container, where `render` does not exist, so use the host's numeric GID:

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

## Good for headless servers

QSV can move encoding off the CPU without a discrete card. Titles select their backend
separately; Vulkan support is probed rather than assumed from Quick Sync availability.

## Quality

The configured CRF uses the common libx265 reference scale. QSV receives a calibrated
`-global_quality`: reference CRF 18 maps to 20, and CRF 24 maps to 22. The mapping currently
borrows the VAAPI measurements; there is no separate QSV quality sweep. See
[the overview](./overview.md#quality-one-dial-calibrated-per-encoder).

## Title rendering

Title rendering uses Quadrants on a supported GPU or CPU backend, with PIL as the fallback.
See [Title kernels](./cpu-only.md#title-kernels) for platform support and limitations.
