---
sidebar_position: 4
title: Hardware encoding
---

# Hardware encoding

Reader: power user.

It works on a plain NAS: preparation, selection, assembly and encoding all run on a CPU, a four-core
Celeron included. A GPU makes it better in two ways: a faster encode, and the effects in the animated
title screens. It does not run the models: NVENC, Quick Sync and VAAPI decode, scale and encode,
nothing else. Putting the heads and detectors on a card is the
[inference service](../better/inference.md), a separate add-on.

`immich-memories hardware` prints what it found and which backend it would pick. Run it first.

## Without a GPU

| Feature | With a GPU | Without |
|---------|----------|-------------|
| Title screens | kernel effects: bokeh particles, SDF text, the animated deblur of a content-backed card | PIL: same text, timing and animated gradient, without the effects |
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265 |
| Video scaling | scale_cuda, scale_vaapi, scale_qsv | FFmpeg swscale |

Everything else runs the same: clip discovery, the eight context heads and two detectors, burst
collapsing, audio ducking, assembly and the whole UI. Title cost scales with title length and
resolution, not clip count, so a 12-clip film and a 40-clip film pay about the same title bill.

## What the card is actually worth

Faster encodes, bigger files for the same picture (table below). On a real render the encode is
one phase among several, and downloading the originals from Immich often takes longer than it, so
a box without a hardware encoder renders somewhat slower and never refuses a film. The measured
numbers are on [Measured](../better/measured.md).

## Backends

| Backend | Platform | Encode | Decode | GPU scaling | Face detection |
|---|---|---|---|---|---|
| NVIDIA NVENC | Linux | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (YuNet) |
| Apple VideoToolbox | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | none | Vision framework (Neural Engine) |
| Intel QSV | Linux | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (YuNet) |
| AMD VAAPI | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (YuNet) |
| Software | everywhere | libx264, libx265 | FFmpeg | swscale | CPU (YuNet) |

