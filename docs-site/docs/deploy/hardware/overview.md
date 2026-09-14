---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

A GPU can accelerate title rendering and video encoding. Preparation and the reader have their
own device choices: Quick Sync, VAAPI and NVENC do not run model inference. The optional
[inference service](../installation/inference-service.md) has a CUDA image; reader and caption
servers run separately. See [Running modes](../running-modes.md) before sizing those services.

## Backends

| Backend | Platform | Encode | Decode | GPU scaling |
|---|---|---|---|---|
| NVIDIA NVENC | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda |
| Apple VideoToolbox | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | none |
| Intel QSV | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv |
| AMD VAAPI | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi |
| Software | everywhere | libx264, libx265 | FFmpeg | swscale |

The backend is probed in the order NVIDIA, Apple, Intel QSV, VAAPI, and the first one whose
one-frame test encode succeeds is used. An FFmpeg that merely lists a backend (Debian's does,
inside the image too) does not send a GPU-less box down the hardware path. There is no switch to
pick a backend; `hardware.enabled: false` forces software video encoding. Title kernels and model servers
select their devices separately; it does not disable every GPU probe.

```yaml
hardware:
  enabled: true                # false = software video encoding
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true
```

A backend is chosen per codec, not once. Intel Gemini Lake has an H.264 encode entrypoint and no
HEVC one, so it encodes H.264 on the GPU and H.265 in libx265, and the log says which half went
where. `output.codec_policy: prefer_hardware` (the default) goes one step further and uses the codec
the machine can actually encode, logging the substitution; `strict` always honours `output.codec`
and accepts the CPU cost. The substitution never applies to ProRes or to an HDR output (H.264
carries no HDR).

`immich-memories hardware` prints what was found and which backend would be selected. Run it first.

## VAAPI and QSV in Docker

Two things must be true: a VA-API driver in the image (the amd64 image ships
`intel-media-va-driver`, `i965-va-driver`, `mesa-va-drivers` and `vainfo`), and the render device
passed through with permission to open it. `devices:` alone is not enough, because the container
runs as uid 1000 and `/dev/dri/renderD128` is group-only; see the per-backend pages for the
`group_add` line.

## Quality: one dial, calibrated per encoder

`output.quality` (or an explicit `output.crf`) is on libx265's CRF scale, the reference because
libx265 is on every machine. Encoder families map that scale to their own quality controls. The anchors below were measured
on one 1080p60 clip; QSV borrows the VAAPI anchors rather than a separate sweep. These are
constant-quality controls for H.264/H.265. ProRes uses its profile instead.

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | about 35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | about 12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | about 12 MB, encoded as fast as the backend can |

There is no tier below `balanced`: the obvious candidate bands on sky and skin, and a preset that
visibly breaks a gradient is not worth a few megabytes. `fast` keeps the balanced picture and buys
speed from the encoder preset (setting `quality: fast` overrides `hardware.encoder_preset`).

| | `high` | `balanced` |
|---|---|---|
| `libx265` (reference) | CRF 18 | CRF 24 |
| `libx264` | CRF 18 | CRF 22 |
| `h264_vaapi` | QP 20 | QP 22 |
| `h264_nvenc` | QP 20 | QP 24 |
| `hevc_videotoolbox` | `-q:v` 65 | `-q:v` 55 |

What hardware encoding costs at matched SSIM, measured on the same clip:

| Backend | Bits for the same picture |
|---|---|
| NVENC (T1000, Turing) | 1.2× libx264 |
| VAAPI (J4125, Gemini Lake) | 2.2× libx264 |
| VideoToolbox (Apple Silicon) | 2.9× libx265 |

Hardware buys speed, not quality per byte. On an M-series Mac, libx265 `-preset medium` runs at
2.6× realtime against VideoToolbox's 8.2×, so it is usually still worth taking. The clip, hardware
and method behind every anchor are in `src/immich_memories/processing/rate_control.py`. If a
backend bands on your content, raise `quality` to `high` and open an issue: the anchor should move.

## NVIDIA: two things that fail after detection looks fine

`--gpus all` grants compute and utility only; without the `video` capability the NVENC library is
absent. Set `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` (in Kubernetes alongside
`runtimeClassName: nvidia`). And an FFmpeg built against a newer NVENC SDK than your driver
provides refuses to open the encoder; this project's image is fine on a 570 driver. Either way the
one-frame probe catches it and the run falls back to software, naming the cause.

## Per-backend pages

- [NVIDIA](./nvidia.md): NVENC/NVDEC, CUDA scaling
- [Apple Silicon](./apple-silicon.md): VideoToolbox, the Vision framework
- [Intel Quick Sync](./intel-qsv.md)
- [AMD VAAPI](./amd-vaapi.md)
- [CPU-only](./cpu-only.md): running without any GPU

## Title rendering

Title rendering uses Quadrants on a supported GPU or CPU backend, with PIL as the fallback.
See [Title kernels](./cpu-only.md#title-kernels) for platform support and limitations.
