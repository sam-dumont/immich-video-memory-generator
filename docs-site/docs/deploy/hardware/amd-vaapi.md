---
sidebar_position: 5
title: AMD VAAPI
---

# AMD VAAPI

VAAPI is the encode path for a Radeon card on Linux: `h264_vaapi` and `hevc_vaapi`, `scale_vaapi`
for the resizes, and face detection back on the CPU because AMD exposes nothing for it. It is the
last backend probed, so it gets picked when NVIDIA, Apple and QSV are all absent.

## Drivers

Mesa's VA driver (`mesa-va-drivers` on Debian/Ubuntu, `libva-mesa-driver` on Arch) and an FFmpeg
built with VAAPI. The amd64 Docker image has both, from `0.77.1` on. Images older than that shipped
FFmpeg with VAAPI compiled in and no VA-API driver at all, so `vaInitialize` failed with
`-542398533` and every run silently encoded in software. If you are on one, upgrade.

`vainfo` lists the profiles and entrypoints your GPU actually has, which is the thing to read
before you believe anything else. A device that encodes H.264 but has no HEVC encode entrypoint
sends the H.265 half to libx265 and says so in the log.

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

Then `docker compose exec immich-memories vainfo` confirms the driver loaded.

## Quality

VAAPI takes the configured CRF as `-rc_mode CQP -qp`. `-qp` on its own is ignored unless CQP is
selected, which is why this needs both flags; before 0.77.1 neither was emitted and the driver's
default decided quality. It is not a fixed offset: two measured anchors are pinned per encoder
family and the dial interpolates between them, and VAAPI's pair is QP 20 at reference CRF 18 and
QP 22 at CRF 24 (`processing/rate_control.py`).

Reaching software quality costs roughly 2.2x the bits, see
[the measured table](./overview.md#quality-one-dial-calibrated-per-encoder). That sweep was taken
through the iHD driver on an Intel J4125, not on an AMD card: the flags and the interpolation are
the same on Mesa, the bit cost on your GPU is not something anyone here has measured.

## Title rendering

The kernel library tries CUDA first and gets Vulkan here. What it does when neither works is on
[Title kernels](./cpu-only.md#title-kernels).