The app probes NVIDIA, Apple, Intel QSV, then VAAPI, and takes the first one whose one-frame test
encode succeeds. An FFmpeg that only lists a backend (Debian's does, inside the image too) does
not send a GPU-less box down the hardware path.

```yaml
advanced:
  hardware:
    enabled: true                # false = software encoding, no GPU probing
    encoder_preset: "balanced"   # fast | balanced | quality
    gpu_decode: true
```

`hardware.backend` names one backend to probe instead of walking the list; a miss is then a warning
in the log. The choice is per codec: Intel Gemini Lake has an H.264 encoder and no HEVC one, so it
encodes H.264 on the GPU and H.265 in libx265, and the log says which went where.
`output.codec_policy: prefer_hardware` (the default) goes one step further and uses the codec the
machine can encode, logging the swap; `strict` always honours `output.codec`. The swap never
applies to ProRes or to an HDR output, because H.264 carries no HDR.

## Prerequisites

### NVIDIA

A GTX 1050 or newer, the NVIDIA drivers, and the container toolkit. The trap: asking for the GPU
does not grant the encoder. `--gpus all` and `nvidia.com/gpu: 1` grant compute and utility only;
without the `video` capability the NVENC library is absent, `nvidia-smi` still lists the card, and
the probe fails with what looks like a bad flag:

```
Hardware encoder probe failed for ['-c:v', 'h264_nvenc']: Terminating thread with return code -22 (Invalid argument)
No hardware acceleration detected, using software encoding
```

Set `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` on the container, with
`runtimeClassName: nvidia` in Kubernetes or `capabilities: [gpu, video]` in the compose device
reservation. `kubectl apply -k overlays/gpu` sets both.

An FFmpeg built against a newer NVENC SDK than your driver refuses to open the encoder at render
time. This project's image works on the 570 driver; the one-frame probe catches a mismatch and
falls back to software.

The image ships the CPU build of PyTorch on purpose: the detectors are ONNX graphs and need no
torch. To run the ONNX seats on CUDA, use the `-cuda` [inference service](../better/inference.md),
or install `immich-memories[editorial-cuda]` on the host in place of `editorial`.

### Apple Silicon

Nothing to set up. VideoToolbox takes the encode, the title kernels run on Metal, and face
detection runs on the Neural Engine through Vision. Install the `all-mac` extra: `mac` alone is the
pyobjc bindings and nothing else, so a `mac`-only install stops at the heads on the first cut.

```bash
uv tool install "immich-memories[all-mac]"
```

### Intel Quick Sync and AMD VAAPI

Quick Sync is in most Intel CPUs with integrated graphics (6th gen and newer), so a NUC, a mini PC
or a J-series NAS probably has it. VAAPI is the same for a Radeon card on Linux. The amd64 image
ships the Intel and Mesa VA-API drivers and `vainfo`; on a bare host install
`intel-media-va-driver` (or `-non-free`) or `mesa-va-drivers`.

In Docker, pass the render device through **and** add the group that owns it. The container runs as
uid 1000 and `/dev/dri/renderD128` is usually `root:render` with `crw-rw----`, so `devices:` alone
leaves the device visible and unopenable. The group name `render` doesn't exist inside the
container, so use the host's numeric GID:

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

## Verify it

```bash
docker compose exec immich-memories immich-memories hardware
docker compose exec immich-memories vainfo     # Intel / AMD only
```

`hardware` names the backend it would use. `vainfo` should name the iHD, i965 or Mesa driver and
list `VAEntrypointEncSlice` or `VAEntrypointEncSliceLP`. During a render, the log names the encoder
per codec; `nvidia-smi` or `intel_gpu_top` shows the card busy.

Nothing to redo after enabling a backend: prepared facts don't depend on the encoder, so the next
render encodes faster.

## Quality: one dial, calibrated per encoder

`output.quality` (or an explicit `output.crf`) is on libx265's CRF scale, the reference because
libx265 runs everywhere. Every other encoder is calibrated to the same picture, by SSIM on real
1080p60 footage, in constant-quality modes, never bitrate targets.

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | about 35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | about 12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | about 12 MB, encoded as fast as the backend can |

There is no tier below `balanced`: the next step down bands on sky and skin. `fast` keeps the
balanced picture and buys speed from the encoder preset.

| | `high` | `balanced` |
|---|---|---|
| `libx265` (reference) | CRF 18 | CRF 24 |
| `libx264` | CRF 18 | CRF 22 |
| `h264_vaapi` and `h264_qsv` | QP 20 | QP 22 |
| `h264_nvenc` | QP 20 | QP 24 |
| `hevc_videotoolbox` | `-q:v` 65 | `-q:v` 55 |

Hardware buys speed, not quality per byte. Bits for the same picture:

| Backend | Bits for the same picture |
|---|---|
| NVENC (T1000, Turing) | 1.2x libx264 |
| VAAPI (J4125, Gemini Lake) | 2.2x libx264 |
| VideoToolbox (Apple Silicon) | 2.9x libx265 |

QSV carries VAAPI's anchors (same iHD driver, same silicon); nobody here has measured an AMD card.
The clip, hardware and method behind each anchor are in
`src/immich_memories/processing/rate_control.py`. If a backend bands on your footage, set
`quality: high` and open an issue.

## Title kernels

The animated title renderer (bokeh, gradients, SDF text) runs through
[Quadrants](https://github.com/Genesis-Embodied-AI/quadrants), which installs with the app. One log
line says what is drawing:

```
Title kernels: quadrants 1.3.0 on the Metal backend
```

Metal on Apple Silicon, CUDA on NVIDIA, Vulkan on an integrated GPU, CPU everywhere else. Each GPU
backend is tried in a throwaway child process first; one that fails is skipped and named in the
log once. Kernels are cached in `~/.immich-memories/cache/kernels`. `IMMICH_FORCE_CPU=1` keeps the
kernel renderer but runs it on the processor.

Quadrants publishes wheels for Python 3.10 to 3.13 on Linux (x86_64, aarch64), macOS arm64 and
Windows, and none for Intel macOS or Python 3.14. This app needs 3.11 or later, so the kernels need
**3.11 to 3.13**. Everywhere else titles draw through PIL: same text and timing, no kernel effects.
`immich-memories preflight` says which renderer a machine will use.

### CPUs without AVX

The CPU kernel backend needs AVX. Celeron J-series (the J4125 in a Synology DS423+) and older Atoms
don't have it, and the kernel library dies with SIGILL as it loads. The app loads it in a child
process first, so such a box renders titles through PIL and says so up front:

```
Title rendering       WARNING   kernel backend crashed on this CPU: illegal
                                instruction; titles fall back to the PIL renderer
```

On that box PIL is also the faster renderer, so nothing is lost but the effects.
