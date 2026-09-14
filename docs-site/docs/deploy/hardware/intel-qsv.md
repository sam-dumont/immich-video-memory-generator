---
sidebar_position: 4
title: Intel Quick Sync
---

# Intel Quick Sync

Intel Quick Sync Video (QSV) is built into most Intel CPUs with integrated graphics (6th gen Skylake and newer). If you're running on an Intel NUC, a mini PC, or a server with an Intel CPU, you probably have it.

## What you get

- **QSV encoding**: h264_qsv, hevc_qsv. Hardware-accelerated encoding on the integrated GPU.
- **QSV scaling**: `scale_qsv` resizes frames on the GPU.
- **Face detection**: not a thing this app does. Photo pans use Immich's own face boxes.

## Requirements

- Intel CPU with integrated graphics (6th gen+)
- Intel media drivers installed
- FFmpeg built with QSV support

On Linux, you'll need the `intel-media-va-driver` (or `intel-media-va-driver-non-free` for newer chips) and `libmfx` or `libvpl`.

**The Docker image installs these** (amd64 only), and that fix is not in a release yet. Every
published image up to and including 0.76.1, the newest tag, ships FFmpeg with QSV compiled in and
no VA-API driver at all, so `vaInitialize` fails with `-542398533` and every run silently encodes
in software. Build from `main`, or wait for the next release.

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
H.264 has an encode entrypoint and HEVC does not, while the project's default output codec is
H.265. That combination is handled: the backend encodes what it can and the rest goes to
libx265, with `vaapi cannot encode h265 on this device; encoding it in software` in the log.
Set `output.codec: h264` if you want the whole run on the GPU.

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

QSV is common in home server setups: Intel NUCs, older desktops repurposed as media servers. It takes the encode off the CPU cores without needing a discrete graphics card. That is the larger half of a CPU-only assembly again, now that the title blur has been fixed: see [CPU-Only Mode](./cpu-only.md#title-rendering-used-to-be-the-bottleneck) for the measured split. Titles are the other half, and the kernel library installs with the app and tries the Vulkan backend on the integrated GPU.

## Quality

QSV takes the configured CRF as `-global_quality` on the same 0-51 quantiser scale, in ICQ mode,
never a bitrate target. Before 0.76.1 it got no rate-control flag at all and the driver's default
decided quality. See [the overview](./overview.md#quality-one-dial-calibrated-per-encoder) for what
the dial costs on each backend, and note that a hardware encoder needs more bits than libx264 for
the same picture.

## Title rendering

GPU title rendering runs on Quadrants, which has wheels for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 on Python 3.11-3.13. On macOS x86_64 and on Python 3.14 there is none, and title screens fall back to the PIL renderer (static gradient and text, no animated kernels, no SDF text); `immich-memories preflight` says which you will get. See [Title kernels](./cpu-only.md#title-kernels).
