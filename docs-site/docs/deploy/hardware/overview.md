---
sidebar_position: 1
title: Hardware Acceleration Overview
---

# Hardware Acceleration Overview

A GPU buys three things here: animated title screens, faster encoding, and on Apple Silicon face
detection on the Neural Engine. Every feature has a CPU fallback. What a media accelerator does
not buy is the editor's models: NVENC, Quick Sync and VAAPI decode, scale and encode, they do not
run inference. The reader and the caption server are separate services with their own hardware
([Running modes](../running-modes.md)); an NVIDIA card can run the encoder and the detectors, but
through CUDA in the [inference service](../installation/inference-service.md).

The encode is not where a run spends its time. Downloading the originals from Immich is, and no
card touches that. The measured split is below.

## What the card is actually worth

One controlled comparison: the same cluster node, the same cut, the same 15 clips, GPU title
kernels on both sides, and only the video encoder changed.

| Phase | `h264_nvenc` | `libx264` |
|---|---:|---:|
| Download the originals | 129.9 s (59 %) | 123.2 s (48 %) |
| Assembly | 71.5 s (33 %) | 117.9 s (46 %) |
| Music | 12.1 s (6 %) | 11.9 s (5 %) |
| **Pipeline** | **218.6 s** | **257.8 s** |
| of the assembly: the encode alone | 53.8 s | 95.1 s |
| of the assembly: title and ending screens | 10.4 s | 17.4 s |

So the card moved the encode by **1.77x**, the whole assembly by **1.65x**, and the render by
**15 %**. It moved the download by nothing, and the download was half the wall clock.

One caveat on that pair: the two cells ran a patch release apart (0.96.0 and 0.97.0) because the
first CPU-encode attempt failed and was retried. Same node, same requests, same cache state, and
the download and music phases came out within 6 % of each other, which is what you would expect of
two runs that differ only in the encoder.

A machine that cannot open NVENC therefore renders about 15 % slower. It does not refuse the film,
and the log names the cause.

## What a GPU is worth off the render

Two other things move more than the encoder does:

**The classifiers.** The encoder, its six context heads and both detectors are ONNX sessions, and
they open on whatever provider ONNX Runtime has. Put them on a card behind the
[inference service](../installation/inference-service.md) and preparation changes shape: on the
fixture month the same cluster pod paid **0.6083 s a picture** to a CPU-backed service and
**0.1957 s** to a GPU-backed one. On a real 13,552-picture month the GPU-backed service ran at
0.2445 s a picture, 87 % of a 64-minute preparation. A pod fast enough to compute its own facts in
process managed 0.2555 s, which is quicker than the CPU-backed service and slower than the
GPU-backed one.

**The title kernels.** They need a CPU with AVX when there is no card. The Celeron J-series in a
typical Synology has none, the kernel library dies with SIGILL as it loads, and every title falls
back to the PIL renderer: same text, same timing, an animated gradient still, no bokeh particles,
no SDF text. That is not a speed question, it is a "this machine cannot run them at all" question.
See [CPUs without AVX](./cpu-only.md#cpus-without-avx).

What no GPU buys you is the editor's reader. That is a separate service with its own hardware,
usually 17 GB of weights: [Running modes](../running-modes.md) and [Readers](../readers.md).

## Backends

| Backend | Platform | Encode | Decode | GPU scaling | Face detection |
|---|---|---|---|---|---|
| NVIDIA NVENC | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (YuNet) |
| Apple VideoToolbox | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | none | Vision framework (Neural Engine) |
| Intel QSV | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (YuNet) |
| AMD VAAPI | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (YuNet) |
| Software | everywhere | libx264, libx265 | FFmpeg | swscale | CPU (YuNet) |

The backend is probed in the order NVIDIA, Apple, Intel QSV, VAAPI, and the first one whose
one-frame test encode succeeds is used. An FFmpeg that merely lists a backend (Debian's does,
inside the image too) does not send a GPU-less box down the hardware path. `hardware.enabled: false`
forces software encoding, and `hardware.backend` names one backend to probe instead of walking the
list, which is there for a benchmark rather than for running ([NVIDIA](./nvidia.md#configuration)).

```yaml
hardware:
  enabled: true                # false = software encoding, no GPU probing
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
libx265 is on every machine. Every other encoder is calibrated to reproduce that picture, measured
by SSIM on real 1080p60 film. Constant-quality modes throughout, never bitrate targets.

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | about 35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | about 12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | about 12 MB, encoded as fast as the backend can |

There is no tier below `balanced`: the obvious candidate bands on sky and skin, and a preset that
visibly breaks a gradient is not worth a few megabytes. `fast` keeps the balanced picture and buys
speed from the encoder preset (setting `quality: fast` overrides `hardware.encoder_preset`).
`medium` and `low` still load and resolve to `balanced` and `fast`.

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

The kernels also need a wheel, and Quadrants publishes none for macOS x86_64 or for Python 3.14.
Those two fall back to the PIL renderer the same way an AVX-less CPU does.
`immich-memories preflight` says which you will get; [Title kernels](./cpu-only.md#title-kernels)
has the platform table.
