---
sidebar_position: 4
title: Hardware acceleration
---

# Hardware acceleration

A GPU buys three things here: animated title screens, faster encoding, and on Apple Silicon face
detection on the Neural Engine. Every one of them has a CPU fallback, so a machine without a card
still cuts the film.

What a media accelerator does not buy is the models. NVENC, Quick Sync and VAAPI decode, scale and
encode; they do not run inference. The reader and the caption server are separate services with
their own hardware ([Running modes](./running-modes.md)). An NVIDIA card can run both the encoder
and the detectors, but the detectors go through CUDA in the
[inference service](./installation/inference-service.md), not through NVENC.

`immich-memories hardware` prints what was found and which backend would be selected. Run it first.

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

The card moved the encode by **1.77x**, the whole assembly by **1.65x**, and the render by **15 %**.
It moved the download by nothing, and the download was half the wall clock. A machine that cannot
open NVENC renders about 15 % slower; it does not refuse the film, and the log names the cause.

One caveat on that pair: the two cells ran a patch release apart (0.96.0 and 0.97.0) because the
first CPU-encode attempt failed and was retried. Same node, same requests, same cache state, and the
download and music phases came out within 6 % of each other.

The 17 September matrix ran the pair again on `0.102.0` and got 231 s of render against 316 s, with
no phase split because the NVENC cell's copy-out broke. That pair is worse controlled, not better:
the NVENC Job asked for 1 CPU and the CPU-encode Job for 2, so the card finished 85 s sooner on half
the CPU request. The table above is still the one to quote.

Two things off the render move more than the encoder does:

**The classifiers.** The ONNX encoder, its eight context heads and both detectors open on whatever
provider ONNX Runtime has. Put them on a card behind the
[inference service](./installation/inference-service.md) and preparation changes shape: on the
fixture month the same cluster pod paid **0.1863 s a picture** to a GPU-backed service, measured on
17 September 2026, against **0.6083 s** to a CPU-backed one, measured on 12 September and not run
again since. Over a real year of 13,544 pictures the GPU-backed service ran at 0.2509 s a picture,
88 % of a 1 h 05 min preparation.

