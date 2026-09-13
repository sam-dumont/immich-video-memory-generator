---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

A GPU buys three things here: animated title screens, faster encoding, and (on Apple Silicon) face detection on the Neural Engine. **Every feature has a CPU fallback**, so it works on any machine. See [CPU-Only Mode](./cpu-only.md) for details on running without a GPU.

What a media accelerator does **not** buy is the editor's models. NVENC, Quick Sync and VAAPI decode, scale and encode; they do not run inference. The reader (~17 GB resident) and the caption server (1-2 GB) are separate services with their own hardware needs, see the [self-hosting guide](../self-hosting.md#one-machine-or-two). An NVIDIA card can run the encoder and the classifiers, but through CUDA and in their own container: the [inference service](../installation/inference-service.md).

Encoding video in software (libx264) works everywhere but it's slow. A hardware encoder is faster; how much faster depends on your card, codec and preset, and this project has not measured it. The pipeline auto-detects your hardware and picks the best available backend; NVENC, Quick Sync and VAAPI are only selected after a one-frame test encode succeeds, so an FFmpeg build that merely lists them (Debian's does, including inside the Docker image) doesn't send a GPU-less box down the hardware path.

The encode is not where a run spends its time, though. Analysis and title rendering are: measured at `--cpus=2`, title rendering was ~263 s of a ~339 s assembly ([CPU-Only Mode](./cpu-only.md#title-rendering-is-the-bottleneck-not-encoding)), and in a measured end-to-end run analysis was 7.4 of 10.1 minutes ([NAS-Only](../common-setups/nas-only.md#performance-expectations)). Hardware acceleration shortens the last phase; a GPU earns its keep first on titles.

## Supported backends

| Backend | Platform | Encode | Decode | GPU Scaling | Face Detection |
|---------|----------|--------|--------|-------------|----------------|
| **NVIDIA NVENC** | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (OpenCV Haar cascades) |
| **Apple VideoToolbox** | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | - | Vision Framework (Neural Engine) |
| **Intel QSV** | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (OpenCV Haar cascades) |
| **AMD VAAPI** | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (OpenCV Haar cascades) |
| **Software** | Everywhere | libx264, libx265 | FFmpeg | swscale | CPU (OpenCV Haar cascades) |

Face detection (for smart crops) runs on the GPU only on Apple Silicon (Vision Framework).
Everywhere else it is OpenCV Haar cascades on the CPU.

## Configuration

```yaml
hardware:
  enabled: true                # false = software encoding, no GPU probing
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true             # hardware decoding when the backend supports it
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

`output.quality` (or an explicit `output.crf`) is one dial, and the number is on **libx265's CRF
scale**: the reference, because libx265 is the only encoder present on every machine. Every other
family is calibrated to reproduce *that picture*, measured by SSIM on real 1080p60 film, rather than
to copy that integer. Before 0.76.1 the three hardware backends got **no rate-control flag at all**
and the driver's default decided quality, while VideoToolbox got a mapping never checked against an
output.

Constant-quality modes throughout, never bitrate targets, so a still frame and a fast pan each cost
what they need.

### The preset ladder

Two quality points, measured on 1080p60 film and (for `balanced`) judged by eye on gradients:

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | ~35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | ~12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | ~12 MB, encoded as fast as the backend can |

`high` used to mean CRF 12. SSIM is already past 0.999 by CRF 18, so CRF 12 bought nothing visible
while asking VideoToolbox for 76 Mbps, which is where 645 MB two-minute exports came from. A
memory film is watched, not archived for remastering.

There is deliberately **no tier below balanced**. The obvious candidate, around 0.980, bands on
gradients on real content, and a preset that visibly breaks up a sky is not worth shipping to save
a few megabytes. `fast` therefore keeps the balanced picture and buys its speed from the encoder
effort preset instead, which is what the name promises and the only thing it can honestly trade.
Setting `quality: fast` overrides `hardware.encoder_preset`.

`medium` and `low` are retired names that still load, resolving to `balanced` and `fast`.

### What each preset asks of each encoder

The same picture needs a different number on every scale, so each family is pinned by two measured
anchors rather than a shared offset: the five slopes are +1.00, +0.67, +0.33, +0.67 and -1.67 per
reference CRF step:

| | `high` | `balanced` |
|---|---|---|
| `libx265` (the reference) | CRF 18 | CRF 24 |
| `libx264` | CRF 18 | CRF 22 |
| `h264_vaapi` | QP 20 | QP 22 |
| `h264_nvenc` | QP 20 | QP 24 |
| `hevc_videotoolbox` | `-q:v` 65 | `-q:v` 55 |

### What hardware encoding actually costs

Every backend was measured against software on the same clip, at matched SSIM:

| Backend | Matching software costs | Tax |
|---|---|---|
| **NVENC** (T1000, Turing) | 8.5 Mbps vs libx264's 7.0 | **1.2x** |
| **VAAPI** (J4125, Gemini Lake) | 15.3 Mbps vs libx264's 7.0 | **2.2x** |
| **VideoToolbox** (Apple Silicon) | 13.2 Mbps vs libx265's 4.6 | **2.9x** |

Hardware buys speed, not quality per byte, and how much it costs varies a lot by chip. Turing's
NVENC is nearly free; Apple's costs roughly three times the bits for the same picture. It is still
usually worth taking: on an M-series Mac, libx265 `-preset medium` runs at 2.6x realtime against
VideoToolbox's 8.2x.

Full tables, with the clip, hardware and method behind every anchor, are in
`src/immich_memories/processing/rate_control.py`.

A note on method: SSIM fixes the rough level but barely punishes **banding**, and banding on sky,
walls and skin is the first thing a viewer notices. The balanced anchor was therefore confirmed by
eye on gradients, not by the score alone. If a backend bands on your content, raise `quality` to
`high`, and say so, because the anchor should move.

## When your hardware cannot encode the codec you asked for

A backend is chosen per codec, not once. Intel Gemini Lake advertises an H.264 encode entrypoint
and no HEVC one at all, so asking it for H.265 means the CPU encodes the whole film.

`output.codec_policy: prefer_hardware` (the default) uses the codec the machine can actually
encode, and says so in the log and the run record:

```
This device has no hardware h265 encoder but does have h264; encoding h264
instead of giving the whole film to the CPU (set output.codec_policy: strict to keep h265)
```

The result is a bigger file that plays on more things, produced far faster. Set
`output.codec_policy: strict` to always honour `output.codec` and accept the CPU cost. The
substitution never applies to ProRes, and never to an HDR output: H.264 carries no HDR, so trading
the codec there would trade away the dynamic range with it.

## NVIDIA: two things that fail after detection looks fine

**The `video` driver capability is not granted by default.** `--gpus all` grants compute and
utility only, and without `video` the NVENC library is simply absent. In Docker set
`NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`; in Kubernetes set the same environment variable
alongside `runtimeClassName: nvidia` and the GPU limit.

**An image can be built against a newer NVENC SDK than your driver provides.** A third-party FFmpeg
built against SDK 13.1 refuses to open the encoder on a 570 driver (which provides 13.0), reporting
that it needs driver 610 or newer. This project's own image is fine on 570. Either way the one-frame
probe catches it and the run falls back to software, naming the cause rather than just "could not
open encoder".

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
