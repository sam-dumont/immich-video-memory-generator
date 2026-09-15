---
sidebar_position: 4
title: Intel Quick Sync
---

# Intel Quick Sync

Quick Sync is built into most Intel CPUs with integrated graphics (6th gen Skylake and newer), so
an Intel NUC, a mini PC or a repurposed desktop probably has it. You get `h264_qsv` and `hevc_qsv`
plus `scale_qsv` for the resizes; Quick Sync exposes no face-detection path, so the face-aware pan
on photos and the portrait crops stay on CPU OpenCV. Taking the encode off the cores is the larger
half of a CPU-only assembly again, now that the title blur is fixed:
[the measured split](./cpu-only.md#title-rendering-used-to-be-the-bottleneck).

## Drivers

On Linux, `intel-media-va-driver` (or `intel-media-va-driver-non-free` for newer chips) and either
`libmfx` or `libvpl`. The amd64 Docker image installs them, from `0.77.1` on. Images older than
that ship FFmpeg with QSV compiled in and no VA-API driver at all, so `vaInitialize` fails with
`-542398533` and every run silently encodes in software. Upgrade if you are on one.

`vainfo` shows what the driver found. A working Intel setup names the iHD or i965 driver and lists
`VAEntrypointEncSlice` / `VAEntrypointEncSliceLP` profiles:

```bash
docker compose exec immich-memories vainfo
```

### Not every chip encodes every codec

`vainfo` lists profiles per codec. On Gemini Lake (J4125 and friends, the Synology and mini-PC
chip), H.264 has an encode entrypoint and HEVC does not, while the project's default output codec
is H.265. That combination is handled: the backend encodes what it can and the rest goes to
libx265, with `vaapi cannot encode h265 on this device; encoding it in software` in the log.
Set `output.codec: h264` if you want the whole run on the GPU.

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

## Quality

QSV takes the configured CRF as `-global_quality` on the same 0-51 quantiser scale, in ICQ mode,
never a bitrate target. Before 0.77.1 it got no rate-control flag at all and the driver's default
decided quality.

QSV is the one family with no sweep of its own: `rate_control.py` carries VAAPI's two anchors over
(QP 20 at reference CRF 18, QP 22 at CRF 24), because the same iHD driver on the same silicon
drives both. See [the overview](./overview.md#quality-one-dial-calibrated-per-encoder) for what the
dial costs on each backend, and note that a hardware encoder needs more bits than libx264 for the
same picture.

## Title rendering

The kernel library installs with the app and tries the Vulkan backend on the integrated GPU. What
it does when that fails is on [Title kernels](./cpu-only.md#title-kernels).