**The title kernels.** They need a CPU with AVX when there is no card, and the Celeron J-series in a
typical NAS has none. That is not a speed question but a "this machine cannot run them at all"
question: see [CPUs without AVX](#cpus-without-avx).

## Backends

| Backend | Platform | Encode | Decode | GPU scaling | Face detection |
|---|---|---|---|---|---|
| NVIDIA NVENC | Linux (Windows untested) | h264_nvenc, hevc_nvenc | NVDEC | scale_cuda | CPU (YuNet) |
| Apple VideoToolbox | macOS | h264_videotoolbox, hevc_videotoolbox | VideoToolbox | none | Vision framework (Neural Engine) |
| Intel QSV | Linux (Windows untested) | h264_qsv, hevc_qsv | QSV | scale_qsv | CPU (YuNet) |
| AMD VAAPI | Linux | h264_vaapi, hevc_vaapi | VAAPI | scale_vaapi | CPU (YuNet) |
| Software | everywhere | libx264, libx265 | FFmpeg | swscale | CPU (YuNet) |

Backends are probed in the order NVIDIA, Apple, Intel QSV, VAAPI, and the first one whose one-frame
test encode succeeds is used. An FFmpeg that merely lists a backend (Debian's does, inside the image
too) does not send a GPU-less box down the hardware path.

```yaml
hardware:
  enabled: true                # false = software encoding, no GPU probing
  encoder_preset: "balanced"   # fast | balanced | quality
  gpu_decode: true
```

`hardware.enabled: false` forces software encoding. `hardware.backend` names one backend to probe
instead of walking the list, which is there for a benchmark rather than for running: detection
treats software as no backend at all, so a box missing a driver capability would encode on the CPU
and still look like a GPU run. Named, the miss is a warning in the log.

A backend is chosen per codec, not once. Intel Gemini Lake has an H.264 encode entrypoint and no
HEVC one, so it encodes H.264 on the GPU and H.265 in libx265, and the log says which half went
where. `output.codec_policy: prefer_hardware` (the default) goes one step further and uses the codec
the machine can actually encode, logging the substitution; `strict` always honours `output.codec`
and accepts the CPU cost. The substitution never applies to ProRes or to an HDR output, because
H.264 carries no HDR.

### NVIDIA

A GTX 1050 or newer has NVENC. You need the CUDA drivers and an FFmpeg built with NVENC support,
which most distro packages include.

The trap is that requesting the GPU does not grant the encoder. `--gpus all` and
`nvidia.com/gpu: 1` both grant compute and utility only, and without the `video` capability the
NVENC library is simply absent: `nvidia-smi` lists the card, the title kernels log
`on the CUDA backend`, and the probe fails with what looks like a bad flag.

```
Hardware encoder probe failed for ['-c:v', 'h264_nvenc']: Terminating thread with return code -22 (Invalid argument)
No hardware acceleration detected, using software encoding
```

Set `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` on the container, with
`runtimeClassName: nvidia` in Kubernetes or `capabilities: [gpu, video]` in the compose device
reservation. `kubectl apply -k overlays/gpu` sets both; the usual way to hit this is a hand-written
Job dropped onto a shared GPU node, which inherits the node's default `compute,utility`.
[Linux + NVIDIA](./common-setups/linux-nvidia.md) has a full compose file.

The other NVIDIA failure happens at render time rather than at detection: an FFmpeg built against a
newer NVENC SDK than the driver provides refuses to open the encoder. A third-party build against
SDK 13.1 fails on a 570 driver with "The minimum required Nvidia driver for nvenc is 610.00 or
newer". This project's own image works on 570. The one-frame probe catches it and falls back to
software.

The image installs the CPU build of PyTorch on purpose, on both architectures, so it does not do
CUDA inference. The detectors are ONNX graphs and want no torch at all, and the CUDA wheels are pure
weight: on arm64 they cost 3.3 GB of `nvidia` libraries plus 818 MB of triton, and the CUDA torch
still reports `cuda_available: False` inside the container. To run the ONNX seats on CUDA, either
install `immich-memories[editorial-cuda]` on the host (it replaces `editorial` rather than joining
it, and pins ONNX Runtime GPU to the 1.26 series for CUDA 12 and cuDNN 9) or use the `-cuda` variant
of the [inference service](./installation/inference-service.md).

### Apple Silicon

An M-series Mac is the best platform for this tool, and the only one anyone has run end to end on a
single machine. VideoToolbox takes the encode, the title kernels run on Metal, face detection runs
on the Neural Engine through the Vision framework, and unified memory means the reader's weights and
the render share one pool.

```bash
uv tool install "immich-memories[all-mac]"
```

`all-mac` is the extra that can actually cut. The `mac` extra on its own is the pyobjc bindings
(Quartz, Metal, Vision) and nothing else, so a `mac`-only install stops at the heads stage on the
first cut.

Every chip from M1 on is supported and nothing needs selecting. The models are the constraint, not
the chip: the graded reader's weights are around 17 GB resident, so a single-machine Mac wants
32 GB. An 8 or 16 GB M1 is an app host that needs a second box for the models. Standing the reader
and the caption server up is [steps 3 and 4 of the self-hosting guide](./self-hosting.md#3-serve-the-reader).

### Intel Quick Sync and AMD VAAPI

Quick Sync is in most Intel CPUs with integrated graphics (6th gen Skylake and newer), so an Intel
NUC, a mini PC or a repurposed desktop probably has it. VAAPI is the same story for a Radeon card on
Linux. Neither exposes a face-detection path, so the face-aware pan and the portrait crops stay on
CPU OpenCV.

Both need a VA-API driver: `intel-media-va-driver` (or `-non-free` for newer chips) plus `libmfx` or
`libvpl` for Intel, `mesa-va-drivers` for AMD. The amd64 Docker image installs all of them from
`0.77.1` on. Images older than that shipped FFmpeg with the backends compiled in and no driver at
all, so `vaInitialize` failed with `-542398533` and every run silently encoded in software. Upgrade
if you are on one.

`vainfo` shows what the driver found, per codec, which is the thing to read before believing
anything else. A working setup names the iHD, i965 or Mesa driver and lists `VAEntrypointEncSlice`
or `VAEntrypointEncSliceLP` profiles.

In Docker, pass the render device through **and** add the group that owns it. The container runs as
uid 1000 and `/dev/dri/renderD128` is typically `root:render` with `crw-rw----`, so `devices:` alone
leaves the device visible and unopenable. Group names resolve inside the container, where `render`
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
speed from the encoder preset, overriding `hardware.encoder_preset`. `medium` and `low` still load
and resolve to `balanced` and `fast`.

| | `high` | `balanced` |
|---|---|---|
| `libx265` (reference) | CRF 18 | CRF 24 |
| `libx264` | CRF 18 | CRF 22 |
| `h264_vaapi` and `h264_qsv` | QP 20 | QP 22 |
| `h264_nvenc` | QP 20 | QP 24 |
| `hevc_videotoolbox` | `-q:v` 65 | `-q:v` 55 |

Hardware buys speed, not quality per byte. At matched SSIM on the same clip:

| Backend | Bits for the same picture |
|---|---|
| NVENC (T1000, Turing) | 1.2x libx264 |
| VAAPI (J4125, Gemini Lake) | 2.2x libx264 |
| VideoToolbox (Apple Silicon) | 2.9x libx265 |

On an M-series Mac, libx265 `-preset medium` runs at 2.6x realtime against VideoToolbox's 8.2x, so
the card is usually still worth taking. QSV has no sweep of its own and carries VAAPI's anchors,
because the same iHD driver on the same silicon drives both; the VAAPI sweep itself was taken on an
Intel J4125, so the bit cost on an actual AMD card is not something anyone here has measured. The
clip, hardware and method behind every anchor are in
`src/immich_memories/processing/rate_control.py`. If a backend bands on your content, raise
`quality` to `high` and open an issue: the anchor should move.

## Title kernels

The animated title renderer (bokeh particles, gradient animation, SDF text) runs on the GPU through
[Quadrants](https://github.com/Genesis-Embodied-AI/quadrants), which installs with the app. Nothing
to configure. One line at the start of a render says what is drawing:

```
Title kernels: quadrants 1.3.0 on the Metal backend
```

Metal on Apple Silicon, CUDA on an NVIDIA card, Vulkan on an integrated GPU, and a CPU backend
everywhere else. `IMMICH_FORCE_CPU=1` keeps the GPU renderer but forces it onto the processor, for a
broken driver or a timing comparison.

Where there is no wheel there are no kernels. Quadrants 1.3.0 publishes none for Intel macOS and
none for Python 3.14, and it ships no source distribution, so those two get the PIL renderer rather
than a long build:

| Platform | Wheels |
| --- | --- |
| Linux x86_64 (manylinux 2.27+) | Python 3.10 to 3.13 |
| Linux aarch64 (manylinux 2.27+) | Python 3.10 to 3.13 |
| macOS arm64 (macOS 13+) | Python 3.10 to 3.13 |
| Windows AMD64 | Python 3.10 to 3.13 |
| **macOS x86_64 (Intel)** | **none** |
| **Python 3.14 and later** | **none** |

This app needs Python 3.11 or later, so the usable range is **3.11 to 3.13**.

The PIL renderer draws the same title text with the same timing, and still animates a gradient
behind it unless `animated_background` is off (`preset: fast` turns it off on every renderer). What
it loses is the kernel effects and the SDF text path. `immich-memories preflight` says which
renderer a machine will use before you start a long run.

### CPUs without AVX

The CPU backend needs AVX. On a processor without it (Celeron J-series such as the J4125 in a
Synology DS423+, and older Atom) the kernel library dies with SIGILL, exit 132, and takes the
interpreter with it. On the J4125 that happens as the library loads, so `python -c "import
quadrants"` is already the crash.

A child process does the loading and the first kernel dispatch, and the app never touches the
library until that child has come back alive. A machine that fails renders static titles through PIL
and says so up front:

```
Title rendering       WARNING   kernel backend crashed on this CPU: illegal
                                instruction; titles fall back to the PIL renderer
```

Losing the kernels costs effects, not time. PIL draws a content-backed title in 4.4 s against 19.8 s
for the kernel renderer on a CPU backend, so on a box with no AVX it is both the only renderer and
the faster one.

## Without a GPU

Preparation, selection, assembly and encoding all run without a card. What you give up is the kernel
effects and the hardware encoder, not a step of the pipeline.

| Feature | With GPU | Without GPU |
|---------|----------|-------------|
| Title screens | kernel effects: bokeh particles, SDF text, the animated deblur of a content-backed card | PIL: the same animated gradient, text and timing, without the effects |
| Video encoding | NVENC / VideoToolbox / VAAPI / QSV | libx264 / libx265, about 15 % slower overall |
| Video scaling | scale_cuda, scale_vaapi, scale_qsv | FFmpeg swscale |

Clip discovery, the eight context heads and two detectors, burst collapsing, audio ducking, assembly
and the whole UI run identically. Title cost scales with title duration and resolution, not with
clip count: a 12-clip memory and a 40-clip memory pay nearly the same title bill.

What a CPU-only box cannot hold is the vision reader, roughly 17 GB resident, and not in this
container. On a cheap VPS that means a second machine, not a slower first one. Read
[one machine or two](./self-hosting.md#one-machine-or-two) before sizing anything. The caption server
is separate: set `editorial.preparation.tier: no_captions` and this box prepares every producer the
audience gate reads without one. On four Celeron cores that is about 4 h for ten thousand pictures
instead of four days.

End-to-end numbers for a Mac, a Synology DS423+ and a Kubernetes cluster are on
[Running modes](./running-modes.md#what-to-expect-on-a-first-run). The CPU-only row to read is the
NAS: 1,573 s to render a 60-second film on four Celeron cores, against 114 s for the same length on
the Mac, and that Mac render had another project's run beside it, so read it as a ceiling. There is no card on that box to put the heads and detectors on, and every producer banks
its answer, so a second cut over the same period skips them entirely. If the classifiers are the
bill, the way out is not a faster CPU but the
[inference service](./installation/inference-service.md) on a box that has one.
