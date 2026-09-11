---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

A GPU buys three things here: animated title screens, faster encoding, and (on Apple Silicon) face detection on the Neural Engine. **Every feature has a CPU fallback**, so it works on any machine. See [CPU-Only Mode](./cpu-only.md) for details on running without a GPU.

Encoding video in software (libx264) works everywhere but it's slow. A hardware encoder is faster; how much faster depends on your card, codec and preset, and this project has not measured it. The pipeline auto-detects your hardware and picks the best available backend; NVENC, Quick Sync and VAAPI are only selected after a one-frame test encode succeeds, so an FFmpeg build that merely lists them (Debian's does, including inside the Docker image) doesn't send a GPU-less box down the hardware path.

The encode is not where a run spends its time, though. Analysis and title rendering are — measured at `--cpus=2`, title rendering was ~263 s of a ~339 s assembly ([CPU-Only Mode](./cpu-only.md#title-rendering-is-the-bottleneck-not-encoding)), and in a measured end-to-end run analysis was 7.4 of 10.1 minutes ([NAS-Only](../common-setups/nas-only.md#performance-expectations)). Hardware acceleration shortens the last phase; a GPU earns its keep first on titles.

## Supported backends

| Backend | Platform | Encode | Decode | GPU Scaling | Face Detection |
|---------|----------|--------|--------|-------------|----------------|
| **NVIDIA NVENC** | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (OpenCV Haar cascades) |
| **Apple VideoToolbox** | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | - | Vision Framework (Neural Engine) |
| **Intel QSV** | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (OpenCV Haar cascades) |
| **AMD VAAPI** | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (OpenCV Haar cascades) |
| **Software** | Everywhere | libx264, libx265 | FFmpeg | swscale | CPU (OpenCV Haar cascades) |

Face detection runs on the GPU only on Apple Silicon (Vision Framework). Everywhere else it is
OpenCV Haar cascades on the CPU. On NVIDIA, CUDA is also used for scene analysis (frame
differencing) when OpenCV has CUDA support and `hardware.gpu_analysis` is on.

## Configuration

```yaml
hardware:
  enabled: true                # false = software encoding, no GPU probing
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true             # hardware decoding when the backend supports it
  gpu_analysis: true           # CUDA scene analysis on NVIDIA when available
```

The backend is probed automatically in the order NVIDIA → Apple → Intel QSV → VAAPI, and the first
one that works is used. There is no override to pick a specific backend; the only switch is
`hardware.enabled: false`, which forces software encoding (useful for testing or a broken driver).

A backend is chosen per codec, not once: a chip whose VA-API driver has an H.264 encode entrypoint
and no HEVC one (Intel Gemini Lake, for example) encodes H.264 on the GPU and H.265 in libx265,
and logs which half went where.

## VAAPI and QSV in Docker (fixed after 0.76.1)

Two things had to be true before either backend could work in a container, and until 0.76.1
neither was:

1. **A VA-API driver in the image.** FFmpeg lists `vaapi` and `qsv` under `-hwaccels` whenever it
   was compiled with them, which says nothing about whether libva can reach a GPU. The image
   shipped no `*_drv_video.so` at all, so `vaInitialize` failed with `-542398533` on every host.
   `intel-media-va-driver`, `i965-va-driver` and `mesa-va-drivers` now ship in the amd64 image,
   along with `vainfo` for diagnosis.
2. **The render device, and permission to open it.** See the per-backend pages: `devices:` alone
   is not enough, because the container runs as uid 1000 and `/dev/dri/renderD128` is group-only.

## Quality: what CRF means on each backend

`output.quality` (or an explicit `output.crf`) is one dial, but only libx264/libx265 take a CRF.
Each backend gets that dial translated into its own constant-quality control — VAAPI `-rc_mode CQP
-qp`, QSV `-global_quality`, NVENC `-rc constqp -qp`, VideoToolbox `-q:v`. Before 0.76.1 the three
hardware backends got **no rate-control flag at all** and the driver's default decided quality, and
VideoToolbox got a mapping that had never been checked against an output.

Constant-quality modes are used throughout rather than bitrate targets, so a still frame and a fast
pan each cost what they need.

### Hardware encoding is not free quality

Measured on a Synology J4125 (Gemini Lake), 20 s of 1080p60 film, SSIM against the same source,
with software on the same box as the reference:

| Encode | Size | SSIM |
|---|---|---|
| `libx264 -crf 18` | 17.4 MB | 0.99011 |
| `h264_vaapi -qp 20` | 38.2 MB | 0.98946 |
| `h264_vaapi -qp 22` | 19.8 MB | 0.98468 |
| `h264_vaapi -qp 24` | 14.7 MB | 0.98079 |
| `h264_vaapi -qp 26` | 10.4 MB | 0.97561 |

Matching CRF 18 quality costs QP 20 and about **2.2x the bits**. At equal file size VAAPI is
clearly worse than x264. That is the trade a hardware encoder makes: it buys speed, not quality per
byte. The CRF mapping is anchored on this table, so `crf: 18` asks VAAPI for QP 20.

VideoToolbox was the other way round — its old mapping sent CRF 18 to `-q:v 75`, which on the same
kind of source is 37.3 Mbps against libx265's 4.6 Mbps at CRF 18. That is where oversized exports
came from; the default `quality: high` is CRF 12, which the old line sent all the way to `-q:v 87`.

:::note NVENC is provisional
The NVENC offset has not been measured on real hardware yet — it starts at the VAAPI offset because
both use the same 0-51 quantiser scale. If you have an NVIDIA card, a sweep of
`h264_nvenc -rc constqp -qp {18,20,22,24}` against `libx264 -crf 18` on the same clip would replace
the assumption with a number.
:::

## Checking your hardware

```bash
immich-memories hardware
```

This prints what backends are available, which one would be selected, and the specific encoders/decoders found. Run this first if you're not sure what you've got.

## Per-backend details

- [NVIDIA](./nvidia.md): NVENC/NVDEC, CUDA scaling and scene analysis
- [Apple Silicon](./apple-silicon.md): VideoToolbox, Vision Framework, mlx-vlm
- [Intel Quick Sync](./intel-qsv.md): QSV encoding and scaling
- [AMD VAAPI](./amd-vaapi.md): VAAPI encoding and scaling (Linux only)
- [CPU-Only Mode](./cpu-only.md): Running without any GPU
