---
sidebar_position: 5
title: AMD VAAPI
---

# AMD VAAPI

VAAPI (Video Acceleration API) provides hardware-accelerated video encoding on AMD GPUs under Linux. If you have a Radeon GPU and you're on Linux, this is your backend.

## What you get

- **VAAPI encoding**: h264_vaapi, hevc_vaapi. Hardware-accelerated encoding.
- **VAAPI scaling**: `scale_vaapi` resizes frames on the GPU.
- **Face detection**: falls back to CPU (OpenCV Haar cascades). AMD doesn't expose a GPU-accelerated face detection path.

## Requirements

- AMD GPU with VAAPI support
- Linux (VAAPI is Linux-only)
- Mesa VA drivers installed (`mesa-va-drivers` on Debian/Ubuntu, `libva-mesa-driver` on Arch)
- FFmpeg built with VAAPI support

**In the Docker image the drivers are already installed** (amd64 only). Images up to 0.76.1
shipped FFmpeg with VAAPI compiled in but no VA-API driver at all, so `vaInitialize` failed with
`-542398533` and every run silently encoded in software. If you are on an older image, upgrade.

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

VAAPI takes the configured CRF as `-rc_mode CQP -qp`, offset by the measured +2 that makes
`crf: 18` land on the quality libx264 gives at CRF 18. `-qp` on its own is ignored unless CQP is
selected, which is why this needs both flags. Before 0.76.1 neither was emitted and the driver's
default decided quality.

Reaching software quality costs roughly 2.2x the bits — see
[the measured table](./overview.md#what-hardware-encoding-actually-costs).
